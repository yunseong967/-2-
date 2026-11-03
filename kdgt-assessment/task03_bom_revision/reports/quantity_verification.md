# 과제 03 수량 검증 결과

개정별 다단계 전개 총수량(부모 수량 × 자식 수량의 곱을 경로별로 합산)을 수작업 정답과 대조.

## 전개 수량

| 개정 | 품번 | 정답 | 계산 | 판정 |
|---|---|---:|---:|---|
| A | SA-SHELL | 1 | 1 | OK |
| A | SA-JACKET | 1 | 1 | OK |
| A | SA-NOZZLE | 3 | 3 | OK |
| A | PL-SHELL | 2 | 2 | OK |
| A | HD-TOP | 1 | 1 | OK |
| A | HD-BTM | 1 | 1 | OK |
| A | NZ-50A | 3 | 3 | OK |
| A | FL-50A | 3 | 3 | OK |
| A | GSK-50A | 3 | 3 | OK |
| A | BOLT-M16 | 12 | 12 | OK |
| A | PL-JKT | 2 | 2 | OK |
| A | PIPE-25A | 4 | 4 | OK |
| A | ELB-25A | 6 | 6 | OK |
| A | LEG | 4 | 4 | OK |
| A | LUG | 4 | 4 | OK |
| A | PIPE-25A 총길이(mm) | 5000 | 5000 | OK |
| A | LEG 총길이(mm) | 3200 | 3200 | OK |
| B | SA-SHELL | 1 | 1 | OK |
| B | SA-JACKET | 1 | 1 | OK |
| B | SA-NOZZLE | 3 | 3 | OK |
| B | PL-SHELL | 2 | 2 | OK |
| B | HD-TOP | 1 | 1 | OK |
| B | HD-BTM | 1 | 1 | OK |
| B | NZ-50A | 3 | 3 | OK |
| B | FL-50A | 3 | 3 | OK |
| B | GSK-50A | 3 | 3 | OK |
| B | BOLT-M16 | 12 | 12 | OK |
| B | PL-JKT | 2 | 2 | OK |
| B | PIPE-25A | 4 | 4 | OK |
| B | ELB-25A | 6 | 6 | OK |
| B | LEG | 4 | 4 | OK |
| B | LUG | 4 | 4 | OK |
| B | PIPE-25A 총길이(mm) | 5000 | 5000 | OK |
| B | LEG 총길이(mm) | 3200 | 3200 | OK |
| C | SA-SHELL | 1 | 1 | OK |
| C | SA-JACKET | 1 | 1 | OK |
| C | SA-NOZZLE | 3 | 3 | OK |
| C | PL-SHELL | 2 | 2 | OK |
| C | HD-TOP | 1 | 1 | OK |
| C | HD-BTM | 1 | 1 | OK |
| C | NZ-50A | 3 | 3 | OK |
| C | FL-50A | 3 | 3 | OK |
| C | GSK-50A | 3 | 3 | OK |
| C | BOLT-M16 | 12 | 12 | OK |
| C | PL-JKT | 2 | 2 | OK |
| C | PIPE-25A | 4 | 4 | OK |
| C | ELB-25A | 6 | 6 | OK |
| C | LEG | 4 | 4 | OK |
| C | LUG | 4 | 4 | OK |
| C | SG-01 | 1 | 1 | OK |
| C | WSH-M16 | 12 | 12 | OK |
| C | PIPE-25A 총길이(mm) | 5000 | 5000 | OK |
| C | LEG 총길이(mm) | 3200 | 3200 | OK |
| D | SA-SHELL | 1 | 1 | OK |
| D | SA-JACKET | 1 | 1 | OK |
| D | SA-NOZZLE | 4 | 4 | OK |
| D | PL-SHELL | 2 | 2 | OK |
| D | HD-TOP | 1 | 1 | OK |
| D | HD-BTM | 1 | 1 | OK |
| D | NZ-50A | 4 | 4 | OK |
| D | FL-50A | 4 | 4 | OK |
| D | GSK-50A | 4 | 4 | OK |
| D | BOLT-M16 | 16 | 16 | OK |
| D | PL-JKT | 2 | 2 | OK |
| D | PIPE-25A | 6 | 6 | OK |
| D | ELB-25A | 6 | 6 | OK |
| D | LEG | 4 | 4 | OK |
| D | SG-01 | 1 | 1 | OK |
| D | WSH-M16 | 16 | 16 | OK |
| D | PIPE-25A 총길이(mm) | 7500 | 7500 | OK |
| D | LEG 총길이(mm) | 3200 | 3200 | OK |

## 개정 비교 요약

| 비교 | 추가 | 삭제 | 수량변경 | 길이변경 | 규격변경 | 정답 |
|---|---:|---:|---:|---:|---:|---|
| A->B | 0 | 0 | 0 | 0 | 2 | OK |
| B->C | 2 | 0 | 0 | 0 | 0 | OK |
| C->D | 0 | 1 | 2 | 0 | 0 | OK |

## mm·m 변환

| 입력 | mm 정답 | mm 계산 | m 정답 | m 계산 | 판정 |
|---|---:|---:|---:|---:|---|
| 1250 mm | 1250 | 1250 | 1.25 | 1.25 | OK |
| 1.25 m | 1250 | 1250 | 1.25 | 1.25 | OK |
| 125 cm | 1250 | 1250 | 1.25 | 1.25 | OK |
| 0.8 m | 800 | 800 | 0.8 | 0.8 | OK |
| 4.5 mm | 4.5 | 4.5 | 0.0045 | 0.0045 | OK |
| 1 cm | 10 | 10 | 0.01 | 0.01 | OK |
| 0.1 m | 100 | 100 | 0.1 | 0.1 | OK |

## 오류 입력 (순환 참조 등)

| 입력 | 기대 코드 | HTTP | 반환 코드 | 순환 경로 | 판정 |
|---|---|---:|---|---|---|
| cycle_nested | CYCLE | 422 | CYCLE | SA-SHELL → SA-NOZZLE → SA-SHELL | OK |
| cycle_self | CYCLE | 422 | CYCLE | SA-JACKET → SA-JACKET | OK |
| unit_inch | UNIT_UNSUPPORTED | 422 | UNIT_UNSUPPORTED |  | OK |
| unknown_part | UNKNOWN_PART | 422 | UNKNOWN_PART |  | OK |
| qty_negative | QTY_NOT_POSITIVE | 422 | QTY_NOT_POSITIVE |  | OK |
| part_has_children | PART_HAS_CHILDREN | 422 | PART_HAS_CHILDREN |  | OK |
| orphan_item | ORPHAN_ITEM | 422 | ORPHAN_ITEM |  | OK |
