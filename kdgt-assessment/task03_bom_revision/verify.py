"""과제 03 자동 검증 (실제 HTTP 서버, 새 DB).

완료 기준 (슬라이드)
  1. 개정 전후 수량 재현   — 개정 A~D 전개 수량·총 길이가 수작업 정답과 일치, 비교 결과도 정답과 일치
  2. mm·m 변환 검증        — 7개 입력의 mm/m 환산값, 1.25m 표기 변경이 '변경'으로 잡히지 않음
  3. 과거 BOM 조회         — rev 지정, as_of 날짜 조회, D 등록 후에도 A 가 그대로
  4. 순환 오류 처리        — 순환·자기참조 포함 오류 입력 7종이 기대 코드로 거부되고 DB 는 그대로
추가: 같은 개정 재등록(멱등), 다른 내용 재등록 거부, 개정 수정 불가(트리거)
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from common.jsonapi import http_client
from . import bom
from .api import start_in_background

FIX = Path(__file__).with_name("fixtures")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def run(report_dir=None) -> tuple[list[Check], dict]:
    exp = _load("expected")
    checks: list[Check] = []
    art: dict = {"qty_rows": [], "diff_rows": [], "unit_rows": [], "invalid_rows": []}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t3.db"
        server, base = start_in_background(str(db))
        call = http_client(base)
        try:
            # 등록
            reg = {r: call("POST", "/api/revisions", _load(f"rev_{r}")) for r in "ABCD"}
            checks.append(Check("개정 A~D 등록", all(s == 201 for s, _ in reg.values()),
                                ", ".join(f"{r}:{s} 부품{b.get('counts', {}).get('PART')} 조립{b.get('counts', {}).get('ASSEMBLY')}"
                                          for r, (s, b) in reg.items())))

            # 1) 전개 수량·길이 재현
            bad = []
            for r in "ABCD":
                _, got = call("GET", f"/api/products/RX-500/bom?rev={r}&unit=mm")
                ex = {e["part_no"]: e for e in got["exploded"]}
                for pn, q in exp["exploded"][r].items():
                    g = ex.get(pn, {}).get("total_qty")
                    ok = g is not None and Decimal(g) == Decimal(q)
                    art["qty_rows"].append((r, pn, q, g, ok))
                    if not ok:
                        bad.append(f"{r}/{pn} 기대 {q} 실제 {g}")
                extra = set(ex) - set(exp["exploded"][r])
                if extra:
                    bad.append(f"{r} 정답에 없는 품목 {sorted(extra)}")
                for pn, l in exp["total_length_mm"][r].items():
                    g = ex.get(pn, {}).get("total_length")
                    ok = g is not None and Decimal(g) == Decimal(l)
                    art["qty_rows"].append((r, f"{pn} 총길이(mm)", l, g, ok))
                    if not ok:
                        bad.append(f"{r}/{pn} 길이 기대 {l} 실제 {g}")
            n = sum(len(v) for v in exp["exploded"].values()) + sum(len(v) for v in exp["total_length_mm"].values())
            checks.append(Check(f"개정별 전개 수량·총길이 {n}개 값 정답 일치", not bad, "; ".join(bad) or "전부 일치"))

            # 1-b) 개정 비교
            bad = []
            for key, e in exp["diffs"].items():
                a, b = key.split("->")
                _, d = call("GET", f"/api/products/RX-500/diff?from={a}&to={b}&unit=mm")
                lines = sorted([c["type"], c["parent"], c["child"]] for c in d["line_changes"])
                specs = sorted([c["part_no"], c["field"], c["before"], c["after"]] for c in d["spec_changes"])
                exq = {c["part_no"]: [c["qty_before"], c["qty_after"]] for c in d["exploded_changes"]}
                ok = (lines == sorted(e["line_changes"]) and specs == sorted(e["spec_changes"])
                      and exq == e["exploded_changes"])
                art["diff_rows"].append((key, d["summary"], ok))
                if not ok:
                    bad.append(f"{key}: 줄 {lines} / 규격 {specs} / 전개 {exq}")
            checks.append(Check("개정 비교 3건 (A→B 규격, B→C 추가, C→D 수량·삭제) 정답 일치", not bad,
                                "; ".join(bad) or "전부 일치"))

            # 2) 단위 변환
            bad = []
            for u in exp["unit_checks"]:
                errs = []
                mm = bom.to_mm(u["input"], "x", errs)
                got_mm, got_m = bom.canon(mm), bom.canon(bom.mm_to(mm, "m"))
                ok = not errs and got_mm == u["mm"] and got_m == u["m"]
                art["unit_rows"].append((u["input"], u["mm"], got_mm, u["m"], got_m, ok))
                if not ok:
                    bad.append(f"{u['input']} → {got_mm}mm/{got_m}m")
            _, d = call("GET", "/api/products/RX-500/diff?from=A&to=B")
            pipe_unchanged = not any(c["child"] == "PIPE-25A" for c in d["line_changes"])
            _, bm = call("GET", "/api/products/RX-500/bom?rev=D&unit=m")
            pipe_m = next(e for e in bm["exploded"] if e["part_no"] == "PIPE-25A")["total_length"]
            ok = not bad and pipe_unchanged and pipe_m == "7.5"
            checks.append(Check("mm·m 변환 (7개 입력, 1250mm↔1.25m 표기 차이 무시, D 배관 총 7.5m)", ok,
                                "; ".join(bad) or f"변환 일치, A→B 배관 변경 없음={pipe_unchanged}, D 배관 {pipe_m}m"))

            # 3) 과거 BOM 조회
            bad = []
            for day, rev in exp["as_of"].items():
                s, b = call("GET", f"/api/products/RX-500/bom?as_of={day}")
                got = b.get("revision") if s == 200 else None
                if got != rev:
                    bad.append(f"{day}: 기대 {rev} 실제 {got}({s})")
            s, a_now = call("GET", "/api/products/RX-500/bom?rev=A")
            a_ok = {e["part_no"]: e["total_qty"] for e in a_now["exploded"]} == exp["exploded"]["A"]
            checks.append(Check("과거 BOM 조회 (as_of 5개 날짜, D 등록 후 A 그대로)", not bad and a_ok,
                                "; ".join(bad) or f"as_of 일치, A 재조회 일치={a_ok}"))

            # 4) 오류 입력 거부 (순환 포함)
            before = call("GET", "/api/products/RX-500/revisions")[1]
            bad = []
            for name, code in exp["invalid"].items():
                s, b = call("POST", "/api/revisions", _load(f"invalid/{name}"))
                codes = [e["code"] for e in b.get("errors", [])]
                path = next((e.get("path") for e in b.get("errors", []) if e["code"] == "CYCLE"), None)
                ok = s == 422 and set(codes) == {code}   # 기대 코드 '만' 나와야 함 (부수 오류 금지)
                art["invalid_rows"].append((name, code, s, codes, path, ok))
                if not ok:
                    bad.append(f"{name}: {s} {codes}")
            after = call("GET", "/api/products/RX-500/revisions")[1]
            checks.append(Check(f"오류 입력 {len(exp['invalid'])}종 거부 (순환 2종 포함), DB 변화 없음",
                                not bad and before == after, "; ".join(bad) or "전부 기대 코드로 거부"))

            # 5) 멱등 / 덮어쓰기 거부 / 불변
            s1, _ = call("POST", "/api/revisions", _load("rev_B"))
            changed = _load("rev_B")
            changed["items"][4]["thickness"] = {"value": 10, "unit": "mm"}
            s2, b2 = call("POST", "/api/revisions", changed)
            conn = bom.connect(db)
            try:
                conn.execute("UPDATE rev_lines SET qty='99' WHERE rev_id=1")
                imm = False
            except sqlite3.DatabaseError as e:
                imm = "REVISION_IMMUTABLE" in str(e)
            conn.close()
            ok = s1 == 200 and s2 == 409 and b2.get("error") == "REVISION_EXISTS" and imm
            checks.append(Check("같은 개정 재등록=200, 다른 내용=409, 직접 UPDATE 차단", ok,
                                f"재등록 {s1}, 변경본 {s2} {b2.get('error')}, 트리거 차단 {imm}"))
        finally:
            server.shutdown()
            server.server_close()
    if report_dir:
        _report(Path(report_dir), checks, art)
    return checks, art


def _report(out: Path, checks, a):
    out.mkdir(parents=True, exist_ok=True)
    L = ["# 과제 03 자동 검증 결과", "",
         "- 방법: 빈 DB + 실제 HTTP 서버. 정답은 `fixtures/expected.json` (수작업 계산)",
         "- 재실행: `python -m task03_bom_revision.cli verify`", "",
         "## 판정", "", "| 결과 | 항목 | 근거 |", "|---|---|---|",
         *[f"| {'PASS' if c.ok else 'FAIL'} | {c.name} | {c.detail} |" for c in checks], ""]
    (out / "verification.md").write_text("\n".join(L), encoding="utf-8")

    Q = ["# 과제 03 수량 검증 결과", "",
         "개정별 다단계 전개 총수량(부모 수량 × 자식 수량의 곱을 경로별로 합산)을 수작업 정답과 대조.", "",
         "## 전개 수량", "", "| 개정 | 품번 | 정답 | 계산 | 판정 |", "|---|---|---:|---:|---|",
         *[f"| {r} | {p} | {e} | {g} | {'OK' if ok else 'NG'} |" for r, p, e, g, ok in a["qty_rows"]],
         "", "## 개정 비교 요약", "", "| 비교 | 추가 | 삭제 | 수량변경 | 길이변경 | 규격변경 | 정답 |",
         "|---|---:|---:|---:|---:|---:|---|",
         *[f"| {k} | {s['ADDED']} | {s['REMOVED']} | {s['QTY_CHANGED']} | {s['LENGTH_CHANGED']} | "
           f"{s['SPEC_CHANGED']} | {'OK' if ok else 'NG'} |" for k, s, ok in a["diff_rows"]],
         "", "## mm·m 변환", "", "| 입력 | mm 정답 | mm 계산 | m 정답 | m 계산 | 판정 |", "|---|---:|---:|---:|---:|---|",
         *[f"| {i['value']} {i['unit']} | {em} | {gm} | {e_m} | {g_m} | {'OK' if ok else 'NG'} |"
           for i, em, gm, e_m, g_m, ok in a["unit_rows"]],
         "", "## 오류 입력 (순환 참조 등)", "", "| 입력 | 기대 코드 | HTTP | 반환 코드 | 순환 경로 | 판정 |",
         "|---|---|---:|---|---|---|",
         *[f"| {n} | {c} | {s} | {', '.join(codes)} | {' → '.join(p) if p else ''} | {'OK' if ok else 'NG'} |"
           for n, c, s, codes, p, ok in a["invalid_rows"]], ""]
    (out / "quantity_verification.md").write_text("\n".join(Q), encoding="utf-8")
