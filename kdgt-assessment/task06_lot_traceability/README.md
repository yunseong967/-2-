# 과제 06 — LOT 양방향 추적과 출하 차단

> 자재 LOT → 출하 제품(정방향), 출하 제품 → 원자재(역방향)를 추적한다.
> 분할·병합·부분 출하·재검사를 지원하고, **미검사·부적합 재고의 출하를 막으며, 동시 출하 요청에도 재고가 음수가 되거나 이중 출하되지 않는다.**

## 실행 (kdgt-assessment 폴더에서)

```bash
python -m task06_lot_traceability.cli verify   # 자동 검증 (시나리오 38단계 + 동시성 3종 + 수불 대조)
python -m task06_lot_traceability.cli demo     # 데모 DB(out/task06.db)를 새로 만들고 시나리오 적재
python -m task06_lot_traceability.cli serve    # http://127.0.0.1:8006  추적 화면 + API
python -m pytest                               # 전체 테스트 (과제 06: 30개)
```

Python 3.11+, 외부 라이브러리 없음. 화면은 `serve` 후 브라우저로 접속.

## 완료 기준 대응

| 슬라이드 요구 | 구현 | 증빙 |
|---|---|---|
| 재고 음수 방지 | ① `BEGIN IMMEDIATE` 로 확인·차감을 한 트랜잭션에 ② `UPDATE … WHERE qty_m >= ?` 조건부 차감 ③ 테이블 `CHECK (qty_m >= 0)` | verification.md 동시성 A, `test_negative_stock_rejected_by_db_constraint` |
| 중복 출하 차단 | `request_id` UNIQUE. 같은 요청 재전송 → 기존 결과 반환(`replayed`), 같은 번호·다른 내용 → `REQUEST_ID_CONFLICT` | 동시성 B, 시나리오 22·23단계 |
| 미검사·부적합 출하 차단 | 출하·생산투입·병합은 **상태 PASS + 검사 근거 사슬에 FAIL 없음**일 때만 | 시나리오 15·16·27·29·31·33단계 |
| 동시 출하 요청 | 실제 HTTP 서버에 20스레드 동시 요청, 출하 vs 부적합 재검사 경합 | 동시성 A·C |
| 영향받는 출하 목록 | 재검사 FAIL 응답에 `affected_shipments`, 정방향 추적 API/화면 | 시나리오 28단계, `screen_forward_M-A.png` |
| 검사 근거 제시 | 출하 시점 근거를 출하 행에 **고정 저장**(이후 상태가 바뀌어도 보존) + 현재 근거 별도 조회 | `test_shipment_evidence_is_frozen_at_ship_time`, `screen_backward_shipment1.png` |
| 재고 수불 대조표 | 모든 수량 변화는 수불부(ledger)에 기록 → LOT별 합계 = 현재고, 품목별 보존식 | `reports/inventory_reconciliation.md`, `test_reconcile_detects_tampering` |

## 제출물 위치

| 제출물 | 파일 |
|---|---|
| 추적 화면 | `static/trace.html` (실행: `cli serve`), 캡처 `reports/screen_*.png` |
| API | `api.py` (엔드포인트 목록은 파일 상단 주석) |
| 동시성 검증 | `reports/verification.md` 동시성 A·B·C, `reports/concurrency.json` |
| 재고 수불 대조표 | `reports/inventory_reconciliation.md` |

## 상태와 규칙

| 상태 | 의미 | 출하·생산투입 |
|---|---|---|
| PENDING 미검사 | 입고·생산 직후 | 불가 |
| PASS 합격 | 최근 검사 합격 | 가능 (단, 근거 사슬에 FAIL 없을 때) |
| FAIL 부적합 | 최근 검사 부적합 | 불가 |
| HOLD 보류 | 상위 LOT 이 부적합 판정됨 | 불가 |

- **분할**: 자식은 부모의 상태를 물려받는다. 남은 양은 부모에 남는다. 전량 분할도 가능하다.
- **병합**: 품목·단위·종류가 같아야 한다. 부적합이나 보류 LOT 은 섞을 수 없다. 전부 합격이면 합격, 미검사가 섞이면 미검사가 된다.
- **재검사 FAIL**: 하위 LOT(분할·병합·생산으로 파생된 모든 LOT) 중 합격·미검사인 것을 보류로 바꾸고, 이전 상태를 저장한다. 응답에 영향받은 출하 목록이 담긴다.
- **재검사 PASS**: 이 LOT 때문에 보류된 하위 LOT 을 **원래 상태로** 복원한다(미검사였으면 미검사로). 다른 상위 LOT 이 여전히 부적합이면 보류를 유지한다.
- **제품만 재검사 합격해서는 안 된다**: 제품 근거 = 제품 검사 + 투입 자재 검사. 자재가 부적합이면 제품을 출하할 수 없다(`UPSTREAM_FAIL`).
- 자재 LOT 은 직접 출하할 수 없다(`KIND_INVALID`).
- 수량은 1/1000 단위 정수로 저장해 소수 오차를 없앴다(0.1+0.1+0.1 = 0.3 정확히).

## 설계 판단 (시연 때 설명 포인트)

1. **왜 SQLite `BEGIN IMMEDIATE` 인가**: 쓰기 잠금을 트랜잭션 시작 시점에 잡는다. 그래서 "재고 확인 → 차감" 사이에 다른 요청이 끼어들 틈이 없다. 일반 `BEGIN`(DEFERRED)으로 바꾸면 동시성 검증 3개가 모두 실패한다. 이 변이 실험으로 직접 확인했다(AI_LOG 참고).
2. **request_id 는 클라이언트가 만든다**: 버튼을 두 번 누르거나 네트워크 재전송이 일어나도, 같은 번호면 출하는 1건만 생긴다.
3. **출하 근거는 출하 시점에 고정 저장한다**: 나중에 자재가 부적합으로 바뀌어도 "출하 당시에는 무엇을 근거로 합격이었는가"를 증명할 수 있다.
4. **PostgreSQL 로 옮긴다면**: `SELECT … FOR UPDATE` 행 잠금 또는 조건부 UPDATE 를 그대로 쓰면 된다. 조건부 차감과 CHECK 제약은 DB 종류와 무관하게 유지된다.

## 오류 코드 (HTTP)

`400` INVALID_QTY, MISSING_FIELD, INVALID_JSON … / `404` LOT_NOT_FOUND / `409` STATUS_BLOCKED, UPSTREAM_FAIL, INSUFFICIENT_QTY, REQUEST_ID_CONFLICT, DUPLICATE_LOT, ITEM_MISMATCH, KIND_INVALID

## 알려진 한계

- 권한/인증 없음 → 과제 10 에서 추가
- 과제 01 데이터(입고·검사 CSV)를 직접 가져오는 기능은 아직 없음. 데모는 시나리오 데이터를 쓴다.
- 시각은 서버 UTC 기록 시각이다. 실제 검사일·출하일을 따로 입력받지 않는다.
- 단일 SQLite 파일 기준이라 서버 여러 대로 확장하려면 DB 교체가 필요하다.
