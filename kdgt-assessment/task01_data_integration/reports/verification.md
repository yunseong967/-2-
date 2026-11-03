# 과제 01 자동 검증 결과

- 입력: `task01_data_integration/data/sample` (+ 정답 `expected.json`)
- 방법: 빈 DB에 같은 파일을 2회 적재한 뒤 아래 항목을 확인
- 재실행: `python -m task01_data_integration.cli verify`

## 판정

| 결과 | 항목 | 근거 |
|---|---|---|
| PASS | 재적재 시 건수 불변 | 1차 {"projects": 10, "material_lots": 20, "receipts": 25, "inspections": 25, "shipments": 20, "quarantine": 20} / 2차 {"projects": 10, "material_lots": 20, "receipts": 25, "inspections": 25, "shipments": 20, "quarantine": 20} |
| PASS | 2차 적재는 신규 0건, 전부 '변경없음' | 2차 신규 0건, 변경없음 100건 |
| PASS | 정상 100행 적재 (파일별 일치) | orders.csv=10/10, materials.csv=20/20, receipts.csv=25/25, inspections.csv=25/25, shipments.csv=20/20 |
| PASS | 오류 20행 격리 (파일·행·코드 정답 일치) | 격리 20행, 누락 [], 초과 [], 코드불일치 [] |
| PASS | 관계가 끊긴 행 조회 (v_broken_relations) | 5행 조회됨 (기대 5) : inspections.csv:8→L2026-9008, inspections.csv:23→L2026-9009, receipts.csv:6→L2026-9007, receipts.csv:20→L2026-9006, shipments.csv:4→L2026-9010 |
| PASS | 외래키 무결성 (PRAGMA foreign_key_check) | 위반 0건 |
| PASS | 원본 행 번호 보존 | 누락 0, 중복 0 |

## 1차 적재

| 파일 | 읽은 행 | 신규 | 변경없음 | 격리 |
|---|---:|---:|---:|---:|
| orders.csv | 10 | 10 | 0 | 0 |
| materials.csv | 21 | 20 | 0 | 1 |
| receipts.csv | 33 | 25 | 0 | 8 |
| inspections.csv | 30 | 25 | 0 | 5 |
| shipments.csv | 26 | 20 | 0 | 6 |

## 2차 적재 (동일 파일)

| 파일 | 읽은 행 | 신규 | 변경없음 | 격리 |
|---|---:|---:|---:|---:|
| orders.csv | 10 | 0 | 10 | 0 |
| materials.csv | 21 | 0 | 20 | 1 |
| receipts.csv | 33 | 0 | 25 | 8 |
| inspections.csv | 30 | 0 | 25 | 5 |
| shipments.csv | 26 | 0 | 20 | 6 |

## 테이블 건수

| 테이블 | 1차 후 | 2차 후 |
|---|---:|---:|
| projects | 10 | 10 |
| material_lots | 20 | 20 |
| receipts | 25 | 25 |
| inspections | 25 | 25 |
| shipments | 20 | 20 |
| quarantine | 20 | 20 |
