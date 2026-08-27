#!/usr/bin/env bash
# ============================================================
# down.sh — llama-server 종료
#   SIGTERM → 30초 대기 → SIGKILL, 잔여 프로세스 정리
# ============================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PID_FILE="$ROOT/llama-server.pid"

# config.env 로딩 (없으면 기본값 사용)
if [[ -f "$ROOT/config.env" ]]; then
  set -a; source "$ROOT/config.env"; set +a
fi

# 방화벽(ufw) 폐쇄 — FIREWALL_ENABLE=on + HOST=0.0.0.0 일 때만
firewall_close() {
  [[ "${FIREWALL_ENABLE:-off}" == "on" ]] || return 0
  [[ "${HOST:-127.0.0.1}" == "0.0.0.0" ]] || return 0
  local FP="${FIREWALL_PORT:-${PORT:-8000}}"
  command -v ufw >/dev/null 2>&1 || return 0
  if sudo -n true 2>/dev/null; then
    sudo ufw delete allow "$FP"/tcp >/dev/null 2>&1 && echo "🔒 방화벽 폐쇄: $FP/tcp" || true
  else
    echo "⚠ sudo 비밀번호 필요 — 직접 실행: sudo ufw delete allow $FP/tcp"
  fi
}

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
PATTERN="llama-server"
if [[ -n "${MODEL_FILE:-}" ]]; then
  PATTERN="llama-server.*(${MODEL_FILE}|${DRAFT_MODEL_FILE:-})"
fi
if pgrep -f "$PATTERN" >/dev/null 2>&1; then
  pkill -f "$PATTERN"
  echo "잔여 프로세스 정리됨"
else
  echo "실행 중인 llama-server 없음"
fi

# 서버 중지 후 방화벽 포트 폐쇄
firewall_close
