# 과제 10 — 배포·권한·장애 복구

> **선택 과제: 과제 06 (LOT 양방향 추적)** 을 컨테이너로 배포한다. 쓰기(입고·출하)와 조회가 모두 있어 권한 시험에 맞다.
> LLM 시간초과 시나리오를 위해 같은 서비스에 **과제 07 질의응답**(`POST /api/assist`)을 붙였다.

## 설치·실행 (새 환경)

필요: Docker (Compose 포함). 아래는 `kdgt-assessment` 폴더 기준.

```bash
# 1) 비밀값 준비 (.env 는 저장소에 올리지 않음)
cp task10_deploy/deploy/.env.example task10_deploy/deploy/.env      # ANTHROPIC_API_KEY 채우기 (질의응답 쓸 때만)

# 2) 빌드·실행
docker compose -f task10_deploy/deploy/compose.yaml up -d --build
curl http://127.0.0.1:8010/readyz

# 3) API 키 발급 (원문은 이때 한 번만 출력, 서버에는 해시만 저장)
docker compose -f task10_deploy/deploy/compose.yaml exec app python -m task10_deploy.cli keys add --name 품질팀장 --role admin
docker compose -f task10_deploy/deploy/compose.yaml exec app python -m task10_deploy.cli keys add --name 현장조회 --role viewer

# 4) 사용
curl -H "Authorization: Bearer <조회자키>" http://127.0.0.1:8010/api/lots
```

운영 명령 (`docker compose ... exec app python -m task10_deploy.cli <명령>`): `backup`, `restore [--file]`, `stats`, `keys list|revoke`.
Compose 없이 Docker 만 쓸 때는 `deploy/demo.sh` 안의 `docker run` 줄을 참고한다.

## 완료 기준 대응

| 슬라이드 요구 | 결과 (실제 컨테이너에서 실행) | 증빙 |
|---|---|---|
| 새 환경에서 실행 가능하게 배포 | 매번 **새 볼륨**으로 이미지 빌드·기동 → 스키마 자동 생성 → readyz 200. root 아닌 사용자(uid 10001), HEALTHCHECK, 재시작 정책 | `deploy/Dockerfile`, `deploy/compose.yaml` |
| 관리자·조회자 권한 | 권한 테스트 10건 전부 기대대로. **조회자 쓰기 403**, 키 없음·틀린 키·폐기 키 401 | `reports/verification_docker.md` 권한 표 |
| 서버 측 API 키 | 서비스 키는 SHA-256 해시만 저장(원문은 발급 때 한 번). Anthropic 키는 서버 환경변수로만 존재하고 응답·로그에 나오지 않음 | `security.py` |
| 감사 로그 | 요청마다 JSON 한 줄: 시각·요청ID·key_id·역할·행위·경로·상태코드·지연. 기록 직전 비밀값 패턴 제거 | `/data/audit.log`, `GET /admin/audit`(관리자) |
| DB 중단 | DB 잠금 8초: 조회는 계속 200, 쓰기는 **2초 안에 503 + Retry-After**, 해제 후 **재시작 없이** 201 | 타임라인 "장애 A" |
| 백업 복원 | DB 파일 손상 → 조회·readyz 503(프로세스는 생존) → 백업 해시·무결성·건수 검증 후 원자적 교체 → **건수·합계 9항목 = 백업 manifest** | 타임라인 "장애 B·복원" |
| LLM 시간초과 | 무응답 LLM 서버 상대로 **정확히 제한 시간(3.0초)에 504**, 그동안 다른 API 0.0초 응답, 3회 연속 실패 후 차단기가 **즉시 503 + Retry-After** | 타임라인 "장애 C" |
| 로그에 비밀값 없음 | 감사 로그·서버 로그·키 파일·감사 API 응답 4곳 × 비밀값 4종 검색 → **0건** | 판정표 |

## 제출물 위치

| 제출물 | 파일 |
|---|---|
| 컨테이너 실행 구성 | `deploy/Dockerfile`, `deploy/compose.yaml`, `deploy/.env.example`, `../.dockerignore` |
| 설치 안내 | 이 README 위 "설치·실행" |
| 권한 테스트 | `reports/verification_docker.md` 권한 표, `tests/test_task10.py` |
| 복구 영상과 결과 | 결과: `reports/verification_docker.md` 타임라인. **영상: `deploy/demo.sh` 를 화면 녹화하며 실행** (단계마다 Enter 로 진행, 약 3분) |

```bash
bash task10_deploy/deploy/demo.sh          # 녹화용 시연 (빌드 → 권한 → 백업 → 장애 A·B·C → 감사 로그)
python -m task10_deploy.verify --runner docker   # 자동 검증 (컨테이너, 약 1분)
python -m task10_deploy.verify --runner local    # 자동 검증 (Docker 없이 로컬 프로세스)
```

## 권한 표

| 경로 | viewer | admin | 인증 없음 |
|---|:---:|:---:|:---:|
| `GET /api/...` (LOT·출하·추적·수불 대조 조회) | ✔ | ✔ | 401 |
| `POST /api/receipts·inspections·splits·merges·productions·shipments` | 403 | ✔ | 401 |
| `POST /api/assist` (질의응답, 읽기 전용) | ✔ | ✔ | 401 |
| `POST /admin/backup`, `GET /admin/audit` | 403 | ✔ | 401 |
| `GET /healthz`, `GET /readyz` | ✔ | ✔ | ✔ (데이터 없음) |

키 발급·폐기는 HTTP 로 열지 않고 서버 안에서만(`cli keys`) 한다.

## 장애 대응 설계

| 상황 | 동작 | 이유 |
|---|---|---|
| DB 잠김 | `busy_timeout` 2초 후 503 `DB_LOCKED` + `Retry-After: 5` | 요청이 30초씩 매달리지 않고 클라이언트가 재시도 시점을 앎 |
| DB 파일 손상·없음 | 503 `DB_UNAVAILABLE`, `/readyz` 503, `/healthz` 200 | 오케스트레이터가 재시작 반복 대신 '준비 안 됨'으로 트래픽만 뺌. 파일이 없을 때 **빈 DB 를 새로 만들지 않음**(장애가 '0건'으로 숨지 않게) |
| 복원 중 | `MAINTENANCE` 표시 → 모든 API 503 | 반쯤 교체된 DB 를 읽지 않게 |
| 복원 검증 | 백업 파일 SHA-256 → integrity_check → 건수·합계 = manifest → 임시 파일 복사 → `os.replace` → 다시 대조 | 손상된 백업으로 덮어쓰는 2차 사고 방지 (`test_tampered_backup_is_refused`) |
| LLM 무응답 | SDK `timeout`(기본 10초, 시험 3초), 재시도 0 → 504 `LLM_TIMEOUT` | 질의응답이 멈춰도 LOT 조회는 영향 없음 |
| LLM 연속 실패 | 3회 → 60초간 호출 생략, 즉시 503 `LLM_UNAVAILABLE` | 장애 난 외부 API 를 계속 두드리지 않음 |
| 첫 LLM 요청 | 기동 시 Agent 미리 준비 | 첫 요청도 정확히 제한 시간에 끝나게 |

**복구 목표:** 백업 시점 이후 쓰기는 복원 시 사라진다(검증에서 4건 손실로 확인). 운영에서는 백업 주기를 RPO(허용 손실 시간)에 맞춰 cron 등으로 돌려야 한다.

## 알려진 한계

- SQLite 단일 파일이라 서버 1대 기준이다. 여러 대로 늘리려면 PostgreSQL 등으로 바꾸고, 백업은 PITR(시점 복구)로 바꿔야 한다
- TLS 는 이 서비스에 없다. compose 는 127.0.0.1 에만 열고, 외부 공개는 앞단 리버스 프록시(TLS 종료)에서 한다
- 과제 06 추적 화면(HTML)은 API 키 입력 기능이 없어 이 배포에서는 API 로만 쓴다
- LLM 시간초과는 '응답하지 않는 가짜 LLM 서버'로 재현했다. 실제 Anthropic API 의 지연·장애와 같은 SDK 경로(`APITimeoutError`)를 탄다
