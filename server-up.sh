#!/usr/bin/env bash
# agent API(FastAPI 멀티에이전트 웹) 를 도커로 띄운다.
#   bash server-up.sh
# 컨테이너: image agent-gateway:latest / container agent-gateway / host network
#   → 호스트의 llama.cpp(8081-8088)·Ollama(11434) 그대로 호출 (myllm 이 띄운 모델들).
#   API 는 http://<host>:8000 (POST /run-plans, POST /split-plans, GET /health).
#
# 환경(선택):
#   AGENT_MODE=mock   # 오프라인 echo (--live 아님) — 서버 없이 파이프라인만
#   AGENT_PARSER/WORKERS/CODERS/REASONER/EMBED   # 역할 주소 재정의
#   -f 재빌드:  docker build -f docker/agent-gateway.Dockerfile -t agent-gateway:latest .
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "❌ docker 가 없습니다. Docker 데몬이 있는 서버(예: 192.99.201.121)에서 실행하세요." >&2
  echo "   (로컬 오프라인은 서버 대신 mock: python -m uvicorn agent.api:app ...)" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "❌ docker 데몬에 연결할 수 없습니다 (데몬 시작 여부 확인)." >&2
  exit 1
fi

# 이미지 없으면 빌드
if ! docker image inspect agent-gateway:latest >/dev/null 2>&1; then
  echo "▶ image agent-gateway:latest 없음 → 빌드합니다" >&2
  docker build -f docker/agent-gateway.Dockerfile -t agent-gateway:latest .
fi

echo "▶ agent-gateway 컨테이너 기동 (mode=${AGENT_MODE:-live})" >&2
docker compose -f docker-compose.agent.yml up -d

echo "✅ http://localhost:8000 (POST /run-plans) · 중지하려면: bash server-down.sh" >&2
docker compose -f docker-compose.agent.yml ps
