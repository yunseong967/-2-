# 과제 01 오류 보고서

격리된 행: **20행** (전체 목록: `error_report.csv`)

## 분류별

| 분류 | 건수 |
|---|---:|
| 날짜오류 | 5 |
| 미등록 LOT | 5 |
| 음수수량 | 5 |
| 중복 | 5 |

## 파일별

| 파일 | 건수 |
|---|---:|
| inspections.csv | 5 |
| materials.csv | 1 |
| receipts.csv | 8 |
| shipments.csv | 6 |

## 상세

| 파일 | 원본행 | 키 | 오류코드 | 사유 |
|---|---:|---|---|---|
| inspections.csv | 7 | I2026-9013 | QTY_NEGATIVE | inspected_qty: 음수 (-14) |
| inspections.csv | 8 | I2026-9008 | UNKNOWN_LOT | 자재 LOT 등록부에 없는 LOT: L2026-9008 |
| inspections.csv | 12 | I2026-9019 | DATE_SEQUENCE | 검사일(2026-02-27)이 최초 입고일(2026-03-01)보다 빠름 |
| inspections.csv | 23 | I2026-9009 | UNKNOWN_LOT | 자재 LOT 등록부에 없는 LOT: L2026-9009 |
| inspections.csv | 31 | I2026-0025 | DUPLICATE_IN_FILE | inspection_id=I2026-0025 가 30행과 중복 |
| materials.csv | 12 | L2026-0007 | DUPLICATE_IN_FILE | lot_no=L2026-0007 가 8행과 중복 |
| receipts.csv | 6 | R2026-9007 | UNKNOWN_LOT | 자재 LOT 등록부에 없는 LOT: L2026-9007 |
| receipts.csv | 8 | R2026-9011 | QTY_NEGATIVE | qty: 음수 (-26) |
| receipts.csv | 15 | R2026-9012 | QTY_NEGATIVE | qty: 음수 (-19) |
| receipts.csv | 17 | R2026-0006 | DUPLICATE_IN_FILE | receipt_id=R2026-0006 가 9행과 중복 |
| receipts.csv | 20 | R2026-9006 | UNKNOWN_LOT | 자재 LOT 등록부에 없는 LOT: L2026-9006 |
| receipts.csv | 25 | R2026-0012 | DUPLICATE_IN_FILE | receipt_id=R2026-0012 가 16행과 중복 |
| receipts.csv | 29 | R2026-9016 | DATE_INVALID | received_date: 존재하지 않는 날짜: '2026-13-05' |
| receipts.csv | 31 | R2026-9017 | DATE_INVALID | received_date: 형식 오류(YYYY-MM-DD 아님): '2026/04/12' |
| shipments.csv | 4 | S2026-9010 | UNKNOWN_LOT | 자재 LOT 등록부에 없는 LOT: L2026-9010 |
| shipments.csv | 7 | S2026-0002 | DUPLICATE_IN_FILE | shipment_id=S2026-0002 가 3행과 중복 |
| shipments.csv | 9 | S2026-9015 | QTY_NEGATIVE | qty: 음수 (-18) |
| shipments.csv | 11 | S2026-9020 | DATE_SEQUENCE | 출하일(2026-03-17)이 수주일(2026-03-18)보다 빠름 |
| shipments.csv | 16 | S2026-9014 | QTY_NEGATIVE | qty: 음수 (-7) |
| shipments.csv | 25 | S2026-9018 | DATE_INVALID | shipped_date: 존재하지 않는 날짜: '2026-02-30' |

## 관계가 끊긴 행 (v_broken_relations)

| 파일 | 원본행 | 키 | 참조 LOT | 참조 프로젝트 |
|---|---:|---|---|---|
| inspections.csv | 8 | I2026-9008 | L2026-9008 |  |
| inspections.csv | 23 | I2026-9009 | L2026-9009 |  |
| receipts.csv | 6 | R2026-9007 | L2026-9007 |  |
| receipts.csv | 20 | R2026-9006 | L2026-9006 |  |
| shipments.csv | 4 | S2026-9010 | L2026-9010 | P2026-007 |
