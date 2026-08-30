#!/usr/bin/env bash
# ============================================================
# worker-down.sh — RAG 파이프라인 watcher 중지 + 컨테이너 종료
#   데이터(study/), 인덱스, 모델 캐시는 유지됩니다.
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$ROOT/study/docker"
ENV_FILE="$DOCKER_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi

if [[ ! -d "$DOCKER_DIR" ]]; then
  echo "❌ $DOCKER_DIR 없음" >&2
  exit 1
fi

# 파이프라인 watcher (호스트) 중지
WATCH_PID_FILE="$ROOT/logs/pipeline-watch.pid"
if [[ -f "$WATCH_PID_FILE" ]]; then
  kill "$(cat "$WATCH_PID_FILE")" 2>/dev/null || true
  rm -f "$WATCH_PID_FILE"
  echo "✅ 파이프라인 watcher 중지"
fi

cd "$DOCKER_DIR"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose down
  echo "✅ worker 종료됨 (study/ 데이터·인덱스·모델 캐시는 유지)"
else
  echo "⚠ docker 없음 — 종료할 컨테이너가 없습니다"
fi
