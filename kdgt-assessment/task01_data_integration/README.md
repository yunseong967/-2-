# 과제 01 — 제조 데이터 통합과 정합성 검사

> 수주·자재·입고·검사·출하 CSV를 **프로젝트와 LOT로 연결**하는 적재기.
> 정상 데이터만 DB에 넣고, 오류 행은 **원본 행 번호와 사유**를 보존해 격리한다.

## 실행 (kdgt-assessment 폴더에서)

```bash
python -m task01_data_integration.cli generate   # 합성 데이터 + 정답(expected.json) 생성 (이미 커밋돼 있음)
python -m task01_data_integration.cli load       # out/task01.db 에 적재
python -m task01_data_integration.cli load       # 한 번 더 → 신규 0, 건수 불변
python -m task01_data_integration.cli broken     # 관계가 끊긴 행 조회
python -m task01_data_integration.cli report     # reports/error_report.{md,csv}
python -m task01_data_integration.cli verify     # 완료 기준 자동 검증 → reports/verification.md
python -m pytest                                 # 단위/시나리오 테스트 23개
```

필요: Python 3.11 이상. 런타임 외부 라이브러리 없음(SQLite 내장). 테스트만 `pytest`.

## 완료 기준 대응

| 슬라이드 완료 기준 | 구현 | 증빙 |
|---|---|---|
| 동일 파일 재적재 시 건수 불변 | 키 + 행 해시 비교 → 같은 내용이면 `UNCHANGED`. 격리 테이블은 `UNIQUE(파일, 행, 해시)` | `reports/verification.md` 1·2번 항목, `test_reload_three_times_counts_unchanged` |
| 오류 20행 격리 | 생성기가 만든 정답(`expected.json`)과 **파일·행 번호·오류코드까지** 대조 | `reports/verification.md` 4번, `reports/error_report.md` |
| 관계가 끊긴 행 조회 가능 | 뷰 `v_broken_relations`, 명령 `cli broken` | `reports/error_report.md` 마지막 표 |
| 원본 행 번호·오류 사유 보존 | 모든 행에 `source_file`, `source_row`, `raw_json`(격리 시) | ERD |

## 데이터 구성 (seed 20261102, `data/sample/`)

| 파일 | 정상 | 주입 오류 | 합계 |
|---|---:|---:|---:|
| orders.csv (수주) | 10 | 0 | 10 |
| materials.csv (자재 LOT) | 20 | 중복 1 | 21 |
| receipts.csv (입고) | 25 | 중복 2·미등록LOT 2·음수 2·날짜 2 | 33 |
| inspections.csv (검사) | 25 | 중복 1·미등록LOT 2·음수 1·날짜 1 | 30 |
| shipments.csv (출하) | 20 | 중복 1·미등록LOT 1·음수 2·날짜 2 | 26 |
| **합계** | **100** | **20** (각 유형 5) | **120** |

오류 행은 **오류가 정확히 하나만** 있도록 만들고 파일 중간 무작위 위치에 넣었다(끝에 몰아두면 행 번호 보존 검증이 약해지므로).

## 설계 판단 (평가 시 설명 포인트)

1. **"정상 100행"의 해석**: 파일 5개 합계 100행으로 해석. 파일마다 100행이 의도라면 `generate_data.py`의 개수만 바꾸면 된다.
2. **중복의 정의**
   - 같은 파일 안에서 키가 다시 나오면 → **뒤 행**을 `DUPLICATE_IN_FILE`로 격리 (앞 행은 정상 적재)
   - 이미 DB에 같은 키·같은 내용 → 재적재로 보고 무시(`UNCHANGED`)
   - 이미 DB에 같은 키·**다른 내용** → 덮어쓰지 않고 `KEY_CONFLICT`로 격리 (조용한 데이터 변경 방지)
3. **날짜는 `YYYY-MM-DD`만 허용**. `2026/04/12` 같은 형식을 추측해 고치지 않는다. 형식/존재하지 않는 날짜 → `DATE_INVALID`, 선후관계 위반(검사일 < 최초 입고일, 출하일 < 수주일, 납기 < 수주일) → `DATE_SEQUENCE`.
4. **적재 순서 = 참조 순서**: 수주 → 자재 LOT → 입고 → 검사 → 출하. 앞 단계에서 정상 적재된 행만 참조 대상이 된다.
5. **배치 = 트랜잭션 1개**: 중간에 실패하면 전부 롤백 (`test_failure_mid_batch_rolls_back`).
6. **격리 행은 지우지 않는다**: 나중에 누락 LOT가 등록되어 정상 적재되면 `resolved_batch_id`만 기록(이력 보존).
7. 인코딩: UTF-8(BOM 포함) → CP949 순서로 시도 (엑셀 저장본 대응).

## 오류 코드

| 코드 | 분류 | 의미 |
|---|---|---|
| DUPLICATE_IN_FILE | 중복 | 같은 파일 안 키 중복 |
| KEY_CONFLICT | 중복 | DB에 같은 키가 다른 내용으로 존재 |
| UNKNOWN_LOT / UNKNOWN_PROJECT | 관계 단절 | 등록부에 없는 참조 |
| NO_RECEIPT | 관계 단절 | 등록된 LOT 이지만 정상 입고 없이 검사됨 |
| QTY_NEGATIVE / QTY_ZERO / QTY_INVALID | 수량 | 음수 / 0 / 숫자 아님·정수 아님 |
| DATE_INVALID / DATE_SEQUENCE | 날짜 | 형식·존재 / 선후관계 |
| MISSING_FIELD / VALUE_INVALID | 기타 | 필수값 누락 / 허용값 밖, LOT-프로젝트 불일치 |

한 행에 오류가 여러 개면 **모두** 기록한다 (예: `QTY_NEGATIVE,DATE_INVALID`).

## 파일

```
task01_data_integration/
├── ERD.md               ERD (Mermaid)
├── schema.sql           테이블·뷰 정의
├── generate_data.py     합성 데이터 + 정답 생성
├── loader.py            검증·적재 (핵심)
├── report.py            오류 보고서, 관계 단절 조회
├── verify.py            완료 기준 자동 검증
├── cli.py               명령행
├── data/sample/         샘플 CSV 5개 + expected.json
├── reports/             error_report.md/.csv, verification.md
├── tests/               pytest
└── AI_LOG.md            AI 사용 기록 (제출 필수)
```

## 알려진 한계 (이번 범위 밖)

- 재고 수불(입고량 ≥ 검사·출하량) 검증은 하지 않음 → 과제 06(LOT 추적)에서 다룸
- `shipments.lot_no`는 "투입된 주 자재 LOT"로 단순화. 제품 LOT/분할·병합은 과제 06
- 같은 파일명을 다른 폴더에서 적재하면 같은 출처로 취급함 (격리 유일키가 파일명 기준)
