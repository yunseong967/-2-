"""견적 계산 규칙 (규칙 버전 R1). 견적마다 규칙 버전을 저장해 나중에 규칙이 바뀌어도 재현 가능하게 한다.

중량   판재 1장 중량(kg) = 가로(mm) × 세로(mm) × 두께(mm) × 비중(g/cm³) ÷ 1,000,000
       → 소수 둘째 자리 반올림(ROUND_HALF_UP) 후 사용
수율   판재 0.85 (절단 스크랩 15%), 길이재 0.95 (절단 손실 5%)
       필요 소재량 = 사용량 ÷ 수율
재료비 판재   = 수량 × 1장 중량 ÷ 수율 × kg 단가
       길이재 = 총 길이(m) ÷ 수율 × m 단가
       구매품 = 수량 × 개당 단가
가공비 공정별 = 품목 1개당 시간(h) × 품목 총수량 × 시간당 요율
외주비 외주별 = 품목 1개당 외주 수량 × 품목 총수량 × 외주 단가
반올림 각 줄 금액은 원 단위 반올림(ROUND_HALF_UP). 합계는 반올림된 줄 금액의 합
공급가 소계에서 천원 미만 절사 (단수 조정)
부가세 공급가액 × 10%, 원 미만 절사
미등록 단가·기준정보 → 금액을 추측하지 않고 '검토 필요' 줄로 남기고, 견적 상태 = NEEDS_REVIEW (발행 불가)
"""
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

RULES_VERSION = "R1"
DENSITY = {"SUS304": Decimal("7.93"), "SUS316L": Decimal("7.98"), "SS400": Decimal("7.85")}  # g/cm³
YIELD = {"PLATE": Decimal("0.85"), "LENGTH": Decimal("0.95")}
VAT_RATE = Decimal("0.10")
SUPPLY_TRUNC = Decimal(1000)


def round_weight(kg: Decimal) -> Decimal:
    return kg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_won(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def truncate_supply(subtotal: int) -> int:
    return int((Decimal(subtotal) / SUPPLY_TRUNC).to_integral_value(rounding=ROUND_DOWN) * SUPPLY_TRUNC)


def vat(supply: int) -> int:
    return int((Decimal(supply) * VAT_RATE).to_integral_value(rounding=ROUND_DOWN))
