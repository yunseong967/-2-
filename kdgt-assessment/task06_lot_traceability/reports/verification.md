# 과제 06 자동 검증 결과

- 방법: 빈 DB + 실제 HTTP 서버(ThreadingHTTPServer)에 요청
- 재실행: `python -m task06_lot_traceability.cli verify`

## 판정

| 결과 | 항목 | 근거 |
|---|---|---|
| PASS | 시나리오 38단계 정답 일치 | 전 단계 일치 |
| PASS | 동시성 A: 20스레드×3EA, 재고 30EA → 10건만 성공 | 결과 {'SHIPPED': 10, 'INSUFFICIENT_QTY': 10}, 남은 재고 0 |
| PASS | 동시성 B: 같은 request_id 10건 동시 → 출하 1건 | 신규 1, replayed 9, 출하번호 {15}, 남은 재고 9 |
| PASS | 동시성 C: 출하 10건과 부적합 재검사 동시 → 부적합 이후 출하 0건 | 부적합 전 출하 5건, 차단 5건, 부적합 이후 출하 0건 |
| PASS | 재고 수불 대조 (LOT별·품목별) 일치, 음수 재고 0 | LOT 16개 불일치 [], 품목 5개 불일치 [], 음수 LOT 0 |

## 시나리오 단계별 결과

| # | 단계 | 기대 | 실제 | 판정 | 근거 |
|---:|---|---|---|---|---|
| 1 | 자재 입고 M-A SUS304 6t 1000KG | 201 | 201 | PASS |  |
| 2 | 자재 입고 M-B SUS304 6t 400KG | 201 | 201 | PASS |  |
| 3 | 자재 입고 M-C SUS316L 8t 500KG (검사 안 함) | 201 | 201 | PASS |  |
| 4 | 자재 입고 M-D SS400 10t 300KG | 201 | 201 | PASS |  |
| 5 | 같은 LOT 번호로 다시 입고 → 거부 | 409 DUPLICATE_LOT | 409 DUPLICATE_LOT | PASS | DUPLICATE_LOT: LOT M-A 이미 존재 |
| 6 | M-A 수입검사 합격 | 201 | 201 | PASS |  |
| 7 | M-B 수입검사 합격 | 201 | 201 | PASS |  |
| 8 | M-D 수입검사 부적합(두께 미달) | 201 | 201 | PASS |  |
| 9 | M-A 분할 300 + 200 (남은 500은 M-A) | 201 | 201 | PASS | 자식 M-A-S1(300)/M-A-S2(200) 합격 상태 상속, 부모 500 |
| 10 | 분할 합계가 재고 초과 → 거부 | 409 INSUFFICIENT_QTY | 409 INSUFFICIENT_QTY | PASS | INSUFFICIENT_QTY: LOT M-A 분할 합계 600 > 현재 500 |
| 11 | M-B + M-A-S2 병합 → MRG-1 (600KG) | 201 | 201 | PASS | 600KG, 합격 |
| 12 | 다른 품목(M-C) 병합 → 거부 | 409 ITEM_MISMATCH | 409 ITEM_MISMATCH | PASS | ITEM_MISMATCH: 품목·단위·종류가 다른 LOT 은 병합 불가 |
| 13 | 제품 P-001 반응기 쉘 2EA 생산 (M-A-S1 250 + MRG-1 100 투입) | 201 | 201 | PASS |  |
| 14 | 제품 P-002 3EA 생산 (M-A 200 투입) | 201 | 201 | PASS |  |
| 15 | 미검사 자재 M-C 투입 → 차단 | 409 STATUS_BLOCKED | 409 STATUS_BLOCKED | PASS | STATUS_BLOCKED: LOT M-C 은(는) 미검사 상태라 생산 투입 불가 |
| 16 | 부적합 자재 M-D 투입 → 차단 | 409 STATUS_BLOCKED | 409 STATUS_BLOCKED | PASS | STATUS_BLOCKED: LOT M-D 은(는) 부적합 상태라 생산 투입 불가 (검사 #3 부적합: 두께 9.6mm 실측) |
| 17 | 제품 P-003 1EA 생산 (MRG-1 50 투입, 제품검사 안 함) | 201 | 201 | PASS |  |
| 18 | P-001 제품검사 합격 | 201 | 201 | PASS |  |
| 19 | P-002 제품검사 합격 | 201 | 201 | PASS |  |
| 20 | 자재 LOT 직접 출하 → 차단 | 409 KIND_INVALID | 409 KIND_INVALID | PASS | KIND_INVALID: LOT M-A 은(는) 자재 LOT — 제품 LOT 만 출하 가능 |
| 21 | P-001 부분 출하 1EA (REQ-001) | 201 | 201 | PASS | 검사 근거 3건 저장: P-001#1, M-A#1, M-B#1 |
| 22 | 같은 요청 REQ-001 재전송 → 출하 안 늘어남(200, replayed) | 200 | 200 | PASS | replayed=true, 같은 출하번호 반환 |
| 23 | REQ-001 을 다른 수량으로 재사용 → 거부 | 409 REQUEST_ID_CONFLICT | 409 REQUEST_ID_CONFLICT | PASS | REQUEST_ID_CONFLICT: request_id REQ-001 는 다른 내용으로 이미 사용됨 |
| 24 | P-001 나머지 1EA 출하 (REQ-002) | 201 | 201 | PASS |  |
| 25 | P-001 재고 0 상태에서 추가 출하 → 차단 | 409 INSUFFICIENT_QTY | 409 INSUFFICIENT_QTY | PASS | INSUFFICIENT_QTY: LOT P-001 재고 부족: 요청 1, 현재 0 |
| 26 | P-002 2EA 출하 (REQ-004) | 201 | 201 | PASS |  |
| 27 | 미검사 제품 P-003 출하 → 차단 | 409 STATUS_BLOCKED | 409 STATUS_BLOCKED | PASS | STATUS_BLOCKED: LOT P-003 은(는) 미검사 상태라 출하 불가 |
| 28 | M-A 재검사 부적합 (MTC 성분 불일치) → 하위 보류 + 영향 출하 | 201 | 201 | PASS | 보류: M-A-S1, M-A-S2, P-002, P-001, MRG-1, P-003 / 영향 출하: REQ-001, REQ-002, REQ-004 |
| 29 | 보류된 P-002 출하 → 차단 | 409 STATUS_BLOCKED | 409 STATUS_BLOCKED | PASS | STATUS_BLOCKED: LOT P-002 은(는) 보류 상태라 출하 불가 (상위 LOT M-A 부적합(검사 #6)) |
| 30 | P-002 개별 재검사 합격 (제품 성분분석 적합) | 201 | 201 | PASS |  |
| 31 | P-002 는 합격이지만 투입 자재 M-A 가 부적합 → 출하 차단 | 409 UPSTREAM_FAIL | 409 UPSTREAM_FAIL | PASS | UPSTREAM_FAIL: LOT P-002 의 검사 근거에 부적합이 있어 출하 불가: M-A 검사#2 |
| 32 | 부적합 근거가 있는 MRG-1 투입 생산 → 차단 | 409 STATUS_BLOCKED | 409 STATUS_BLOCKED | PASS | STATUS_BLOCKED: LOT MRG-1 은(는) 보류 상태라 생산 투입 불가 (상위 LOT M-A 부적합(검사 #6)) |
| 33 | M-A 재검사 #3 합격 (MTC 재발행·성분 재분석 적합) → 보류 해제 | 201 | 201 | PASS | 해제: M-A-S1, M-A-S2, P-001, MRG-1, P-003 (P-003 은 원래 미검사로 복원) |
| 34 | P-003 은 미검사로 복원되어 여전히 출하 차단 | 409 STATUS_BLOCKED | 409 STATUS_BLOCKED | PASS | STATUS_BLOCKED: LOT P-003 은(는) 미검사 상태라 출하 불가 |
| 35 | P-002 나머지 1EA 출하 (REQ-006 재시도) | 201 | 201 | PASS | 근거: P-002#2 PASS, M-A#3 PASS |
| 36 | 정방향 추적 M-A → 영향 출하 4건 | 200 | 200 | PASS | 출하: REQ-001, REQ-002, REQ-004, REQ-006 |
| 37 | 역방향 추적 출하#1 → 원자재 M-A, M-B | 200 | 200 | PASS | 원자재: M-A, M-B |
| 38 | 재고 수불 대조 일치 | 200 | 200 | PASS | LOT 10개 / 품목 4개 모두 일치, 음수 0 |
