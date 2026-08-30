#!/usr/bin/env bash
# ============================================================
# pipeline.sh — 함수형 파이프라인 오케스트레이터 (HOST)
#
# RAG 인덱싱(Phase A/B)과 LLM 생성(Phase C)을 직렬로 실행하고, 생성
# 단계에서만 llama-server(up.sh)를 띄웠다가 내립니다(down.sh).
# → 같은 머신에서 RAG 파이프라인과 LLM이 동시에 도는 일이 구조적으로 없음.
#
# Usage:
#   ./pipeline.sh watch                  # index → (pending 없으면) generate, 반복
#   ./pipeline.sh once                   # index → (pending 없으면) generate
#   ./pipeline.sh index [--force]        # Phase A+B만 (LLM off)
#   ./pipeline.sh generate [--book X]    # Phase C만 (LLM up/down 자동)
#   ./pipeline.sh note <topic> [옵션]    # 주제 1개 즉시 생성 (LLM up/down 자동)
#   ./pipeline.sh prefetch
#   ./pipeline.sh status
#   ./pipeline.sh stop                   # watch 중단 (PID 파일 기반)
#
# env (study/docker/.env):
#   WATCH_INTERVAL_S   반복 주기(초)
#   LLM_MANAGED=on     (기본) up.sh/down.sh로 llama-server 생명주기 관리
#   LLM_MANAGED=off    직접 ./up.sh ./down.sh 로 관리 (이 스크립트는 LLM 안 건드림)
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root
DOCKER_DIR="$ROOT/study/docker"
ENV_FILE="$DOCKER_DIR/.env"

cd "$ROOT"

[[ -f "$ENV_FILE" ]] || { echo "❌ $ENV_FILE 없음 — ./worker-up.sh 먼저 실행" >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a

WATCH_INTERVAL_S="${WATCH_INTERVAL_S:-300}"
LLM_MANAGED="${LLM_MANAGED:-on}"
WATCH_PID_FILE="$ROOT/logs/pipeline-watch.pid"

log() { echo "[pipeline.sh] $*"; }

container_run() {  # python tools/study.py <command> [args...]
  docker compose -f "$DOCKER_DIR/docker-compose.yml" run --rm -T pipeline \
    python -u tools/study.py "$@"
}

llm_up()   { [[ "$LLM_MANAGED" == "on" ]] && bash "$ROOT/up.sh"; }
llm_down() { [[ "$LLM_MANAGED" == "on" ]] && bash "$ROOT/down.sh"; }

pending_index() {
  container_run status | sed -n 's/^pending_index=\([0-9]*\).*/\1/p'
}

cmd_index()    { llm_down; container_run index "$@"; }
cmd_generate() { llm_up; container_run generate "$@"; llm_down; }
cmd_note()     { llm_up; container_run note "$@"; llm_down; }

cmd_once() {
  cmd_index "$@"
  if [[ "$(pending_index)" == "0" ]]; then
    cmd_generate "$@"
  else
    log "인덱싱이 남아 있어 생성 단계를 건너뜁니다 (선 RAG → 후 생성)."
  fi
}

cmd_watch() {
  mkdir -p "$ROOT/logs"
  echo "$$" > "$WATCH_PID_FILE"
  trap 'rm -f "$WATCH_PID_FILE"' EXIT
  log "watcher 시작: WATCH_INTERVAL_S=${WATCH_INTERVAL_S}s, LLM_MANAGED=${LLM_MANAGED}"
  while true; do
    cmd_once "$@"
    sleep "$WATCH_INTERVAL_S"
  done
}

case "${1:-}" in
  watch)    shift; cmd_watch "$@" ;;
  once)     shift; cmd_once "$@" ;;
  index)    shift; cmd_index "$@" ;;
  generate) shift; cmd_generate "$@" ;;
  note)     shift; cmd_note "$@" ;;
  prefetch) shift; llm_down; container_run prefetch "$@" ;;
  status)   shift; container_run status "$@" ;;
  stop)
    if [[ -f "$WATCH_PID_FILE" ]]; then
      kill "$(cat "$WATCH_PID_FILE")" 2>/dev/null || true
      rm -f "$WATCH_PID_FILE"
      log "watcher 중지."
    else
      log "watcher 실행 중이 아닙니다."
    fi
    ;;
  *)
    echo "usage: $0 watch|once|index|generate|note|prefetch|status|stop [args...]" >&2
    echo "  note <topic> [--book X --section Y ...]  # generate ONE topic (LLM up/down auto)" >&2
    exit 1 ;;
esac
