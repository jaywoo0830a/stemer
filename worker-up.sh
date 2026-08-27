#!/usr/bin/env bash
# ============================================================
# worker-up.sh — RAG 파이프라인 워커 + 웹 UI 시작 (Docker Compose)
#   pipeline(인덱싱·노트생성 watcher) + api(FastAPI) + web(nginx UI)
#   이미지가 없으면 자동 빌드. 강제 재빌드는: ./worker-up.sh --build
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DOCKER_DIR="$ROOT/study/docker"
ENV_FILE="$DOCKER_DIR/.env"

command -v docker >/dev/null 2>&1 || {
  echo "❌ docker 없음 — 먼저 실행: bash study/setup.sh" >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || {
  echo "❌ docker compose 플러그인 없음 — 먼저 실행: bash study/setup.sh" >&2
  exit 1
}

[[ -d "$DOCKER_DIR" ]] || { echo "❌ $DOCKER_DIR 없음" >&2; exit 1; }
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$DOCKER_DIR/.env.example" "$ENV_FILE"
  echo "ℹ .env 생성됨: $ENV_FILE"
fi

set -a; source "$ENV_FILE"; set +a
UI_PORT="${UI_PORT:-8080}"

# 방화벽(ufw) 개방 — UI_FIREWALL_ENABLE=on 일 때만 (nginx UI 포트)
firewall_open() {
  [[ "${UI_FIREWALL_ENABLE:-off}" == "on" ]] || return 0
  command -v ufw >/dev/null 2>&1 || { echo "⚠ ufw 없음 — 방화벽 개방 생략"; return 0; }
  if sudo -n true 2>/dev/null; then
    sudo ufw allow "$UI_PORT"/tcp >/dev/null 2>&1 && echo "✅ 방화벽 개방: $UI_PORT/tcp" || true
  else
    echo "⚠ sudo 비밀번호 필요 — 직접 실행: sudo ufw allow $UI_PORT/tcp"
  fi
}

cd "$DOCKER_DIR"

# 이미지가 없으면 빌드. --build 인자로 강제 재빌드.
BUILD_FLAG=""
if [[ "${1:-}" == "--build" ]]; then
  BUILD_FLAG="--build"
elif ! docker image inspect study-rag:latest >/dev/null 2>&1; then
  echo "ℹ 이미지 없음 — 최초 빌드 시작 (수 분 소요)"
  BUILD_FLAG="--build"
fi

docker compose up -d $BUILD_FLAG

# 헬스체크: nginx 경유 API (nginx 기동에 수 초 소요)
echo "웹 UI 헬스체크 대기 중... (http://127.0.0.1:$UI_PORT/api/health)"
OK=""
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$UI_PORT/api/health" >/dev/null 2>&1; then OK=1; break; fi
  sleep 2
done
if [[ -n "$OK" ]]; then
  echo "✅ 웹 UI 정상 응답"
else
  echo "⚠ 아직 응답 없음 — 확인: docker compose logs --tail 50 api web"
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "✅ 웹 UI: http://${LAN_IP:-<서버_IP>}:$UI_PORT"

# llama-server 상태 안내 (노트 생성에 필요)
if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
  echo "✅ llama-server 정상 — 노트 생성까지 가능"
else
  echo "⚠ llama-server 꺼짐 — 인덱싱은 되지만 노트 생성은 안 됩니다 (./up.sh 먼저)"
fi
echo "로그: $ROOT/study/logs/pipeline.log"
