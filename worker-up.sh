#!/usr/bin/env bash
# ============================================================
# worker-up.sh — RAG 파이프라인 준비 + watcher 시작
#   이미지(study-rag)가 없으면 자동 빌드. 강제 재빌드: ./worker-up.sh --build
#   웹 UI(api/web)는 제거됨 — PDF는 SCP로 books/inbox/에 넣습니다.
#   파이프라인 watcher(study/pipeline.sh watch)는 WATCHER_ENABLE=on 일 때 백그라운드 기동.
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

cd "$DOCKER_DIR"

# 이미지가 없으면 빌드. --build 인자로 강제 재빌드 (requirements/Dockerfile 변경 시).
BUILD_FLAG=""
if [[ "${1:-}" == "--build" ]]; then
  BUILD_FLAG="--build"
elif ! docker image inspect study-rag:latest >/dev/null 2>&1; then
  echo "ℹ 이미지 없음 — 최초 빌드 시작 (수 분 소요)"
  BUILD_FLAG="--build"
fi
if [[ -n "$BUILD_FLAG" ]]; then
  docker compose build $BUILD_FLAG pipeline
fi

# 파이프라인 watcher (호스트) — WATCHER_ENABLE=on 이면 백그라운드로 기동
WATCH_SCRIPT="$ROOT/study/pipeline.sh"
WATCH_PID_FILE="$ROOT/logs/pipeline-watch.pid"
if [[ "${WATCHER_ENABLE:-on}" == "on" ]] && [[ -x "$WATCH_SCRIPT" ]]; then
  if [[ -f "$WATCH_PID_FILE" ]] && kill -0 "$(cat "$WATCH_PID_FILE")" 2>/dev/null; then
    echo "✅ 파이프라인 watcher 이미 실행 중 (PID $(cat "$WATCH_PID_FILE"))"
  else
    mkdir -p "$ROOT/logs"
    nohup bash "$WATCH_SCRIPT" watch >> "$ROOT/logs/pipeline-watch.log" 2>&1 &
    echo "$!" > "$WATCH_PID_FILE"
    echo "✅ 파이프라인 watcher 시작 (PID $(cat "$WATCH_PID_FILE")) — 로그: logs/pipeline-watch.log"
  fi
fi

# llama-server는 이제 온디맨드 (생성 단계에서만 pipeline.sh가 관리)
echo "ℹ llama-server는 생성 단계에서만 켜집니다 (LLM_MANAGED=${LLM_MANAGED:-on})"
echo "   생성 실행: bash study/pipeline.sh generate   | 전체: bash study/pipeline.sh once"
echo "로그: $ROOT/study/logs/pipeline.log"
