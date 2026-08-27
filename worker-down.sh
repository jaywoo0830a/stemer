#!/usr/bin/env bash
# ============================================================
# worker-down.sh — RAG 파이프라인 워커 + 웹 UI 종료 (Docker Compose)
#   컨테이너만 종료. 데이터(study/), 인덱스, 모델 캐시는 유지됩니다.
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$ROOT/study/docker"
ENV_FILE="$DOCKER_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi
UI_PORT="${UI_PORT:-8080}"

# 방화벽(ufw) 폐쇄 — UI_FIREWALL_ENABLE=on 일 때만
firewall_close() {
  [[ "${UI_FIREWALL_ENABLE:-off}" == "on" ]] || return 0
  command -v ufw >/dev/null 2>&1 || return 0
  if sudo -n true 2>/dev/null; then
    sudo ufw delete allow "$UI_PORT"/tcp >/dev/null 2>&1 && echo "🔒 방화벽 폐쇄: $UI_PORT/tcp" || true
  else
    echo "⚠ sudo 비밀번호 필요 — 직접 실행: sudo ufw delete allow $UI_PORT/tcp"
  fi
}

if [[ ! -d "$DOCKER_DIR" ]]; then
  echo "❌ $DOCKER_DIR 없음" >&2
  exit 1
fi

cd "$DOCKER_DIR"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose down
  echo "✅ worker 종료됨 (study/ 데이터·인덱스·모델 캐시는 유지)"
else
  echo "⚠ docker 없음 — 종료할 컨테이너가 없습니다"
fi

firewall_close
