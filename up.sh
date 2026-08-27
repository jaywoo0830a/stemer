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

# 0.0.0.0 바인딩(외부/LAN 노출)은 API_KEY 필수 — 인증 없이 노출 방지
if [[ "${HOST:-127.0.0.1}" == "0.0.0.0" && -z "${API_KEY:-}" ]]; then
  echo "❌ HOST=0.0.0.0 이면서 API_KEY가 비어 있습니다." >&2
  echo "   외부/LAN 노출은 인증 필수 — config.env에서 API_KEY를 설정하세요." >&2
  exit 1
fi

# 방화벽(ufw) 개방/폐쇄 — FIREWALL_ENABLE=on + HOST=0.0.0.0 일 때만
firewall_open() {
  [[ "${FIREWALL_ENABLE:-off}" == "on" ]] || return 0
  [[ "${HOST:-127.0.0.1}" == "0.0.0.0" ]] || return 0
  local FP="${FIREWALL_PORT:-${PORT:-8000}}"
  command -v ufw >/dev/null 2>&1 || { echo "⚠ ufw 없음 — 방화벽 개방 생략"; return 0; }
  if sudo -n true 2>/dev/null; then
    sudo ufw allow "$FP"/tcp >/dev/null 2>&1 && echo "✅ 방화벽 개방: $FP/tcp" || true
  else
    echo "⚠ sudo 비밀번호 필요 — 직접 실행: sudo ufw allow $FP/tcp"
  fi
}
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
  ARGS+=(--spec-type "$SPEC_TYPE")
  # draft 계열(draft-simple / draft-mtp)에만 적용되는 파라미터
  case "$SPEC_TYPE" in
    *draft-mtp*|*draft-simple*)
      ARGS+=(--spec-draft-n-max "${SPEC_DRAFT_N_MAX:-3}")
      ARGS+=(--spec-draft-type-k "$KV_CACHE_TYPE" --spec-draft-type-v "$KV_CACHE_TYPE")
      ;;
  esac
  # draft-simple: 소형 드래프트 모델(--spec-draft-model) 필요
  if [[ "$SPEC_TYPE" == *draft-simple* ]]; then
    if [[ -z "${DRAFT_MODEL_FILE:-}" || ! -f "$ROOT/models/$DRAFT_MODEL_FILE" ]]; then
      echo "❌ draft-simple 은 드래프트 모델이 필요합니다." >&2
      echo "   config.env 에서 DRAFT_MODEL_FILE 확인 후:" >&2
      echo "     ./init.sh   # 드래프트 모델 다운로드" >&2
      exit 1
    fi
    ARGS+=(--spec-draft-model "$ROOT/models/$DRAFT_MODEL_FILE")
    [[ -n "${SPEC_DRAFT_THREADS:-}" ]] && ARGS+=(--spec-draft-threads "$SPEC_DRAFT_THREADS")
  fi
fi
[[ "${MLOCK:-}" == "on" ]] && ARGS+=(--load-mode mmap+mlock)   # --mlock은 최신 llama.cpp에서 deprecated
[[ "$MAX_TOKENS" != "-1" ]] && ARGS+=(--n-predict "$MAX_TOKENS")
if [[ -n "${LLAMA_EXTRA_ARGS:-}" ]]; then
  read -r -a EXTRA <<<"$LLAMA_EXTRA_ARGS"
  ARGS+=("${EXTRA[@]}")
fi

# MTP(draft-mtp)는 GGUF에 MTP 헤드(nextn 텐서)가 있어야 동작 — 없으면 시작 즉시 종료되므로 미리 검사
if [[ "${SPEC_TYPE:-}" == *mtp* ]]; then
  echo "MTP 헤드 확인 중... (GGUF 검사, 수 초 소요)"
  if ! grep -aFq 'nextn.' "$MODEL_PATH"; then
    echo "❌ 이 GGUF에는 MTP(Multi-Token Prediction) 헤드가 없어" >&2
    echo "   --spec-type draft-mtp 로 시작할 수 없습니다." >&2
    echo "   해결: config.env에서 SPEC_TYPE=\"\" 로 변경 후 다시 실행:" >&2
    echo "     sed -i 's|^SPEC_TYPE=.*|SPEC_TYPE=\"\"|' config.env && ./up.sh" >&2
    exit 1
  fi
fi
if [[ "${MLOCK:-}" == "on" ]] && ! ulimit -l unlimited 2>/dev/null; then
  echo "⚠ RLIMIT_MEMLOCK 상승 실패(비root) → mlock 경고 로그가 나올 수 있음 (실행은 계속됨)"
fi

firewall_open
echo "llama-server 시작 중... (모델 로딩 1~3분 소요)"
nohup "$LLAMA_SERVER" "${ARGS[@]}" >"$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

BASE="http://$HOST:$PORT"
print_startup_hints() {
  grep -q "failed to create MTP context" "$LOG_FILE" && {
    echo >&2
    echo "💡 원인: 이 GGUF에 MTP(Multi-Token Prediction) 헤드가 없는데" >&2
    echo "   SPEC_TYPE=\"draft-mtp\" 로 시작을 시도했습니다." >&2
    echo "   해결: config.env에서 SPEC_TYPE=\"\" 로 변경:" >&2
    echo "     sed -i 's|^SPEC_TYPE=.*|SPEC_TYPE=\"\"|' config.env && ./up.sh" >&2
  }
  grep -q "failed to mlock" "$LOG_FILE" && {
    echo >&2
    echo "💡 mlock 경고는 치명적이지 않습니다(가중치 잠금만 미적용, 추론은 정상)." >&2
    echo "   root로 실행 시: ulimit -l unlimited" >&2
    echo "   또는 잠금 포기: config.env에서 MLOCK=\"off\"" >&2
  }
}
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
    if [[ "$HOST" == "0.0.0.0" ]]; then
      echo "  원격 접속 (LAN 직접 HTTP):"
      echo "    http://${SERVER_IP:-<서버_IP>}:$PORT/v1"
    else
      echo "  원격 접속 (SSH 포워딩):"
      echo "    ssh -N -L $PORT:127.0.0.1:$PORT $(whoami)@${SERVER_IP:-<서버_IP>}"
    fi
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
    print_startup_hints
    firewall_close
    exit 1
  }
  sleep 1
done

echo "❌ ${HEALTH_TIMEOUT}초 내 헬스체크 실패. 최근 로그:" >&2
tail -30 "$LOG_FILE" >&2
print_startup_hints
firewall_close
exit 1
