"""과제 05 합성 '과거 견적·실제 원가' 데이터 생성기. 생성 규칙은 이 파일과 GENERATION.md 에 공개한다.

한 행 = 견적 1건 (같은 프로젝트에 재견적이 여러 건 있을 수 있음).
target = actual_cost (그 견적 건이 수주됐다면 들어간 실제 원가, 원)

핵심 생성 규칙 (숫자는 아래 코드와 동일)
  1. 프로젝트 120개, 2024-01 ~ 2026-10. 프로젝트당 견적 1~4건, 첫 견적 후 30일 안에 재견적
  2. 제품 유형: REACTOR / AGITATOR / DRYER, 그리고 PSG(Pure Steam Generator)는 2026-05 부터만 등장
  3. 시장 단가(견적일 기준 공개값이므로 입력 특징으로 써도 누수가 아님)
       SUS304 kg 단가 4,600원(2024-01) → 매달 +15원, 2026-06 부터 +400원 계단 인상
       SUS316L = SUS304 × 1.35
       시간당 노무 요율 70,000원, 2026-06 부터 78,000원
  4. 실제 원가 = (재료 + 노무 + 외주) × 수량^0.93 × 급행 할증 × 잡음
       재료 = 판재 중량 × kg 단가 ÷ 0.85
       노무시간 = 유형 기본시간 + 용접길이×0.9 + 노즐×1.5 + 자켓 12h, DRYER 는 ×(용량/1000)^0.25 가중
       외주 = 경판 성형 560,000 + (SUS316L 이면 전해연마 30,000 × 용량^0.67)
       급행 할증: 10% 확률로 ×1.20 (견적 시점에 '급행' 표시가 있음 → 특징으로 사용 가능)
       잡음: 로그정규 σ=0.06
  5. 0원 행: 견적 후 취소된 건은 actual_cost = 0 (status=CANCELLED) — 학습·평가에서 제외하고 건수 보고
같은 seed → 같은 데이터.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "quotes_history.csv"
START, END = date(2024, 1, 1), date(2026, 10, 31)
STEP_DAY = date(2026, 6, 1)
PSG_FROM = date(2026, 5, 1)

TYPES = {  # 용량 범위(L), 기본 노무시간, 노즐 범위
    "REACTOR": ((200, 3000), 40, (4, 10)),
    "AGITATOR": ((100, 2000), 25, (2, 5)),
    "DRYER": ((300, 4000), 60, (4, 8)),
    "PSG": ((100, 800), 70, (6, 12)),
}
COLUMNS = ["record_id", "project_id", "quote_date", "status", "product_type", "volume_l", "material",
           "shell_t_mm", "plate_weight_kg", "nozzle_count", "jacket", "weld_m", "qty", "rush",
           "sus304_price", "labor_rate", "actual_cost"]


def sus304_price(d: date) -> int:
    months = (d.year - START.year) * 12 + d.month - START.month
    return 4600 + 15 * months + (400 if d >= STEP_DAY else 0)


def labor_rate(d: date) -> int:
    return 78000 if d >= STEP_DAY else 70000


def kg_price(material: str, d: date) -> float:
    p = sus304_price(d)
    return p * 1.35 if material == "SUS316L" else p


def true_cost(r: dict, rng: random.Random) -> int:
    """생성 규칙 4번. 모델은 이 함수를 볼 수 없다 (학습 데이터만 봄)."""
    d = date.fromisoformat(r["quote_date"])
    material = r["plate_weight_kg"] * kg_price(r["material"], d) / 0.85
    hours = TYPES[r["product_type"]][1] + r["weld_m"] * 0.9 + r["nozzle_count"] * 1.5 + (12 if r["jacket"] else 0)
    if r["product_type"] == "DRYER":
        hours *= (r["volume_l"] / 1000) ** 0.25
    labor = hours * labor_rate(d)
    outsource = 560_000 + (30_000 * r["volume_l"] ** 0.67 if r["material"] == "SUS316L" else 0)
    cost = (material + labor + outsource) * r["qty"] ** 0.93
    if r["rush"]:
        cost *= 1.20
    cost *= math.exp(rng.gauss(0, 0.06))
    return int(round(cost, -3))


def generate(seed: int = 5, n_projects: int = 120) -> list[dict]:
    rng = random.Random(seed)
    span = (END - START).days - 30
    rows, rid = [], 0
    for p in range(1, n_projects + 1):
        first = START + timedelta(days=rng.randint(0, span))
        types = [t for t in TYPES if t != "PSG" or first >= PSG_FROM]
        weights = [3, 2, 2, 2][:len(types)]
        ptype = rng.choices(types, weights)[0]
        (vmin, vmax), _, (nmin, nmax) = TYPES[ptype]
        volume = rng.randrange(vmin, vmax + 1, 50)
        material = "SUS316L" if rng.random() < (0.6 if ptype == "PSG" else 0.3) else "SUS304"
        t = 6 if volume < 800 else 8 if volume < 2000 else 10
        jacket = ptype in ("REACTOR", "DRYER") and rng.random() < 0.7
        base_w = 0.42 * volume ** 0.9 * t / 6
        n_quotes = rng.choices([1, 2, 3, 4], [4, 3, 2, 1])[0]
        rush = rng.random() < 0.10
        qty = rng.choices([1, 2, 3], [8, 2, 1])[0]
        day = first
        for k in range(n_quotes):
            rid += 1
            nozzles = max(nmin, min(nmax, rng.randint(nmin, nmax) + (k if rng.random() < 0.3 else 0)))
            weight = round(base_w * rng.uniform(0.97, 1.03), 1)
            weld = round(0.004 * volume + nozzles * 0.6 + (8 if jacket else 0) + rng.uniform(-1, 1), 1)
            r = {"record_id": f"H{rid:04d}", "project_id": f"PJ{p:03d}", "quote_date": day.isoformat(),
                 "status": "QUOTED", "product_type": ptype, "volume_l": volume, "material": material,
                 "shell_t_mm": t, "plate_weight_kg": weight, "nozzle_count": nozzles, "jacket": int(jacket),
                 "weld_m": weld, "qty": qty, "rush": int(rush),
                 "sus304_price": sus304_price(day), "labor_rate": labor_rate(day)}
            r["actual_cost"] = true_cost(r, rng)
            rows.append(r)
            day = day + timedelta(days=rng.randint(3, 10))
            if day > END:
                break
    # 규칙 5: 취소 건 (0원) — 무작위 10건
    for r in rng.sample(rows, 10):
        r["status"], r["actual_cost"] = "CANCELLED", 0
    rows.sort(key=lambda r: (r["quote_date"], r["record_id"]))
    return rows


def write(rows, path: Path = OUT):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument("--seed", type=int, default=5)
    a.add_argument("--projects", type=int, default=120)
    args = a.parse_args(argv)
    rows = generate(args.seed, args.projects)
    write(rows)
    print(f"{OUT}: {len(rows)}행, 프로젝트 {len({r['project_id'] for r in rows})}개, "
          f"0원(취소) {sum(r['actual_cost'] == 0 for r in rows)}건")


if __name__ == "__main__":
    main()
