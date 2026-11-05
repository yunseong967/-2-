# syntax=docker/dockerfile:1
# 과제 10 배포 이미지: 과제 06 LOT 추적 + 과제 07 질의응답, 권한·감사·장애 대응
# 빌드 (kdgt-assessment 폴더에서):  docker build -f task10_deploy/deploy/Dockerfile -t kdgt-lot:task10 .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    KDGT_DATA_DIR=/data KDGT_PORT=8010 KDGT_LLM_TIMEOUT=10

WORKDIR /app
COPY task10_deploy/deploy/requirements-runtime.txt /tmp/requirements.txt
# 사내 프록시 CA 가 필요한 환경에서만 --secret id=ca,src=<CA파일> 로 넘긴다 (이미지 레이어에 남지 않음)
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# root 가 아닌 전용 사용자로 실행
RUN useradd --uid 10001 --create-home kdgt && mkdir -p /data && chown kdgt:kdgt /data

COPY common/ common/
COPY task03_bom_revision/ task03_bom_revision/
COPY task06_lot_traceability/ task06_lot_traceability/
COPY task07_qa_agent/ task07_qa_agent/
COPY task10_deploy/ task10_deploy/

USER kdgt
VOLUME ["/data"]
EXPOSE 8010
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/healthz', timeout=2)" || exit 1
CMD ["python", "-m", "task10_deploy.cli", "serve"]
