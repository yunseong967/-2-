#!/usr/bin/env bash
# 과제 10 복구 시연 (화면 녹화용). kdgt-assessment 폴더에서:  bash task10_deploy/deploy/demo.sh
# 단계마다 설명을 출력하고 Enter 를 기다린다. 필요: docker, curl, python3
# 옵션: SKIP_BUILD=1 (이미지 재사용), BUILD_ARGS='--network host ...' (프록시 환경)
set -euo pipefail
IMG=kdgt-lot:task10; APP=kdgt-demo; LLM=kdgt-demo-llm; NET=kdgt-demo-net; VOL=kdgt-demo-data; URL=http://127.0.0.1:8010
step() { echo; echo "━━━ $1"; read -r -p "  (Enter) " _; }
run() { echo "\$ $*"; "$@"; echo; }
api() { local key=$1; shift; curl -s -w '  ← HTTP %{http_code} (%{time_total}s)\n' -H "Authorization: Bearer $key" -H 'Content-Type: application/json' "$@"; echo; }
cleanup() { docker rm -f $APP $LLM >/dev/null 2>&1 || true; docker network rm $NET >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup; docker volume rm $VOL >/dev/null 2>&1 || true

step "1. 이미지 빌드 후 새 볼륨으로 컨테이너 실행 (LLM 은 일부러 응답하지 않는 가짜 서버로 연결)"
# 프록시 환경이면 BUILD_ARGS 로 빌드 옵션 전달, 이미 빌드했으면 SKIP_BUILD=1
if [ -z "${SKIP_BUILD:-}" ]; then run docker build -q ${BUILD_ARGS:-} -f task10_deploy/deploy/Dockerfile -t $IMG .; fi
docker network create $NET >/dev/null
docker run -d --name $LLM --network $NET $IMG python -m task10_deploy.cli slow-llm --port 9999 --delay 60 >/dev/null
run docker run -d --name $APP --network $NET -p 127.0.0.1:8010:8010 -v $VOL:/data -e KDGT_LLM_TIMEOUT=3 \
    -e ANTHROPIC_API_KEY=sk-ant-demo-not-a-real-key -e KDGT_ANTHROPIC_BASE_URL=http://$LLM:9999 $IMG
until curl -sf $URL/healthz >/dev/null; do sleep 0.5; done; run curl -s $URL/readyz

step "2. 키 발급 — 원문은 이때 한 번만 보이고 서버에는 해시만 저장"
ADMIN=$(docker exec $APP python -m task10_deploy.cli keys add --name 품질팀장 --role admin | python3 -c 'import sys,json;print(json.load(sys.stdin)["api_key"])')
VIEW=$(docker exec $APP python -m task10_deploy.cli keys add --name 현장조회 --role viewer | python3 -c 'import sys,json;print(json.load(sys.stdin)["api_key"])')
run docker exec $APP cat /data/keys.json

step "3. 권한: 키 없음 401 / 조회자 쓰기 403 / 관리자 쓰기 201"
api none $URL/api/lots
api "$VIEW" -X POST $URL/api/receipts -d '{"lot_id":"M-1","item":"SUS304 6t","qty":1000,"unit":"KG"}'
api "$ADMIN" -X POST $URL/api/receipts -d '{"lot_id":"M-1","item":"SUS304 6t","qty":1000,"unit":"KG"}'
api "$ADMIN" -X POST $URL/api/inspections -d '{"lot_id":"M-1","result":"PASS","inspector":"QC김"}'

step "4. 백업 (건수·합계 manifest 포함)"
api "$ADMIN" -X POST $URL/admin/backup -d '{}'

step "5. 장애 A — DB 를 8초간 잠금: 조회는 계속, 쓰기는 2초 안에 503 + Retry-After, 풀리면 재시작 없이 회복"
docker exec -d $APP python -m task10_deploy.cli db-lock --seconds 8; sleep 1
api "$VIEW" $URL/api/lots/M-1
api "$ADMIN" -X POST $URL/api/receipts -d '{"lot_id":"M-2","item":"SS400","qty":5,"unit":"KG"}'
echo "  잠금 해제 대기..."; sleep 8
api "$ADMIN" -X POST $URL/api/receipts -d '{"lot_id":"M-2","item":"SS400","qty":5,"unit":"KG"}'

step "6. 장애 B — DB 파일 손상: 조회 503, 프로세스는 살아 있음(healthz 200)"
run docker exec $APP python -m task10_deploy.cli corrupt
api "$VIEW" $URL/api/lots
run curl -s -o /dev/null -w 'healthz %{http_code}\n' $URL/healthz

step "7. 복원 — 백업 해시·무결성·건수 검증 후 원자적 교체 → 건수·합계 일치"
run docker exec $APP python -m task10_deploy.cli restore
api "$VIEW" $URL/api/lots

step "8. 장애 C — LLM 무응답: 3초에 504, 그동안 다른 API 정상, 3회 실패 후 차단기 즉시 503"
for i in 1 2 3; do api "$VIEW" -X POST $URL/api/assist -d '{"question":"M-1 입고일은?"}'; done
api "$VIEW" -X POST $URL/api/assist -d '{"question":"M-1 입고일은?"}'

step "9. 감사 로그 — 키 원문·Anthropic 키 없이 key_id 와 역할만"
docker exec $APP tail -n 5 /data/audit.log
echo; echo "비밀값 검색:"; docker exec $APP sh -c "grep -c -e '$ADMIN' -e '$VIEW' -e 'sk-ant-demo' /data/audit.log /data/keys.json || true"
step "끝 — 컨테이너 정리"
