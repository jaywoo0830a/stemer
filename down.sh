#!/usr/bin/env bash
# ============================================================
# down.sh — llama-server 종료
#   SIGTERM → 30초 대기 → SIGKILL, 잔여 프로세스 정리
# ============================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PID_FILE="$ROOT/llama-server.pid"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "종료 신호 전송... (PID $PID)"
    kill "$PID"
    for _ in $(seq 1 30); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$PID" 2>/dev/null; then
      echo "정상 종료 안 됨 → 강제 종료"
      kill -9 "$PID"
    fi
    echo "✅ 서버 종료됨"
  else
    echo "PID 파일은 있지만 프로세스 없음 → 정리"
  fi
  rm -f "$PID_FILE"
else
  echo "PID 파일 없음"
fi

# PID 파일 없이 남은 잔여 프로세스 정리 (안전망)
if [[ -f "$ROOT/config.env" ]]; then
  set -a; source "$ROOT/config.env"; set +a
  if pgrep -f "llama-server.*${MODEL_FILE}" >/dev/null 2>&1; then
    pkill -f "llama-server.*${MODEL_FILE}"
    echo "잔여 프로세스 정리됨"
  else
    echo "실행 중인 llama-server 없음"
  fi
fi
