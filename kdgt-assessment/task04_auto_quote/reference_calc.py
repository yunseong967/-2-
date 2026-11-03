"""정답 계산기 — 견적 엔진(quote.py)과 코드를 공유하지 않는 독립 구현.

- BOM 은 과제 03 원본 JSON 을 직접 읽어 자체적으로 전개한다 (bom 모듈 미사용)
- 숫자는 Decimal 대신 fractions.Fraction (정확한 유리수) 으로 계산한다
- 규칙(비중·수율·반올림·부가세)은 README 의 문장을 보고 여기서 다시 적었다

엔진과 이 계산기가 같은 답을 내면, 한쪽에만 있는 실수는 드러난다.
실행: python -m task04_auto_quote.reference_calc   → fixtures/expected.json 갱신 + 견적별 계산서 출력
"""
from __future__ import annotations

import json
import math
from fractions import Fraction as F
from pathlib import Path

HERE = Path(__file__).parent
BOM_DIR = HERE.parent / "task03_bom_revision" / "fixtures"

DENSITY = {"SUS304": F("7.93"), "SUS316L": F("7.98"), "SS400": F("7.85")}
YIELD_PLATE, YIELD_LENGTH = F("0.85"), F("0.95")


def half_up(x: F, step: F = F(1)) -> F:
    return F(math.floor(x / step + F(1, 2))) * step


def to_mm(m: dict) -> F:
    return F(str(m["value"])) * {"mm": 1, "cm": 10, "m": 1000}[m["unit"].lower()]


def load_prices():
    tables = {}
    for p in sorted((HERE / "fixtures" / "prices").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        tables.setdefault(d["table"], []).append(d)
    return tables


def pick(tables, table, day):
    cands = [v for v in tables.get(table, []) if v["effective_from"] <= day]
    if not cands:
        return None
    v = max(cands, key=lambda v: v["effective_from"])
    return {"label": v["label"], "prices": {i["key"]: F(str(i["price"])) for i in v["items"]}}


def explode(rev: str):
    doc = json.loads((BOM_DIR / f"rev_{rev}.json").read_text(encoding="utf-8"))
    items = {i["part_no"]: i for i in doc["items"]}
    kids = {}
    for ln in doc["lines"]:
        kids.setdefault(ln["parent"], []).append(ln)
    qty, length = {doc["product_no"]: F(1)}, {}
    stack = [(doc["product_no"], F(1))]
    while stack:
        node, mult = stack.pop()
        for ln in kids.get(node, []):
            q = mult * F(str(ln["qty"]))
            qty[ln["child"]] = qty.get(ln["child"], F(0)) + q
            if "length" in ln:
                length[ln["child"]] = length.get(ln["child"], F(0)) + q * to_mm(ln["length"])
            stack.append((ln["child"], q))
    return items, qty, length


def calc(req, tables, specs):
    items, qty, length = explode(req["revision"])
    Q = F(req["qty"])
    day = req["quote_date"]
    V = {t: pick(tables, t, day) for t in ("MATERIAL", "LENGTH", "PURCHASE", "PROCESS", "OUTSOURCE")}
    lines, review, steps = {}, [], []

    def price(t, key):
        v = V[t]
        return None if v is None or key not in v["prices"] else v["prices"][key]

    for pn, it in items.items():
        if it["kind"] != "PART":
            continue
        n = qty[pn] * Q
        sp = specs["parts"].get(pn)
        if sp is None:
            review.append(("MATERIAL", pn))
            continue
        if sp["shape"] == "PLATE":
            t = to_mm(it["thickness"])
            w = half_up(F(sp["length_mm"]) * F(sp["width_mm"]) * t * DENSITY[it["material"]] / 1_000_000, F(1, 100))
            p = price("MATERIAL", it["material"])
            if p is None:
                review.append(("MATERIAL", pn))
                continue
            amt = half_up(n * w / YIELD_PLATE * p)
            steps.append(f"재료 {pn}: {n}EA × {float(w)}kg ÷ 0.85 × {p} = {amt}")
        elif sp["shape"] == "LENGTH":
            meters = length[pn] / 1000 * Q
            p = price("LENGTH", f"{sp['size']}/{it['material']}")
            if p is None:
                review.append(("MATERIAL", pn))
                continue
            amt = half_up(meters / YIELD_LENGTH * p)
            steps.append(f"재료 {pn}: {float(meters)}m ÷ 0.95 × {p} = {amt}")
        else:
            p = price("PURCHASE", pn)
            if p is None:
                review.append(("MATERIAL", pn))
                continue
            amt = n * p
            steps.append(f"구매 {pn}: {n}EA × {p} = {amt}")
        lines[("MATERIAL", pn, None)] = int(amt)

    for cat, plan in (("PROCESS", specs["routing"]), ("OUTSOURCE", specs["outsource"])):
        for pn, procs in plan.items():
            if pn.startswith("_") or pn not in qty:
                continue
            for key, per in procs.items():
                p = price(cat, key)
                if p is None:
                    review.append((cat, pn))
                    continue
                amt = half_up(F(str(per)) * qty[pn] * Q * p)
                steps.append(f"{'가공' if cat == 'PROCESS' else '외주'} {pn}/{key}: {per} × {qty[pn] * Q} × {p} = {amt}")
                lines[(cat, pn, key)] = int(amt)

    tot = {c: sum(a for (cc, _, _), a in lines.items() if cc == c) for c in ("MATERIAL", "PROCESS", "OUTSOURCE")}
    sub = sum(tot.values())
    supply = sub // 1000 * 1000
    vat = supply // 10
    return {
        "quote_no": req["quote_no"], "status": "NEEDS_REVIEW" if review else "ISSUED",
        "versions": {t: (v["label"] if v else None) for t, v in V.items()},
        "material_total": tot["MATERIAL"], "process_total": tot["PROCESS"], "outsource_total": tot["OUTSOURCE"],
        "subtotal": sub, "supply": supply, "vat": vat, "total": supply + vat,
        "review_items": sorted(f"{c}:{p}" for c, p in review),
        "line_amounts": {f"{c}:{p}" + (f":{k}" if k else ""): a for (c, p, k), a in sorted(lines.items(), key=str)},
    }, steps


def main():
    specs = json.loads((HERE / "fixtures" / "part_specs.json").read_text(encoding="utf-8"))
    reqs = json.loads((HERE / "fixtures" / "quote_requests.json").read_text(encoding="utf-8"))
    tables = load_prices()
    answers, sheets = [], []
    for r in reqs:
        a, steps = calc(r, tables, specs)
        answers.append(a)
        sheets.append((r, a, steps))
    out = {"_설명": "reference_calc.py(엔진과 독립 구현)가 계산한 견적 10건 정답", "quotes": answers}
    (HERE / "fixtures" / "expected.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# 견적 10건 정답 계산서 (reference_calc.py)", "",
          "엔진(quote.py)과 코드를 공유하지 않는 독립 계산기의 단계별 계산 기록.", ""]
    for r, a, steps in sheets:
        md += [f"## {r['quote_no']} — {r['product_no']} 개정 {r['revision']} × {r['qty']}대, {r['quote_date']}",
               "", f"단가 버전: {a['versions']}", "", "```", *steps, "```", "",
               f"재료비 {a['material_total']:,} / 가공비 {a['process_total']:,} / 외주비 {a['outsource_total']:,} "
               f"→ 소계 {a['subtotal']:,} → 공급가액 {a['supply']:,} + 부가세 {a['vat']:,} = **{a['total']:,}원** "
               f"({a['status']}{', 검토: ' + ', '.join(a['review_items']) if a['review_items'] else ''})", ""]
    (HERE / "reports" / "answer_sheets.md").write_text("\n".join(md), encoding="utf-8")
    for a in answers:
        print(f"{a['quote_no']} {a['status']:<12} 소계 {a['subtotal']:>12,}  합계 {a['total']:>12,}  {a['review_items']}")


if __name__ == "__main__":
    main()
