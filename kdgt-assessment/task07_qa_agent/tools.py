"""Agent 도구: 읽기 전용 DB 조회, 절차 문서 검색.

읽기 전용은 프롬프트가 아니라 **코드로** 보장한다 (모델이 속아도 쓰기 불가).
  1) SQLite 를 mode=ro 로 연다
  2) authorizer 로 SELECT·READ·FUNCTION·RECURSIVE 외 모든 동작 거부 (DELETE/DROP/ATTACH/PRAGMA 등)
  3) 한 번에 한 문장만 (sqlite3 기본 동작), 결과 최대 100행, 실행 시간 제한
도구가 돌려준 근거 ref(receipts#1, SOP-02§1 …)는 Agent 가 기록해 두고, 답변의 인용이 그 안에 있는지 검사한다.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
REF_RE = re.compile(r"^(lots|receipts|inspections|lot_links|shipments)#\d+$")
MAX_ROWS = 100
ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


def build_db(path: Path) -> Path:
    """fixtures/data.sql 로 DB 파일을 새로 만든다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript((FIX / "data.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return path


def db_fingerprint(path: Path) -> str:
    """DB 내용 해시 — 평가 전후 비교로 '데이터가 바뀌지 않았음'을 확인."""
    conn = sqlite3.connect(path)
    h = hashlib.sha256()
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        h.update(name.encode())
        for row in conn.execute(f"SELECT * FROM {name} ORDER BY 1"):
            h.update(repr(row).encode())
    conn.close()
    return h.hexdigest()


class ReadOnlyDB:
    SCHEMA = (
        "lots(ref, lot_no, kind[MATERIAL|PRODUCT], item, project_id, created_at)\n"
        "receipts(ref, lot_no, item, qty, unit, supplier, received_at, mtc_no)  -- 원자재 입고\n"
        "inspections(ref, lot_no, seq, result[PASS|FAIL], inspector, inspected_at, note)  -- seq 가 클수록 최근(재검사)\n"
        "lot_links(ref, parent_lot, child_lot, link_type[SPLIT|MERGE|CONSUME], qty)  -- LOT 계보: 부모→자식\n"
        "shipments(ref, shipment_no, lot_no, qty, unit, customer, destination, shipped_at, note)  -- 제품 LOT 출하"
    )

    def __init__(self, path: Path):
        self.path = path

    def _connect(self):
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5)
        conn.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK if action in ALLOWED else sqlite3.SQLITE_DENY)
        deadline = time.monotonic() + 2.0
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        return conn

    def query(self, sql: str) -> dict:
        """결과: {ok, columns, rows, refs, truncated} 또는 {ok: False, error}"""
        sql = (sql or "").strip().rstrip(";")
        if not sql:
            return {"ok": False, "error": "빈 SQL"}
        conn = self._connect()
        try:
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(MAX_ROWS + 1)
        except sqlite3.DatabaseError as e:
            msg = str(e)
            if "not authorized" in msg or "prohibited" in msg or "readonly" in msg:
                msg = f"거부됨: 읽기 전용 도구입니다 (조회 SELECT 만 허용). 원문 오류: {msg}"
            elif "one statement" in msg:
                msg = f"거부됨: 한 번에 한 문장만 실행합니다. 원문 오류: {msg}"
            return {"ok": False, "error": msg}
        finally:
            conn.close()
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        refs = sorted({str(v) for r in rows for v in r if isinstance(v, str) and REF_RE.match(v)})
        return {"ok": True, "columns": cols, "rows": [list(r) for r in rows], "refs": refs, "truncated": truncated}

    def get_ref(self, ref: str) -> dict | None:
        if not REF_RE.match(ref):
            return None
        table, rid = ref.split("#")
        conn = self._connect()
        try:
            cur = conn.execute(f"SELECT * FROM {table} WHERE row_id = ?", (int(rid),))
            row = cur.fetchone()
            return dict(zip([d[0] for d in cur.description], row)) if row else None
        finally:
            conn.close()


class DocSearch:
    """절차 문서를 '## §n' 절 단위로 나눠 문자 2-gram 겹침으로 순위를 매긴다 (한국어 형태소 분석 없이도 동작)."""

    def __init__(self, doc_dir: Path = FIX / "docs"):
        self.sections = []
        for p in sorted(doc_dir.glob("*.md")):
            lines = p.read_text(encoding="utf-8").splitlines()
            doc_id = lines[0].lstrip("# ").split()[0]
            title = lines[0].lstrip("# ")
            cur = None
            for i, line in enumerate(lines, 1):
                m = re.match(r"^## (§\d+)\s*(.*)", line)
                if m:
                    cur = {"ref": f"{doc_id}{m.group(1)}", "doc": title, "heading": f"{m.group(1)} {m.group(2)}",
                           "file": p.name, "line_start": i, "line_end": i, "text": ""}
                    self.sections.append(cur)
                elif cur is not None:
                    cur["text"] += line + "\n"
                    cur["line_end"] = i

    @staticmethod
    def _grams(s: str) -> set[str]:
        s = re.sub(r"\s+", "", s.lower())
        return {s[i:i + 2] for i in range(len(s) - 1)}

    def search(self, query: str, k: int = 3) -> dict:
        q = self._grams(query or "")
        if not q:
            return {"ok": False, "error": "빈 검색어"}
        scored = []
        for s in self.sections:
            g = self._grams(s["doc"] + s["heading"] + s["text"])
            score = len(q & g) / len(q)
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: (-x[0], x[1]["ref"]))
        hits = [{"ref": s["ref"], "location": f"{s['file']} {s['heading']} (L{s['line_start']}-{s['line_end']})",
                 "score": round(sc, 3), "text": s["text"].strip()} for sc, s in scored[:max(1, min(k, 5))]]
        return {"ok": True, "hits": hits, "refs": [h["ref"] for h in hits]}

    def get_ref(self, ref: str) -> dict | None:
        return next(({k: v for k, v in s.items()} for s in self.sections if s["ref"] == ref), None)
