#!/usr/bin/env bash
# ============================================================
# up.sh — llama-server 시작 (OpenAI 호환 API)
#   nohup 백그라운드 실행 + 헬스체크 대기 + 접속 정보 출력
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

CONFIG="$ROOT/config.env"
LLAMA_SERVER="$ROOT/deps/llama.cpp/build/bin/llama-server"
PID_FILE="$ROOT/llama-server.pid"
LOG_FILE="$ROOT/logs/llama-server.log"

[[ -f "$CONFIG" ]] || { echo "config.env 없음 → ./init.sh 먼저 실행" >&2; exit 1; }
set -a; source "$CONFIG"; set +a

MODEL_PATH="$ROOT/models/$MODEL_FILE"
mkdir -p "$ROOT/logs"

[[ -x "$LLAMA_SERVER" ]] || { echo "llama-server 없음 → ./init.sh 먼저 실행" >&2; exit 1; }
[[ -f "$MODEL_PATH" ]] || { echo "모델 파일 없음 → ./init.sh 먼저 실행" >&2; exit 1; }

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "이미 실행 중 (PID $(cat "$PID_FILE"))"
  echo "로그: $LOG_FILE"
  exit 0
fi
rm -f "$PID_FILE"

ARGS=(
  -m "$MODEL_PATH"
  --host "$HOST"
  --port "$PORT"
  --alias "${MODEL_ALIAS:-Qwen3.6-27B}"
  --ctx-size "$CTX_SIZE"
  --threads "$THREADS"
  --threads-batch "$THREADS"
  --batch-size "$BATCH_SIZE"
  --cache-type-k "$KV_CACHE_TYPE"
  --cache-type-v "$KV_CACHE_TYPE"
  --temp "$TEMP"
  --top-p "$TOP_P"
  --top-k "$TOP_K"
  --parallel 1
)
[[ -n "${API_KEY:-}" ]] && ARGS+=(--api-key "$API_KEY")
[[ -n "${REASONING_EFFORT:-}" ]] && ARGS+=(--reasoning-effort "$REASONING_EFFORT")
if [[ -n "${SPEC_TYPE:-}" ]]; then
  ARGS+=(--spec-type "$SPEC_TYPE" --spec-draft-n-max "${SPEC_DRAFT_N_MAX:-3}")
  ARGS+=(--spec-draft-type-k "$KV_CACHE_TYPE" --spec-draft-type-v "$KV_CACHE_TYPE")
fi
[[ "${MLOCK:-}" == "on" ]] && ARGS+=(--load-mode mmap+mlock)   # --mlock은 최신 llama.cpp에서 deprecated
[[ "$MAX_TOKENS" != "-1" ]] && ARGS+=(--n-predict "$MAX_TOKENS")
if [[ -n "${LLAMA_EXTRA_ARGS:-}" ]]; then
  read -r -a EXTRA <<<"$LLAMA_EXTRA_ARGS"
  ARGS+=("${EXTRA[@]}")
fi

echo "llama-server 시작 중... (모델 로딩 1~3분 소요)"
nohup "$LLAMA_SERVER" "${ARGS[@]}" >"$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

BASE="http://$HOST:$PORT"
for _ in $(seq 1 "$HEALTH_TIMEOUT"); do
  if curl -sf "$BASE/health" >/dev/null 2>&1; then
    SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1; exit}')"
    echo
    echo "✅ 서버 준비 완료 (PID $PID)"
    echo
    echo "  API     $BASE/v1"
    echo "  웹 UI   $BASE"
    echo "  모델명  ${MODEL_ALIAS:-Qwen3.6-27B}"
    echo "  로그    $LOG_FILE"
    echo
    echo "  테스트:"
    echo "    curl $BASE/v1/models"
    echo
    echo "  원격 접속 (SSH 포워딩):"
    echo "    ssh -N -L $PORT:127.0.0.1:$PORT $(whoami)@${SERVER_IP:-<서버_IP>}"
    if [[ "${API_KEY:-}" != "" ]]; then
      echo
      echo "  API Key: $API_KEY (에이전트 설정에 입력)"
    fi
    echo
    echo "  VS Code 에이전트 설정은 README.md 참고"
    echo
    exit 0
  fi
  kill -0 "$PID" 2>/dev/null || {
    echo "❌ 프로세스가 시작 직후 종료됨. 최근 로그:" >&2
    tail -30 "$LOG_FILE" >&2
    exit 1
  }
  sleep 1
done

echo "❌ ${HEALTH_TIMEOUT}초 내 헬스체크 실패. 최근 로그:" >&2
tail -30 "$LOG_FILE" >&2
exit 1
