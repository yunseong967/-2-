# 과제 10 검증 결과 — 실행기 `docker`

- 재실행: `python -m task10_deploy.verify --runner docker`
- 컨테이너: 사용자 `kdgt`(root 아님), 재시작 정책 `unless-stopped`, 이미지 234.7MB, 매 실행 새 볼륨(새 환경)

## 판정

| 결과 | 항목 | 근거 |
|---|---|---|
| PASS | 새 환경에서 기동 (빈 볼륨 → 스키마 생성 → 준비 완료) | healthz 200, readyz 200 |
| PASS | 권한 테스트 10건 (조회자 쓰기 거부 포함) | 전부 기대대로 (조회자 쓰기 403, 키 없음 401, 관리자 쓰기 201) |
| PASS | 장애 A: DB 잠금 → 쓰기 503(빠른 실패), 조회 유지, 해제 후 자동 회복 | 조회 200, 쓰기 503 DB_LOCKED 2.0s 만에 응답, 해제 후 201 |
| PASS | 장애 B: DB 손상 → 503, 백업 복원 후 건수·합계 일치 | 손상 시 조회 503·readyz 503·healthz 200 / 복원 후 {'lots.count': 11, 'lots.qty_m_sum': 1611000, 'shipments.count': 4, 'shipments.qty_m_sum': 5000, 'inspections.count': 8, 'ledger.count': 23, 'ledger.qty_delta_m_sum': 1611000, 'events.count': 22, 'lot_links.count': 8} = 백업 {'lots.count': 11, 'lots.qty_m_sum': 1611000, 'shipments.count': 4, 'shipments.qty_m_sum': 5000, 'inspections.count': 8, 'ledger.count': 23, 'ledger.qty_delta_m_sum': 1611000, 'events.count': 22, 'lot_links.count': 8} / 복원 후 조회 200, 추적 200 / 백업 이후 쓰기 4건은 손실(RPO = 마지막 백업) |
| PASS | 장애 C: LLM 시간초과 → 504, 다른 API 영향 없음, 3회 후 차단기 | 1회 504 3.0s (제한 3.0s), 그 사이 조회 200 0.00s, 2·3회 [504, 504], 4회 503 LLM_UNAVAILABLE 0.00s |
| PASS | 재시작 후 데이터·키 유지, 폐기 키 거부 | 재시작 후 조회 200 LOT 11개, 폐기 키 401 |
| PASS | 감사 로그 상태코드가 실제 응답과 일치 (예상치 못한 500 없음) | 500 0건, 업무 거부 409 13건이 409 로 기록됨 |
| PASS | 로그·키 파일에 비밀값 없음 | 검사 4곳 × 비밀값 4종 → 노출 0건; 감사 로그 65줄 (역할 ['admin', 'viewer'], 상태코드 [200, 201, 401, 403, 409, 503, 504]); keys.json 은 해시만 |

## 권한 테스트

| 시험 | 요청 | 기대 | 실제 | 판정 |
|---|---|---:|---:|---|
| 키 없이 조회 | GET /api/lots | 401 | 401 | 일치 |
| 잘못된 키로 조회 | GET /api/lots | 401 | 401 | 일치 |
| 조회자 조회 | GET /api/lots | 200 | 200 | 일치 |
| 조회자 쓰기(입고) | POST /api/receipts | 403 | 403 | 일치 |
| 조회자 쓰기(출하) | POST /api/shipments | 403 | 403 | 일치 |
| 조회자 백업 실행 | POST /admin/backup | 403 | 403 | 일치 |
| 조회자 감사 로그 조회 | GET /admin/audit | 403 | 403 | 일치 |
| 관리자 쓰기(입고) | POST /api/receipts | 201 | 201 | 일치 |
| 관리자 감사 로그 조회 | GET /admin/audit?n=5 | 200 | 200 | 일치 |
| 인증 없는 헬스체크 | GET /healthz | 200 | 200 | 일치 |

## 장애·복구 타임라인 (경과 초)

| 경과(s) | 단계 | 한 일 | 결과 |
|---:|---|---|---|
| 2.82 | 기동 | 새 데이터 볼륨으로 서비스 시작 | healthz 200, readyz 200 |
| 4.66 | 권한 | 권한 테스트 표 실행 | 10/10 기대대로 |
| 4.84 | 데이터 | 과제 06 시나리오 38단계를 관리자 키로 적재 | 정답 일치 |
| 4.85 | 백업 | 관리자 POST /admin/backup | 201 lot-20261105T062022302538Z.db 건수·합계 {'lots.count': 11, 'lots.qty_m_sum': 1611000, 'shipments.count': 4, 'shipments.qty_m_sum': 5000, 'inspections.count': 8, 'ledger.count': 23, 'ledger.qty_delta_m_sum': 1611000, 'events.count': 22, 'lot_links.count': 8} |
| 5.51 | 데이터 | 백업 이후 입고 3건 추가 | lots 11 → 14 |
| 6.06 | 장애 A | DB 배타 잠금 8초 시작 (쓰기 중단) |  |
| 8.08 | 장애 A | 잠금 중 조회 / 쓰기 | 조회 200 (0.00s), 쓰기 503 DB_LOCKED (2.01s, Retry-After 5) |
| 14.13 | 장애 A | 잠금 해제 후 같은 쓰기 재시도 (재시작 없음) | 201 |
| 15.26 | 장애 B | DB 파일 앞 4KB 훼손 (디스크 손상 재현) |  |
| 15.27 | 장애 B | 손상 상태 조회 / readyz / healthz | 503 DB_UNAVAILABLE, readyz 503, healthz 200 (프로세스 생존) |
| 15.83 | 복원 | 최신 백업 검증(해시·integrity·manifest) 후 원자적 교체 | match=True {'lots.count': 11, 'lots.qty_m_sum': 1611000, 'shipments.count': 4, 'shipments.qty_m_sum': 5000, 'inspections.count': 8, 'ledger.count': 23, 'ledger.qty_delta_m_sum': 1611000, 'events.count': 22, 'lot_links.count': 8} |
| 18.86 | 장애 C | LLM 무응답 서버 상대로 질의 (제한 3.0s) | 504 LLM_TIMEOUT 3.01s / 그 사이 조회 200 0.00s |
| 24.88 | 장애 C | 연속 실패 3회 후 4번째 질의 | 503 LLM_UNAVAILABLE 0.00s (차단기 열림, Retry-After 61) |
| 36.77 | 재시작 | 컨테이너/프로세스 재시작 | 조회 200, 폐기 키 401 |

## 감사 로그 예시 (마지막 6줄, 키 원문 없음)

```
{"ts": "2026-11-05T06:20:36.314+00:00", "request_id": "69afd229cfdb", "client": "172.18.0.1", "method": "POST", "path": "/api/assist", "action": "assist", "key_id": "k002", "role": "viewer", "status": 504, "latency_ms": 3011.6, "detail": {"error": "LLM_TIMEOUT"}}
{"ts": "2026-11-05T06:20:39.324+00:00", "request_id": "e081cceaf594", "client": "172.18.0.1", "method": "POST", "path": "/api/assist", "action": "assist", "key_id": "k002", "role": "viewer", "status": 504, "latency_ms": 3006.1, "detail": {"error": "LLM_TIMEOUT"}}
{"ts": "2026-11-05T06:20:42.333+00:00", "request_id": "69b8165d0b54", "client": "172.18.0.1", "method": "POST", "path": "/api/assist", "action": "assist", "key_id": "k002", "role": "viewer", "status": 504, "latency_ms": 3006.4, "detail": {"error": "LLM_TIMEOUT"}}
{"ts": "2026-11-05T06:20:42.336+00:00", "request_id": "5a6b1f5c010c", "client": "172.18.0.1", "method": "POST", "path": "/api/assist", "action": "assist", "key_id": "k002", "role": "viewer", "status": 503, "latency_ms": 0.3, "detail": {"error": "LLM_UNAVAILABLE"}}
{"ts": "2026-11-05T06:20:54.224+00:00", "request_id": "3d56bd5384d0", "client": "172.18.0.1", "method": "GET", "path": "/api/lots", "action": "read", "key_id": "k002", "role": "viewer", "status": 200, "latency_ms": 30.4, "detail": null}
{"ts": "2026-11-05T06:20:54.229+00:00", "request_id": "3fe29203514a", "client": "172.18.0.1", "method": "GET", "path": "/api/lots", "action": "read", "key_id": null, "role": null, "status": 401, "latency_ms": 0.4, "detail": {"error": "UNAUTHENTICATED"}}
```
