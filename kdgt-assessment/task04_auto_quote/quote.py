"""과제 04 견적 엔진.

BOM(과제 03) 전개 수량 × 원가 기준정보(part_specs.json) × 견적일에 유효한 단가표 버전 → 견적.
계산 결과는 줄 단위 근거와 함께 저장되며 이후 수정되지 않는다(단가가 바뀌어도 기존 견적 유지).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from task03_bom_revision import bom as bomlib
from . import rules as R

HERE = Path(__file__).parent
SCHEMA = HERE / "schema.sql"
TABLES = ("MATERIAL", "LENGTH", "PURCHASE", "PROCESS", "OUTSOURCE")
CATEGORY_LABEL = {"MATERIAL": "재료비", "PROCESS": "가공비", "OUTSOURCE": "외주비"}
UNIT_LABEL = {"KG": "kg", "M": "m", "EA": "EA", "H": "h", "M2": "m²"}


class QuoteError(bomlib.BomError):
    """업무 오류 (http_status/to_dict 는 BomError 와 같은 형식)."""


class NotFound(QuoteError):
    http_status = 404


class BadRequest(QuoteError):
    http_status = 400


def connect(path):
    return bomlib.connect(path)


def init_db(conn):
    bomlib.init_db(conn)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def won(n: int | None) -> str:
    return "-" if n is None else f"{n:,}원"


def fmt(d: Decimal) -> str:
    """Decimal → 사람이 읽기 좋은 문자열 (천 단위 쉼표, 불필요한 0 제거)"""
    s = bomlib.canon(d)
    whole, _, frac = s.partition(".")
    whole = f"{int(whole):,}" if whole.lstrip("-").isdigit() else whole
    return whole + ("." + frac if frac else "")


# ---------------------------------------------------------------- 단가표 버전
def register_price_version(conn, doc: dict) -> dict:
    """단가표 1개 버전 등록. 같은 내용 재등록은 무시, 같은 라벨에 다른 내용은 거부."""
    t = doc.get("table")
    if t not in TABLES:
        raise BadRequest("TABLE_INVALID", f"table 은 {TABLES} 중 하나 ({t!r})")
    for f in ("label", "effective_from", "source"):
        if not doc.get(f):
            raise BadRequest("MISSING_FIELD", f"{f} 필수")
    try:
        date.fromisoformat(doc["effective_from"])
    except ValueError:
        raise BadRequest("DATE_INVALID", f"effective_from: {doc['effective_from']!r}") from None
    items = doc.get("items") or []
    seen = set()
    for it in items:
        if not it.get("key") or it["key"] in seen:
            raise BadRequest("ITEM_INVALID", f"키 누락 또는 중복: {it.get('key')!r}")
        seen.add(it["key"])
        try:
            p = Decimal(str(it["price"]))
        except (InvalidOperation, KeyError):
            raise BadRequest("PRICE_INVALID", f"{it['key']}: 단가가 숫자가 아님") from None
        if p <= 0:
            raise BadRequest("PRICE_INVALID", f"{it['key']}: 단가는 0보다 커야 함")
    h = _hash(doc)
    with bomlib.tx(conn):
        prev = conn.execute("SELECT * FROM price_versions WHERE table_type=? AND label=?",
                            (t, doc["label"])).fetchone()
        if prev:
            if prev["source_hash"] != h:
                raise QuoteError("PRICE_VERSION_EXISTS",
                                 f"{t} {doc['label']} 은 이미 다른 내용으로 등록됨 (새 버전으로 등록)")
            return {"created": False, "table": t, "label": doc["label"]}
        vid = conn.execute(
            "INSERT INTO price_versions (table_type, label, effective_from, source, source_hash, created_at) "
            "VALUES (?,?,?,?,?,?)", (t, doc["label"], doc["effective_from"], doc["source"], h, _now())).lastrowid
        conn.executemany("INSERT INTO price_items VALUES (?,?,?,?)",
                         [(vid, it["key"], it["unit"], bomlib.canon(Decimal(str(it["price"])))) for it in items])
    return {"created": True, "table": t, "label": doc["label"], "items": len(items)}


def list_price_versions(conn) -> list[dict]:
    return [dict(r) | {"items": conn.execute("SELECT COUNT(*) FROM price_items WHERE version_id=?",
                                             (r["version_id"],)).fetchone()[0]}
            for r in conn.execute("SELECT version_id, table_type, label, effective_from, source "
                                  "FROM price_versions ORDER BY table_type, effective_from")]


def _versions_at(conn, day: str) -> dict[str, dict | None]:
    out = {}
    for t in TABLES:
        r = conn.execute("SELECT * FROM price_versions WHERE table_type=? AND effective_from<=? "
                         "ORDER BY effective_from DESC LIMIT 1", (t, day)).fetchone()
        if r is None:
            out[t] = None
            continue
        prices = {p["key"]: (Decimal(p["price"]), p["unit"]) for p in
                  conn.execute("SELECT * FROM price_items WHERE version_id=?", (r["version_id"],))}
        out[t] = {"label": r["label"], "source": r["source"], "effective_from": r["effective_from"],
                  "prices": prices}
    return out


# ---------------------------------------------------------------- 계산
def _price(versions, table, key):
    """(단가, 단위, 버전라벨) 또는 (None, None, 사유)"""
    v = versions.get(table)
    if v is None:
        return None, None, f"{table} 단가표에 견적일 기준 유효한 버전이 없음"
    if key not in v["prices"]:
        return None, None, f"{table} 단가표 {v['label']} 에 '{key}' 단가 미등록"
    p, unit = v["prices"][key]
    return p, unit, v["label"]


def compute(bom: dict, specs: dict, versions: dict, qty: Decimal) -> list[dict]:
    """견적 줄 목록 계산 (DB 저장 없음). 각 줄에 계산식과 근거 포함."""
    lines: list[dict] = []
    parts, routing, outsource = specs["parts"], specs["routing"], specs["outsource"]

    def add(category, item, name, status="OK", **kw):
        lines.append({"category": category, "item": item, "item_name": name, "status": status,
                      "cost_key": None, "basis_qty": None, "basis_unit": None, "unit_price": None,
                      "price_table": None, "price_version": None, "amount": None, "review_reason": None,
                      "inputs": {}, **kw})

    exploded = [{"part_no": bom["root"], "name": bom["root_name"], "kind": "ASSEMBLY",
                 "total_qty": "1", "material": None, "thickness_mm": None, "total_length": None}] + bom["exploded"]

    # 1) 재료비 ---------------------------------------------------------
    for e in exploded:
        if e["kind"] != "PART":
            continue
        pn, name = e["part_no"], e["name"]
        n = Decimal(e["total_qty"]) * qty
        spec = parts.get(pn)
        if spec is None:
            add("MATERIAL", pn, name, "REVIEW", formula="원가 기준정보 없음",
                review_reason=f"{pn}: part_specs 에 원가 기준정보(형상·치수)가 없음")
            continue
        shape = spec["shape"]
        if shape == "PLATE":
            mat, t = e["material"], e["thickness_mm"]
            if not mat or not t or mat not in R.DENSITY:
                add("MATERIAL", pn, name, "REVIEW", formula="중량 계산 불가",
                    review_reason=f"{pn}: 재질({mat})·두께({t}) 누락 또는 비중 미등록")
                continue
            L, W, T, rho = Decimal(spec["length_mm"]), Decimal(spec["width_mm"]), Decimal(t), R.DENSITY[mat]
            unit_w = R.round_weight(L * W * T * rho / Decimal(1_000_000))
            y = R.YIELD["PLATE"]
            price, unit, ver = _price(versions, "MATERIAL", mat)
            weight_desc = f"{fmt(unit_w)}kg(={fmt(L)}×{fmt(W)}×{fmt(T)}mm×비중 {fmt(rho)}÷1,000,000)"
            if price is None:
                add("MATERIAL", pn, name, "REVIEW", cost_key=mat, basis_qty=bomlib.canon(n * unit_w),
                    basis_unit="kg", formula=f"{fmt(n)}EA × {weight_desc} ÷ 수율 {fmt(y)} × (단가 없음)",
                    review_reason=ver)
                continue
            amt = R.round_won(n * unit_w / y * price)
            add("MATERIAL", pn, name, cost_key=mat, basis_qty=bomlib.canon(n * unit_w), basis_unit="kg",
                unit_price=bomlib.canon(price), price_table="MATERIAL", price_version=ver, amount=amt,
                formula=f"{fmt(n)}EA × {weight_desc} ÷ 수율 {fmt(y)} × {fmt(price)}원/kg = {amt:,}원",
                inputs={"qty": str(n), "length_mm": str(L), "width_mm": str(W), "thickness_mm": str(T),
                        "density": str(rho), "unit_weight_kg": str(unit_w), "yield": str(y)})
        elif shape == "LENGTH":
            mat = e["material"]
            if not e["total_length"] or not mat:
                add("MATERIAL", pn, name, "REVIEW", formula="길이 계산 불가",
                    review_reason=f"{pn}: BOM 에 길이 또는 재질이 없음")
                continue
            key = f"{spec['size']}/{mat}"
            meters = Decimal(e["total_length"]) / Decimal(1000) * qty
            y = R.YIELD["LENGTH"]
            price, unit, ver = _price(versions, "LENGTH", key)
            if price is None:
                add("MATERIAL", pn, name, "REVIEW", cost_key=key, basis_qty=bomlib.canon(meters), basis_unit="m",
                    formula=f"{fmt(meters)}m ÷ 수율 {fmt(y)} × (단가 없음)", review_reason=ver)
                continue
            amt = R.round_won(meters / y * price)
            add("MATERIAL", pn, name, cost_key=key, basis_qty=bomlib.canon(meters), basis_unit="m",
                unit_price=bomlib.canon(price), price_table="LENGTH", price_version=ver, amount=amt,
                formula=f"{fmt(meters)}m(총 {fmt(n)}본) ÷ 수율 {fmt(y)} × {fmt(price)}원/m = {amt:,}원",
                inputs={"meters": str(meters), "yield": str(y)})
        elif shape == "PURCHASED":
            price, unit, ver = _price(versions, "PURCHASE", pn)
            if price is None:
                add("MATERIAL", pn, name, "REVIEW", cost_key=pn, basis_qty=bomlib.canon(n), basis_unit="EA",
                    formula=f"{fmt(n)}EA × (단가 없음)", review_reason=ver)
                continue
            amt = R.round_won(n * price)
            add("MATERIAL", pn, name, cost_key=pn, basis_qty=bomlib.canon(n), basis_unit="EA",
                unit_price=bomlib.canon(price), price_table="PURCHASE", price_version=ver, amount=amt,
                formula=f"{fmt(n)}EA × {fmt(price)}원 = {amt:,}원", inputs={"qty": str(n)})
        else:
            add("MATERIAL", pn, name, "REVIEW", formula="형상 오류", review_reason=f"{pn}: 알 수 없는 shape {shape!r}")

    # 2) 가공비 / 3) 외주비 ------------------------------------------------
    for category, table, plan, basis_label in (("PROCESS", "PROCESS", routing, "h"),
                                               ("OUTSOURCE", "OUTSOURCE", outsource, None)):
        for e in exploded:
            steps = {k: v for k, v in (plan.get(e["part_no"]) or {}).items()}
            n = Decimal(e["total_qty"]) * qty
            for key, per_unit in steps.items():
                per = Decimal(str(per_unit))
                basis = per * n
                price, unit, ver = _price(versions, table, key)
                u = basis_label or UNIT_LABEL.get(unit or "", "")
                if price is None:
                    add(category, e["part_no"], e["name"], "REVIEW", cost_key=key, basis_qty=bomlib.canon(basis),
                        basis_unit=u, formula=f"{fmt(per)}{u} × {fmt(n)}개 × (단가 없음)", review_reason=ver)
                    continue
                amt = R.round_won(basis * price)
                unit_txt = "원/h" if category == "PROCESS" else f"원/{UNIT_LABEL.get(unit, unit)}"
                add(category, e["part_no"], e["name"], cost_key=key, basis_qty=bomlib.canon(basis), basis_unit=u,
                    unit_price=bomlib.canon(price), price_table=table, price_version=ver, amount=amt,
                    formula=f"{fmt(per)}{u} × {fmt(n)}개 = {fmt(basis)}{u} × {fmt(price)}{unit_txt} = {amt:,}원",
                    inputs={"per_unit": str(per), "item_qty": str(n)})
    return lines


def totals(lines: list[dict]) -> dict:
    t = {c: sum(l["amount"] for l in lines if l["category"] == c and l["amount"] is not None)
         for c in ("MATERIAL", "PROCESS", "OUTSOURCE")}
    subtotal = sum(t.values())
    supply = R.truncate_supply(subtotal)
    v = R.vat(supply)
    review = sum(1 for l in lines if l["status"] == "REVIEW")
    return {"material_total": t["MATERIAL"], "process_total": t["PROCESS"], "outsource_total": t["OUTSOURCE"],
            "subtotal": subtotal, "rounding_adj": supply - subtotal, "supply": supply, "vat": v,
            "total": supply + v, "review_count": review, "status": "NEEDS_REVIEW" if review else "ISSUED"}


def load_specs(path: Path | None = None) -> dict:
    return json.loads((path or HERE / "fixtures" / "part_specs.json").read_text(encoding="utf-8"))


def create_quote(conn, req: dict, specs: dict | None = None) -> dict:
    """견적 생성. 같은 견적번호·같은 요청이면 기존 견적 반환, 다른 요청이면 거부."""
    for f in ("quote_no", "product_no", "revision", "qty", "quote_date", "customer"):
        if req.get(f) in (None, ""):
            raise BadRequest("MISSING_FIELD", f"{f} 필수")
    try:
        date.fromisoformat(req["quote_date"])
        qty = Decimal(str(req["qty"]))
        if qty <= 0 or qty != qty.to_integral_value():
            raise ValueError
    except (ValueError, InvalidOperation):
        raise BadRequest("INPUT_INVALID", "quote_date 는 YYYY-MM-DD, qty 는 1 이상의 정수") from None
    specs = specs or load_specs()
    req_key = {k: str(req[k]) for k in ("product_no", "revision", "qty", "quote_date", "customer")}
    h = _hash(req_key)

    prev = conn.execute("SELECT request_hash FROM quotes WHERE quote_no=?", (req["quote_no"],)).fetchone()
    if prev:
        if prev[0] != h:
            raise QuoteError("QUOTE_NO_CONFLICT", f"견적번호 {req['quote_no']} 는 다른 조건으로 이미 발행됨")
        return {"created": False, **get_quote(conn, req["quote_no"])}

    b = bomlib.get_bom(conn, req["product_no"], req["revision"], unit="mm")
    versions = _versions_at(conn, req["quote_date"])
    lines = compute(b, specs, versions, qty)
    tot = totals(lines)
    used = {t: (v["label"] if v else None) for t, v in versions.items()}
    with bomlib.tx(conn):
        if conn.execute("SELECT 1 FROM quotes WHERE quote_no=?", (req["quote_no"],)).fetchone():
            raise QuoteError("QUOTE_NO_CONFLICT", f"견적번호 {req['quote_no']} 가 동시에 생성됨")
        conn.execute(
            """INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (req["quote_no"], h, req["product_no"], req["revision"], str(qty), req["quote_date"], req["customer"],
             tot["status"], R.RULES_VERSION, json.dumps(used, ensure_ascii=False),
             tot["material_total"], tot["process_total"], tot["outsource_total"], tot["subtotal"],
             tot["rounding_adj"], tot["supply"], tot["vat"], tot["total"], tot["review_count"], _now()))
        conn.executemany(
            "INSERT INTO quote_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(req["quote_no"], i + 1, l["category"], l["item"], l["item_name"], l["cost_key"], l["basis_qty"],
              l["basis_unit"], l["unit_price"], l["price_table"], l["price_version"], l["formula"],
              json.dumps(l["inputs"], ensure_ascii=False), l["amount"], l["status"], l["review_reason"])
             for i, l in enumerate(lines)])
    return {"created": True, **get_quote(conn, req["quote_no"])}


def get_quote(conn, quote_no: str, category: str | None = None) -> dict:
    q = conn.execute("SELECT * FROM quotes WHERE quote_no=?", (quote_no,)).fetchone()
    if q is None:
        raise NotFound("QUOTE_NOT_FOUND", f"견적 {quote_no} 없음")
    if category and category not in CATEGORY_LABEL:
        raise BadRequest("CATEGORY_INVALID", f"category 는 {list(CATEGORY_LABEL)}")
    sql = "SELECT * FROM quote_lines WHERE quote_no=?" + (" AND category=?" if category else "") + " ORDER BY seq"
    lines = [dict(r) | {"inputs": json.loads(r["inputs_json"])} for r in
             conn.execute(sql, (quote_no, category) if category else (quote_no,))]
    for l in lines:
        l.pop("inputs_json")
    used = json.loads(q["versions_json"])
    sources = [dict(r) for r in conn.execute(
        "SELECT table_type, label, effective_from, source FROM price_versions ORDER BY table_type")
        if used.get(r["table_type"]) == r["label"]]
    out = {k: q[k] for k in q.keys() if k not in ("versions_json", "request_hash")}
    return out | {"price_versions": used, "price_sources": sources, "lines": lines}


def list_quotes(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT quote_no, product_no, revision, qty, quote_date, customer, status, supply, vat, total "
        "FROM quotes ORDER BY quote_no")]
