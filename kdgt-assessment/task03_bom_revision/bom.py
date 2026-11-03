"""과제 03 BOM 핵심 로직: 단위 변환, 검증(순환 참조 포함), 개정 등록, 전개, 개정 비교.

입력 = 도면 추출 결과 JSON (과제 02 의 출력 형식과 같다. 형식은 README 참고)

    {"product_no": "RX-500", "drawing_no": "DWG-RX500", "revision": "A",
     "effective_date": "2026-03-01", "note": "최초 출도",
     "items": [{"part_no": "PL-SHELL", "name": "쉘 판", "kind": "PART",
                "material": "SUS304", "thickness": {"value": 6, "unit": "mm"}}, ...],
     "lines": [{"parent": "SA-JACKET", "child": "PIPE-25A", "qty": 4,
                "length": {"value": 1250, "unit": "mm"}}, ...]}
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

# 길이 단위 → mm 배율. 그 밖의 단위(inch 등)는 추측하지 않고 거부한다.
LENGTH_UNITS = {"mm": Decimal(1), "cm": Decimal(10), "m": Decimal(1000)}


# ---------------------------------------------------------------- 오류
class BomError(Exception):
    http_status = 409

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def to_dict(self):
        return {"error": self.code, "message": self.message, **self.details}


class BomInvalid(BomError):
    """입력 BOM 자체의 오류 (여러 건을 한 번에 돌려준다)."""
    http_status = 422

    def __init__(self, errors: list[dict]):
        super().__init__("BOM_INVALID", f"BOM 검증 오류 {len(errors)}건", errors=errors)
        self.errors = errors


class NotFound(BomError):
    http_status = 404


# ---------------------------------------------------------------- 숫자/단위
def canon(d: Decimal) -> str:
    """Decimal → 정규 문자열. 1250.0 / 1.25E+3 → '1250', 1.250 → '1.25'"""
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "") else s


def to_decimal(value, where: str, errors: list) -> Decimal | None:
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            raise InvalidOperation
        return d
    except (InvalidOperation, ValueError):
        errors.append({"code": "NUMBER_INVALID", "where": where, "message": f"숫자가 아님: {value!r}"})
        return None


def to_mm(measure, where: str, errors: list) -> Decimal | None:
    """{"value": 1.25, "unit": "m"} → Decimal('1250') (mm)"""
    if not isinstance(measure, dict) or "value" not in measure or "unit" not in measure:
        errors.append({"code": "MEASURE_INVALID", "where": where,
                       "message": "{value, unit} 형식이어야 함"})
        return None
    unit = str(measure["unit"]).strip().lower()
    if unit not in LENGTH_UNITS:
        errors.append({"code": "UNIT_UNSUPPORTED", "where": where,
                       "message": f"지원하지 않는 단위 {measure['unit']!r} (허용: mm, cm, m)"})
        return None
    v = to_decimal(measure["value"], where, errors)
    if v is None:
        return None
    if v <= 0:
        errors.append({"code": "MEASURE_NOT_POSITIVE", "where": where, "message": f"0 이하: {v}"})
        return None
    return v * LENGTH_UNITS[unit]


def mm_to(value_mm: Decimal, unit: str) -> Decimal:
    return value_mm / LENGTH_UNITS[unit]


# ---------------------------------------------------------------- 검증
def find_cycle(edges: dict[str, list[str]]) -> list[str] | None:
    """방향 그래프에서 순환 하나를 찾아 경로로 돌려준다. 예: ['A', 'B', 'A']"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = defaultdict(int)
    stack: list[str] = []

    def dfs(n):
        color[n] = GREY
        stack.append(n)
        for c in edges.get(n, []):
            if color[c] == GREY:
                return stack[stack.index(c):] + [c]
            if color[c] == WHITE:
                found = dfs(c)
                if found:
                    return found
        stack.pop()
        color[n] = BLACK
        return None

    for n in list(edges):
        if color[n] == WHITE:
            found = dfs(n)
            if found:
                return found
    return None


def validate(doc: dict) -> dict:
    """추출 JSON 을 검증·정규화한다. 오류가 하나라도 있으면 BomInvalid(전체 목록)."""
    errors: list[dict] = []
    if not isinstance(doc, dict):
        raise BomInvalid([{"code": "DOC_INVALID", "where": "$", "message": "JSON 객체가 아님"}])

    for f in ("product_no", "drawing_no", "revision", "effective_date"):
        if not str(doc.get(f) or "").strip():
            errors.append({"code": "MISSING_FIELD", "where": f, "message": f"{f} 필수"})
    try:
        date.fromisoformat(str(doc.get("effective_date")))
    except ValueError:
        if doc.get("effective_date"):
            errors.append({"code": "DATE_INVALID", "where": "effective_date",
                           "message": f"YYYY-MM-DD 아님: {doc.get('effective_date')!r}"})

    items: dict[str, dict] = {}
    for i, it in enumerate(doc.get("items") or []):
        where = f"items[{i}]"
        pn = str(it.get("part_no") or "").strip()
        if not pn:
            errors.append({"code": "MISSING_FIELD", "where": where, "message": "part_no 필수"})
            continue
        if pn in items:
            errors.append({"code": "DUPLICATE_ITEM", "where": where, "message": f"품번 중복: {pn}"})
            continue
        kind = it.get("kind")
        if kind not in ("ASSEMBLY", "PART"):
            errors.append({"code": "KIND_INVALID", "where": where,
                           "message": f"{pn}: kind 는 ASSEMBLY/PART ({kind!r})"})
        if not str(it.get("name") or "").strip():
            errors.append({"code": "MISSING_FIELD", "where": where, "message": f"{pn}: name 필수"})
        th = to_mm(it["thickness"], f"{where}.thickness", errors) if it.get("thickness") is not None else None
        items[pn] = {"part_no": pn, "name": str(it.get("name") or "").strip(), "kind": kind,
                     "material": (str(it["material"]).strip() if it.get("material") else None),
                     "thickness_mm": canon(th) if th is not None else None}
    if not items:
        errors.append({"code": "MISSING_FIELD", "where": "items", "message": "items 가 비어 있음"})

    lines: list[dict] = []
    seen_pairs: dict[tuple, int] = {}
    edges: dict[str, list[str]] = defaultdict(list)
    for i, ln in enumerate(doc.get("lines") or []):
        where = f"lines[{i}]"
        p, c = str(ln.get("parent") or "").strip(), str(ln.get("child") or "").strip()
        ok = True
        for role, pn in (("parent", p), ("child", c)):
            if pn not in items:
                errors.append({"code": "UNKNOWN_PART", "where": f"{where}.{role}",
                               "message": f"items 에 없는 품번: {pn!r}"})
                ok = False
        if p and p == c:
            errors.append({"code": "CYCLE", "where": where, "message": f"자기 자신을 포함: {p}", "path": [p, p]})
            ok = False
        if (p, c) in seen_pairs:
            errors.append({"code": "DUPLICATE_LINE", "where": where,
                           "message": f"{p} → {c} 가 lines[{seen_pairs[(p, c)]}] 와 중복"})
            ok = False
        if p in items and items[p]["kind"] == "PART":
            errors.append({"code": "PART_HAS_CHILDREN", "where": where,
                           "message": f"{p} 는 PART 인데 하위 품목({c})이 있음"})
            ok = False
        qty = to_decimal(ln.get("qty"), f"{where}.qty", errors)
        if qty is not None and qty <= 0:
            errors.append({"code": "QTY_NOT_POSITIVE", "where": f"{where}.qty", "message": f"0 이하: {qty}"})
            qty = None
        length = to_mm(ln["length"], f"{where}.length", errors) if ln.get("length") is not None else None
        if not ok:
            continue
        # 구조(부모·자식)가 정상이면 수량이 틀려도 연결 관계에는 넣는다.
        # 빼버리면 그 자식이 '연결 안 된 품목(ORPHAN)'으로 잘못 보고된다 (AI_LOG 오류 #1)
        seen_pairs[(p, c)] = i
        edges[p].append(c)
        if qty is None:
            continue
        lines.append({"seq": i, "parent": p, "child": c, "qty": canon(qty),
                      "length_mm": canon(length) if length is not None else None})

    cycle = find_cycle(edges)
    if cycle:
        errors.append({"code": "CYCLE", "where": "lines",
                       "message": "순환 참조: " + " → ".join(cycle), "path": cycle})

    root = str(doc.get("product_no") or "").strip()
    if root and root not in items:
        errors.append({"code": "ROOT_MISSING", "where": "items", "message": f"최상위 품번 {root} 가 items 에 없음"})
    elif root and not cycle:
        if any(l["child"] == root for l in lines):
            errors.append({"code": "ROOT_HAS_PARENT", "where": "lines", "message": f"최상위 {root} 가 다른 품목의 하위에 있음"})
        reach, todo = {root}, [root]
        while todo:
            for c in edges.get(todo.pop(), []):
                if c not in reach:
                    reach.add(c)
                    todo.append(c)
        for pn in items:
            if pn not in reach:
                errors.append({"code": "ORPHAN_ITEM", "where": "items",
                               "message": f"{pn} 가 최상위 {root} 에서 연결되지 않음"})
        for pn, it in items.items():
            if it["kind"] == "ASSEMBLY" and not edges.get(pn):
                errors.append({"code": "EMPTY_ASSEMBLY", "where": "items",
                               "message": f"조립품 {pn} 에 하위 품목이 없음"})

    if errors:
        raise BomInvalid(errors)
    return {"product_no": root, "drawing_no": str(doc["drawing_no"]).strip(),
            "revision": str(doc["revision"]).strip(), "effective_date": str(doc["effective_date"]),
            "note": doc.get("note"), "items": items, "lines": lines}


# ---------------------------------------------------------------- 저장/조회
def connect(path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(conn) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


@contextmanager
def tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _hash(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def register(conn, doc: dict) -> dict:
    """개정 등록. 같은 개정·같은 내용 재등록 → 그대로 반환(created=False).
    같은 개정·다른 내용 → REVISION_EXISTS. 기존 최신 개정보다 이른 날짜 → REVISION_ORDER."""
    bom = validate(doc)
    # 정규화된 내용으로 해시: 1.25 m 와 1250 mm, 줄 순서만 다른 같은 BOM 은 같은 개정으로 본다
    h = _hash({**{k: v for k, v in bom.items() if k != "lines"},
               "lines": sorted(({k: v for k, v in l.items() if k != "seq"} for l in bom["lines"]),
                               key=lambda l: (l["parent"], l["child"]))})
    with tx(conn):
        prev = conn.execute("SELECT * FROM revisions WHERE product_no=? AND revision=?",
                            (bom["product_no"], bom["revision"])).fetchone()
        if prev:
            if prev["source_hash"] != h:
                raise BomError("REVISION_EXISTS",
                               f"{bom['product_no']} 개정 {bom['revision']} 은 이미 다른 내용으로 등록됨 "
                               "(개정은 수정 불가 — 새 개정으로 등록)", revision=bom["revision"])
            return {"created": False, **revision_summary(conn, prev["rev_id"])}
        last = conn.execute("SELECT revision, effective_date FROM revisions WHERE product_no=? "
                            "ORDER BY effective_date DESC LIMIT 1", (bom["product_no"],)).fetchone()
        if last and bom["effective_date"] <= last["effective_date"]:
            raise BomError("REVISION_ORDER",
                           f"유효일 {bom['effective_date']} 이 최신 개정 {last['revision']}"
                           f"({last['effective_date']}) 보다 늦어야 함", latest=last["revision"])
        rev_id = conn.execute(
            """INSERT INTO revisions (product_no, revision, drawing_no, effective_date, note,
                                      source_hash, source_json, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (bom["product_no"], bom["revision"], bom["drawing_no"], bom["effective_date"], bom["note"],
             h, json.dumps(doc, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(timespec="seconds"))).lastrowid
        conn.executemany(
            "INSERT INTO rev_items VALUES (?,?,?,?,?,?)",
            [(rev_id, i["part_no"], i["name"], i["kind"], i["material"], i["thickness_mm"])
             for i in bom["items"].values()])
        conn.executemany(
            "INSERT INTO rev_lines VALUES (?,?,?,?,?,?)",
            [(rev_id, l["seq"], l["parent"], l["child"], l["qty"], l["length_mm"]) for l in bom["lines"]])
    return {"created": True, **revision_summary(conn, rev_id)}


def revision_summary(conn, rev_id: int) -> dict:
    r = conn.execute("SELECT * FROM revisions WHERE rev_id=?", (rev_id,)).fetchone()
    n_items = conn.execute("SELECT kind, COUNT(*) FROM rev_items WHERE rev_id=? GROUP BY kind",
                           (rev_id,)).fetchall()
    return {"product_no": r["product_no"], "revision": r["revision"], "drawing_no": r["drawing_no"],
            "effective_date": r["effective_date"], "note": r["note"],
            "counts": {k: n for k, n in n_items},
            "lines": conn.execute("SELECT COUNT(*) FROM rev_lines WHERE rev_id=?", (rev_id,)).fetchone()[0]}


def list_products(conn) -> list[dict]:
    return [{"product_no": r[0], "revisions": r[1], "latest": r[2]} for r in conn.execute(
        """SELECT product_no, COUNT(*),
                  (SELECT revision FROM revisions r2 WHERE r2.product_no = r.product_no
                   ORDER BY effective_date DESC LIMIT 1)
           FROM revisions r GROUP BY product_no ORDER BY product_no""")]


def list_revisions(conn, product_no: str) -> list[dict]:
    rows = conn.execute("SELECT rev_id FROM revisions WHERE product_no=? ORDER BY effective_date",
                        (product_no,)).fetchall()
    if not rows:
        raise NotFound("PRODUCT_NOT_FOUND", f"제품 {product_no} 없음")
    return [revision_summary(conn, r[0]) for r in rows]


def resolve(conn, product_no: str, revision: str | None = None, as_of: str | None = None) -> int:
    """개정 지정 → 그 개정, 날짜 지정 → 그날 유효한 개정, 둘 다 없으면 최신."""
    if revision:
        r = conn.execute("SELECT rev_id FROM revisions WHERE product_no=? AND revision=?",
                         (product_no, revision)).fetchone()
        if not r:
            raise NotFound("REVISION_NOT_FOUND", f"{product_no} 개정 {revision} 없음")
        return r[0]
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError:
            raise BomError("DATE_INVALID", f"as_of 는 YYYY-MM-DD ({as_of!r})") from None
        r = conn.execute("SELECT rev_id FROM revisions WHERE product_no=? AND effective_date<=? "
                         "ORDER BY effective_date DESC LIMIT 1", (product_no, as_of)).fetchone()
        if not r:
            raise NotFound("REVISION_NOT_FOUND", f"{product_no}: {as_of} 시점에 유효한 개정 없음")
        return r[0]
    r = conn.execute("SELECT rev_id FROM revisions WHERE product_no=? ORDER BY effective_date DESC LIMIT 1",
                     (product_no,)).fetchone()
    if not r:
        raise NotFound("PRODUCT_NOT_FOUND", f"제품 {product_no} 없음")
    return r[0]


def _load(conn, rev_id):
    items = {r["part_no"]: dict(r) for r in conn.execute("SELECT * FROM rev_items WHERE rev_id=?", (rev_id,))}
    lines = [dict(r) for r in conn.execute("SELECT * FROM rev_lines WHERE rev_id=? ORDER BY seq", (rev_id,))]
    return items, lines


def _fmt_len(mm: str | None, unit: str):
    return None if mm is None else canon(mm_to(Decimal(mm), unit))


def explode(items: dict, lines: list[dict], root: str) -> dict[str, dict]:
    """다단계 전개: 품목별 총 수량 = 경로별 수량 곱의 합. 길이 품목은 총 길이(mm)도 계산."""
    children = defaultdict(list)
    for l in lines:
        children[l["parent"]].append(l)
    total: dict[str, dict] = {}

    def walk(node: str, mult: Decimal, depth: int):
        for l in children[node]:
            q = mult * Decimal(l["qty"])
            t = total.setdefault(l["child"], {"qty": Decimal(0), "length_mm": Decimal(0),
                                              "has_length": False, "min_depth": depth + 1})
            t["qty"] += q
            t["min_depth"] = min(t["min_depth"], depth + 1)
            if l["length_mm"] is not None:
                t["length_mm"] += q * Decimal(l["length_mm"])
                t["has_length"] = True
            walk(l["child"], q, depth + 1)

    walk(root, Decimal(1), 0)
    return total


def get_bom(conn, product_no: str, revision=None, as_of=None, unit: str = "mm") -> dict:
    if unit not in LENGTH_UNITS:
        raise BomError("UNIT_UNSUPPORTED", f"unit 은 mm/cm/m ({unit!r})")
    rev_id = resolve(conn, product_no, revision, as_of)
    items, lines = _load(conn, rev_id)
    ex = explode(items, lines, product_no)

    def tree(node, mult, path):
        out = []
        for l in [x for x in lines if x["parent"] == node]:
            it = items[l["child"]]
            q = mult * Decimal(l["qty"])
            out.append({"part_no": it["part_no"], "name": it["name"], "kind": it["kind"],
                        "material": it["material"], "thickness_mm": it["thickness_mm"],
                        "qty": l["qty"], "length": _fmt_len(l["length_mm"], unit), "total_qty": canon(q),
                        "children": tree(it["part_no"], q, path + [it["part_no"]])})
        return out

    exploded = []
    for pn, t in sorted(ex.items(), key=lambda kv: (items[kv[0]]["kind"] != "ASSEMBLY", kv[1]["min_depth"], kv[0])):
        it = items[pn]
        exploded.append({"part_no": pn, "name": it["name"], "kind": it["kind"], "material": it["material"],
                         "thickness_mm": it["thickness_mm"], "total_qty": canon(t["qty"]),
                         "total_length": canon(mm_to(t["length_mm"], unit)) if t["has_length"] else None})
    return {**revision_summary(conn, rev_id), "unit": unit, "root": product_no,
            "root_name": items[product_no]["name"], "tree": tree(product_no, Decimal(1), [product_no]),
            "exploded": exploded}


def diff(conn, product_no: str, rev_from: str, rev_to: str, unit: str = "mm") -> dict:
    """개정 비교: 구성(줄) 변경, 품목 규격 변경, 전개 총수량 변경."""
    if unit not in LENGTH_UNITS:
        raise BomError("UNIT_UNSUPPORTED", f"unit 은 mm/cm/m ({unit!r})")
    a_id, b_id = resolve(conn, product_no, rev_from), resolve(conn, product_no, rev_to)
    ai, al = _load(conn, a_id)
    bi, bl = _load(conn, b_id)
    amap = {(l["parent"], l["child"]): l for l in al}
    bmap = {(l["parent"], l["child"]): l for l in bl}

    line_changes = []
    for key in sorted(set(amap) | set(bmap), key=lambda k: (k not in amap, k)):
        a, b = amap.get(key), bmap.get(key)
        base = {"parent": key[0], "child": key[1],
                "name": (bi.get(key[1]) or ai.get(key[1]))["name"]}
        if a is None:
            line_changes.append({**base, "type": "ADDED", "qty_before": None, "qty_after": b["qty"],
                                 "length_after": _fmt_len(b["length_mm"], unit)})
        elif b is None:
            line_changes.append({**base, "type": "REMOVED", "qty_before": a["qty"], "qty_after": None,
                                 "length_before": _fmt_len(a["length_mm"], unit)})
        else:
            if Decimal(a["qty"]) != Decimal(b["qty"]):
                line_changes.append({**base, "type": "QTY_CHANGED", "qty_before": a["qty"], "qty_after": b["qty"]})
            if a["length_mm"] != b["length_mm"]:
                line_changes.append({**base, "type": "LENGTH_CHANGED",
                                     "length_before": _fmt_len(a["length_mm"], unit),
                                     "length_after": _fmt_len(b["length_mm"], unit)})

    spec_changes = []
    for pn in sorted(set(ai) & set(bi)):
        for field, label in (("name", "품명"), ("kind", "구분"), ("material", "재질"), ("thickness_mm", "두께")):
            if ai[pn][field] != bi[pn][field]:
                # 판 두께는 현장 관례대로 항상 mm 로 표시 (unit 은 배관·봉재 길이에만 적용)
                spec_changes.append({"part_no": pn, "name": bi[pn]["name"], "field": field.replace("_mm", ""),
                                     "label": label + (" (mm)" if field == "thickness_mm" else ""),
                                     "before": ai[pn][field], "after": bi[pn][field]})

    ea, eb = explode(ai, al, product_no), explode(bi, bl, product_no)
    qty_changes = []
    for pn in sorted(set(ea) | set(eb)):
        qa = ea.get(pn, {}).get("qty", Decimal(0))
        qb = eb.get(pn, {}).get("qty", Decimal(0))
        la, lb = ea.get(pn, {}).get("length_mm", Decimal(0)), eb.get(pn, {}).get("length_mm", Decimal(0))
        if qa != qb or la != lb:
            has_len = ea.get(pn, {}).get("has_length") or eb.get(pn, {}).get("has_length")
            qty_changes.append({
                "part_no": pn, "name": (bi.get(pn) or ai.get(pn))["name"],
                "qty_before": canon(qa), "qty_after": canon(qb), "qty_delta": canon(qb - qa),
                "length_before": canon(mm_to(la, unit)) if has_len else None,
                "length_after": canon(mm_to(lb, unit)) if has_len else None})

    return {"product_no": product_no, "unit": unit,
            "from": revision_summary(conn, a_id), "to": revision_summary(conn, b_id),
            "line_changes": line_changes, "spec_changes": spec_changes, "exploded_changes": qty_changes,
            "summary": {t: sum(1 for c in line_changes if c["type"] == t)
                        for t in ("ADDED", "REMOVED", "QTY_CHANGED", "LENGTH_CHANGED")}
                       | {"SPEC_CHANGED": len(spec_changes)}}
