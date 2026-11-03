"""과제 01 합성 데이터 생성기.

정상 100행 + 오류 20행(중복·미등록 LOT·음수수량·날짜오류 각 5행)을 만든다.
오류 행은 '오류가 정확히 하나만' 있도록 만들고, 무작위 위치에 끼워 넣는다.
정답(어느 파일 몇 행이 어떤 오류인지)은 expected.json 으로 함께 저장한다.

같은 seed → 항상 같은 파일. (재현성)

정상 100행 구성
  orders.csv      10  (프로젝트)
  materials.csv   20  (자재 LOT, 프로젝트당 2개)
  receipts.csv    25  (LOT마다 1건 + 추가 5건)
  inspections.csv 25
  shipments.csv   20
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

from common.csvio import write_rows

HEADERS = {
    "orders.csv": ["project_id", "customer", "product", "order_date", "due_date", "order_qty"],
    "materials.csv": ["lot_no", "project_id", "material_code", "material", "thickness_mm", "unit"],
    "receipts.csv": ["receipt_id", "lot_no", "supplier", "received_date", "qty"],
    "inspections.csv": ["inspection_id", "lot_no", "inspected_date", "result", "inspected_qty"],
    "shipments.csv": ["shipment_id", "project_id", "lot_no", "shipped_date", "qty"],
}

CUSTOMERS = ["한빛제약", "대성바이오", "청솔식품", "미래화학", "서해케미칼"]
PRODUCTS = ["반응기 500L", "반응기 1000L", "교반기 200L", "진공건조기 VD-300", "Pure Steam Generator"]
MATERIALS = [("M-SUS304", "SUS304"), ("M-SUS316L", "SUS316L"), ("M-SS400", "SS400")]
THICKNESS = [3.0, 4.5, 6.0, 8.0, 10.0, 12.0]
SUPPLIERS = ["포스코강판", "현대비앤지스틸", "대한특수강"]

# 오류 주입 계획: (파일, 오류 종류, 기대 오류코드)
ERROR_PLAN = [
    # 중복 5
    ("materials.csv", "duplicate", "DUPLICATE_IN_FILE"),
    ("receipts.csv", "duplicate", "DUPLICATE_IN_FILE"),
    ("receipts.csv", "duplicate", "DUPLICATE_IN_FILE"),
    ("inspections.csv", "duplicate", "DUPLICATE_IN_FILE"),
    ("shipments.csv", "duplicate", "DUPLICATE_IN_FILE"),
    # 미등록 LOT 5
    ("receipts.csv", "unknown_lot", "UNKNOWN_LOT"),
    ("receipts.csv", "unknown_lot", "UNKNOWN_LOT"),
    ("inspections.csv", "unknown_lot", "UNKNOWN_LOT"),
    ("inspections.csv", "unknown_lot", "UNKNOWN_LOT"),
    ("shipments.csv", "unknown_lot", "UNKNOWN_LOT"),
    # 음수수량 5
    ("receipts.csv", "negative_qty", "QTY_NEGATIVE"),
    ("receipts.csv", "negative_qty", "QTY_NEGATIVE"),
    ("inspections.csv", "negative_qty", "QTY_NEGATIVE"),
    ("shipments.csv", "negative_qty", "QTY_NEGATIVE"),
    ("shipments.csv", "negative_qty", "QTY_NEGATIVE"),
    # 날짜오류 5 (형식/존재하지 않는 날짜 3 + 선후관계 위반 2)
    ("receipts.csv", "bad_date", "DATE_INVALID"),
    ("receipts.csv", "bad_date", "DATE_INVALID"),
    ("shipments.csv", "bad_date", "DATE_INVALID"),
    ("inspections.csv", "date_sequence", "DATE_SEQUENCE"),
    ("shipments.csv", "date_sequence", "DATE_SEQUENCE"),
]
CATEGORY = {"duplicate": "중복", "unknown_lot": "미등록 LOT", "negative_qty": "음수수량",
            "bad_date": "날짜오류", "date_sequence": "날짜오류"}
BAD_DATES = ["2026-02-30", "2026-13-05", "2026/04/12"]


def _d(x: date) -> str:
    return x.isoformat()


def build_normal(rng: random.Random) -> dict[str, list[dict]]:
    start = date(2026, 1, 5)
    orders = []
    for i in range(1, 11):
        od = start + timedelta(days=rng.randint(0, 120))
        orders.append({
            "project_id": f"P2026-{i:03d}",
            "customer": rng.choice(CUSTOMERS),
            "product": rng.choice(PRODUCTS),
            "order_date": _d(od),
            "due_date": _d(od + timedelta(days=rng.randint(60, 120))),
            "order_qty": rng.randint(1, 3),
        })

    lots = []
    for i in range(20):
        proj = orders[i // 2]
        code, name = rng.choice(MATERIALS)
        lots.append({
            "lot_no": f"L2026-{i + 1:04d}", "project_id": proj["project_id"],
            "material_code": code, "material": name,
            "thickness_mm": rng.choice(THICKNESS), "unit": "KG",
        })
    order_of = {o["project_id"]: o for o in orders}

    receipts, first_receipt = [], {}
    lot_targets = [l for l in lots] + rng.sample(lots, 5)
    for i, lot in enumerate(lot_targets, 1):
        od = date.fromisoformat(order_of[lot["project_id"]]["order_date"])
        rd = od + timedelta(days=rng.randint(3, 20))
        receipts.append({
            "receipt_id": f"R2026-{i:04d}", "lot_no": lot["lot_no"],
            "supplier": rng.choice(SUPPLIERS), "received_date": _d(rd),
            "qty": rng.randint(50, 800),
        })
        first_receipt[lot["lot_no"]] = min(first_receipt.get(lot["lot_no"], rd), rd)

    inspections = []
    for i in range(1, 26):
        lot = lots[(i - 1) % 20] if i <= 20 else rng.choice(lots)
        idate = first_receipt[lot["lot_no"]] + timedelta(days=rng.randint(0, 5))
        inspections.append({
            "inspection_id": f"I2026-{i:04d}", "lot_no": lot["lot_no"],
            "inspected_date": _d(idate),
            "result": "FAIL" if rng.random() < 0.15 else "PASS",
            "inspected_qty": rng.randint(10, 50),
        })

    shipments = []
    for i in range(1, 21):
        proj = orders[(i - 1) % 10]
        lot = rng.choice([l for l in lots if l["project_id"] == proj["project_id"]])
        sd = date.fromisoformat(proj["order_date"]) + timedelta(days=rng.randint(60, 130))
        shipments.append({
            "shipment_id": f"S2026-{i:04d}", "project_id": proj["project_id"],
            "lot_no": lot["lot_no"], "shipped_date": _d(sd),
            "qty": rng.randint(1, proj["order_qty"]),
        })

    return {"orders.csv": orders, "materials.csv": lots, "receipts.csv": receipts,
            "inspections.csv": inspections, "shipments.csv": shipments,
            "_first_receipt": first_receipt, "_order_of": order_of}


def make_error_row(rng, file_name: str, kind: str, data: dict, seq: int) -> tuple[dict, dict | None]:
    """(오류 행, 원본 행(중복일 때만)) 반환. 각 오류 행은 정확히 한 가지 오류만 가진다."""
    rows = data[file_name]
    if kind == "duplicate":
        original = rng.choice(rows)
        return dict(original), original

    base = dict(rng.choice(rows))
    key = HEADERS[file_name][0]
    prefix = {"receipts.csv": "R", "inspections.csv": "I", "shipments.csv": "S"}[file_name]
    base[key] = f"{prefix}2026-9{seq:03d}"  # 새 키 → 중복이 섞이지 않게

    if kind == "unknown_lot":
        base["lot_no"] = f"L2026-9{seq:03d}"
    elif kind == "negative_qty":
        col = "inspected_qty" if file_name == "inspections.csv" else "qty"
        base[col] = -rng.randint(1, 30)
    elif kind == "bad_date":
        col = {"receipts.csv": "received_date", "shipments.csv": "shipped_date"}[file_name]
        base[col] = BAD_DATES[seq % len(BAD_DATES)]
    elif kind == "date_sequence":
        if file_name == "inspections.csv":
            first = data["_first_receipt"][base["lot_no"]]
            base["inspected_date"] = _d(first - timedelta(days=rng.randint(1, 10)))
        else:
            od = date.fromisoformat(data["_order_of"][base["project_id"]]["order_date"])
            base["shipped_date"] = _d(od - timedelta(days=rng.randint(1, 10)))
    return base, None


def generate(out_dir: str | Path, seed: int = 20261102) -> dict:
    rng = random.Random(seed)
    data = build_normal(rng)
    files = {f: [(r, None) for r in data[f]] for f in HEADERS}  # (행, 오류정보)

    for seq, (file_name, kind, code) in enumerate(ERROR_PLAN, 1):
        row, original = make_error_row(rng, file_name, kind, data, seq)
        info = {"kind": kind, "category": CATEGORY[kind], "expected_code": code,
                "key": row[HEADERS[file_name][0]]}
        bucket = files[file_name]
        if original is not None:
            # 중복은 반드시 원본보다 뒤에 둔다
            orig_idx = next(i for i, (r, e) in enumerate(bucket) if r is original)
            pos = rng.randint(orig_idx + 1, len(bucket))
        else:
            pos = rng.randint(0, len(bucket))
        bucket.insert(pos, (row, info))

    out_dir = Path(out_dir)
    expected = {"seed": seed, "normal_rows": {}, "error_rows": []}
    for file_name, bucket in files.items():
        write_rows(out_dir / file_name, HEADERS[file_name], [r for r, _ in bucket])
        expected["normal_rows"][file_name] = sum(1 for _, e in bucket if e is None)
        for idx, (_, info) in enumerate(bucket):
            if info:
                expected["error_rows"].append({"file": file_name, "source_row": idx + 2, **info})
    expected["error_rows"].sort(key=lambda e: (e["file"], e["source_row"]))
    expected["totals"] = {
        "normal": sum(expected["normal_rows"].values()),
        "error": len(expected["error_rows"]),
    }
    (out_dir / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    return expected


def main(argv=None):
    p = argparse.ArgumentParser(description="과제 01 합성 데이터 생성")
    p.add_argument("--out", default=str(Path(__file__).parent / "data" / "sample"))
    p.add_argument("--seed", type=int, default=20261102)
    a = p.parse_args(argv)
    exp = generate(a.out, a.seed)
    print(f"생성 완료: {a.out}  정상 {exp['totals']['normal']}행 / 오류 {exp['totals']['error']}행")


if __name__ == "__main__":
    main()
