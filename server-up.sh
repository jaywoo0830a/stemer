#!/usr/bin/env bash
# agent API(FastAPI 멀티에이전트 웹) 를 도커로 띄운다.
#   bash server-up.sh
# 컨테이너: image agent-gateway:latest / container agent-gateway / host network
#   → 호스트의 llama.cpp(8081-8088)·Ollama(11434) 그대로 호출 (myllm 이 띄운 모델들).
#   API 는 http://<host>:8080 (POST /run-plans, POST /split-plans, GET /health).
#
# 환경(선택):
#   AGENT_MODE=mock   # 오프라인 echo (--live 아님) — 서버 없이 파이프라인만
#   AGENT_PARSER/WORKERS/CODERS/REASONER/EMBED   # 역할 주소 재정의
#   MYLLM_API / MYLLM_TOKEN                      # 모델기동(DOC/1) 클라이언트 (기본 127.0.0.1:18080)
#   AGENT_BOOT_MODELS=0                          # /run-plans 자동 모델 부팅 끔
#   -f 재빌드:  docker build -f docker/agent-gateway.Dockerfile -t agent-gateway:latest .
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

docker_available
cd_root

IMAGE="agent-gateway:latest"
COMPOSE_FILE="docker-compose.agent.yml"

# 이미지 없으면 빌드
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  step "image $IMAGE 없음 → 빌드합니다"
  docker build -f docker/agent-gateway.Dockerfile -t "$IMAGE" .
fi

step "agent-gateway 컨테이너 기동 (mode=${AGENT_MODE:-live})"
docker compose -f "$COMPOSE_FILE" up -d

info "http://localhost:8080 (POST /run-plans) · 중지하려면: bash server-down.sh"
docker compose -f "$COMPOSE_FILE" ps
