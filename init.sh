#!/usr/bin/env bash
# ============================================================
# init.sh — Qwen3.6-27B CPU 서버 1회성 초기화
#   1) 빌드 도구 설치 (build-essential, cmake 등)
#   2) 8GB 스왑 생성 (RAM 부족분 보완)
#   3) llama.cpp 네이티브 빌드 (AVX2)
#   4) GGUF 모델 다운로드 (~17GB, 중단해도 이어받기)
#   5) config.env 생성 (있으면 유지)
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() { printf '\n\033[1;36m[init]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[init]\033[0m %s\n' "$*" >&2; exit 1; }

CONFIG="$ROOT/config.env"
DEPS_DIR="$ROOT/deps"
MODELS_DIR="$ROOT/models"
LOGS_DIR="$ROOT/logs"
LLAMA_DIR="$DEPS_DIR/llama.cpp"
LLAMA_SERVER="$LLAMA_DIR/build/bin/llama-server"

# ------------------------------------------------------------ config
if [[ ! -f "$CONFIG" ]]; then
  cat > "$CONFIG" <<'CONFIG_EOF'
# Qwen3.6-27B CPU 추론 서버 설정
MODEL_REPO="unsloth/Qwen3.6-27B-GGUF"
MODEL_FILE="Qwen3.6-27B-Q4_K_M.gguf"
HOST="127.0.0.1"
PORT="8000"
CTX_SIZE="32768"
THREADS="8"
BATCH_SIZE="512"
KV_CACHE_TYPE="q8_0"
TEMP="0.6"
TOP_P="0.95"
TOP_K="20"
MAX_TOKENS="-1"
HEALTH_TIMEOUT="600"
LLAMA_EXTRA_ARGS=""
CONFIG_EOF
  log "config.env 생성 (기본값)"
else
  log "config.env 이미 존재 → 그대로 사용"
fi
set -a; source "$CONFIG"; set +a

# ---------------------------------------------------------- apt deps
log "1/5 시스템 패키지 확인"
MISSING=""
for t in cmake gcc g++ make; do
  command -v "$t" >/dev/null 2>&1 || MISSING="$MISSING $t"
done
if [[ -n "$MISSING" ]]; then
  echo "설치 필요:$MISSING"
  sudo apt-get update -y
  sudo apt-get install -y build-essential cmake curl wget ca-certificates
else
  echo "빌드 도구 모두 존재"
fi

# ------------------------------------------------------------- swap
log "2/5 스왑 확인/생성"
if swapon --show --noheadings | grep -q .; then
  echo "스왑 이미 활성화됨"
else
  echo "8GB 스왑 생성 중..."
  sudo fallocate -l 8G /swapfile 2>/dev/null \
    || sudo dd if=/dev/zero of=/swapfile bs=1M count=8192 status=progress
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab \
    || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf >/dev/null
  sudo sysctl -p /etc/sysctl.d/99-swappiness.conf >/dev/null
  echo "스왑 8GB 활성화 완료"
fi

# ------------------------------------------------------- llama.cpp
log "3/5 llama.cpp 빌드"
mkdir -p "$DEPS_DIR"
if [[ -x "$LLAMA_SERVER" ]]; then
  echo "llama-server 이미 존재 → 스킵"
  echo "(재빌드: rm -rf deps/llama.cpp/build 후 재실행)"
else
  echo "소스 클론 + 빌드 (8코어 기준 약 5~10분)"
  [[ -d "$LLAMA_DIR/.git" ]] || git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$LLAMA_DIR"
  cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" \
    -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON
  cmake --build "$LLAMA_DIR/build" -j"$(nproc)" --target llama-server
fi

# ---------------------------------------------------------- model
log "4/5 모델 다운로드"
mkdir -p "$MODELS_DIR"
MODEL_PATH="$MODELS_DIR/$MODEL_FILE"
URL="https://huggingface.co/$MODEL_REPO/resolve/main/$MODEL_FILE?download=true"
if [[ -f "$MODEL_PATH" ]]; then
  echo "모델 존재: $(numfmt --to=iec "$(stat -c%s "$MODEL_PATH")")"
else
  echo "다운로드: $URL"
  echo "(약 17GB — 중단해도 init.sh 재실행 시 이어받기 됨)"
  curl -L -C - --retry 5 --retry-delay 5 -o "$MODEL_PATH.part" "$URL"
  mv "$MODEL_PATH.part" "$MODEL_PATH"
fi
SIZE="$(stat -c%s "$MODEL_PATH")"
if [[ "$SIZE" -lt 15000000000 ]]; then
  die "모델 파일 크기가 비정상: $SIZE bytes"
fi
echo "모델 준비됨: $(numfmt --to=iec "$SIZE")"

# ---------------------------------------------------------- wrap-up
log "5/5 마무리"
mkdir -p "$LOGS_DIR"
chmod +x "$ROOT/init.sh" "$ROOT/up.sh" "$ROOT/down.sh" 2>/dev/null || true

echo
echo "✅ 초기화 완료. 사용 순서:"
echo "   ./up.sh      # 서버 시작 (첫 로딩 1~3분)"
echo "   ./down.sh    # 서버 종료"
