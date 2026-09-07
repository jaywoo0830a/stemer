#!/usr/bin/env bash
# ------------------------------------------------------------------
# gemma-4-26B-A4B (활성 4B / 총 26B, Google) 로컬 LLM 설치 (서버용)
#
# study 후처리/문제은행의 로컬 "범용 모델"(OllamaTextLLM)로 쓰기 위한 준비.
#   - 우선 Ollama 공식 라이브러리 태그를 자동 감지해 pull.
#   - 그게 없으면(Google gated / GGUF 미비) 명확한 수동 등록 안내를 낸다.
#
# 사용:
#   bash install-gemma-4-26b-a4b.sh                     # 태그 자동 감지
#   GEMMA_TAG=gemma-4-26b-a4b bash .../install-gemma-4-26b-a4b.sh  # 강제
#
# 완료 후 study 를 이 모델로 돌리려면:
#   echo 'OLLAMA_MODEL=gemma-4-26b-a4b' >> ~/projects/stemer/study/.env
#   # 도커 compose 는 OLLAMA_MODEL env 를 컨테이너로 넘긴다.
# ------------------------------------------------------------------
set -euo pipefail

MODEL_NAME="gemma-4-26B-A4B"

if ! command -v ollama >/dev/null 2>&1; then
  echo "!! 'ollama' 가 없습니다. 먼저 설치하세요:"
  echo "   curl -fsSL https://ollama.com/install.sh | sh"
  echo "   ollama serve &"
  exit 2
fi

ollama_ok() { ollama show "$1" >/dev/null 2>&1; }

TAG="${GEMMA_TAG:-}"
if [ -z "$TAG" ]; then
  # ① 공식 라이브러리에 나올 만한 후보 태그를 순서대로 시도
  for c in \
      "gemma-4-26b-a4b" \
      "gemma4:26b-a4b"  \
      "gemma:4-26b-a4b" \
      "gemma3:26b-a4b"
  do
    if ollama_ok "$c"; then
      TAG="$c"; echo "[감지] 이미 설치됨: $c"; break
    fi
    if ollama pull "$c" >/dev/null 2>&1; then
      TAG="$c"; echo "[pull OK] $c"; break
    fi
  done
else
  if ! ollama show "$TAG" >/dev/null 2>&1; then
    echo "[pull] $TAG ..."
    ollama pull "$TAG"
  fi
fi

if [ -z "$TAG" ]; then
  cat <<'EOF'

자동 pull 에 실패했습니다. gemma-4-26B-A4B는 Google의 'gated' 모델이라 공식
Ollama 라이브러리에 항상 있는 태그는 아닙니다. 두 가지 방법 중 하나로 진행하세요.

방법 A — 올라마 라이브러리에서 최신 태그 확인 후 재시도:
   브라우저: https://ollama.com/library?q=gemma  에서 정확한 태그 확인
   GEMMA_TAG=<확인된-태그> bash install-gemma-4-26b-a4b.sh

방법 B — HuggingFace GGUF 를 받아 ollama 등록(4~16GB, 일회성):
   https://huggingface.co/google/gemma-4-26B-A4B (gated → HF 계정 인증 필요)
   1) 요청자 등록:  HF 설정 → Access Tokens 에서 read 토큰  HF_TOKEN
   2) GGUF(예 q4_K_M 다운, 또는 로컬 도구로 변환) 를 받는다.
   3) Modelfile 작성:
         FROM ./gemma-4-26B-A4B-q4_k_m.gguf
         TEMPLATE "{{ if .System }}<start_of_turn>system
{{ .System }}<end_of_turn>
<start_of_turn>user
{{ .Prompt }}<end_of_turn>
<start_of_turn>model
{{ .Response }}<end_of_turn>
"
     4) ollama create gemma-4-26b-a4b -f Modelfile
     5) GEMMA_TAG=gemma-4-26b-a4b bash install-gemma-4-26b-a4b.sh
EOF
  exit 3
fi

# 간단 무결성: 짧은 프롬프트가 응답을 주는지
echo "[무결성] 짧은 테스트 1회 (최초 로드엔 수십 초 걸릴 수 있음)..."
printf 'Say ok' | timeout 180 ollama run "$TAG" >/dev/null 2>&1 \
  && echo "[성공] 모델 응답 정상." \
  || echo "[경고] 즉시 응답 실패 — 'ollama serve' 가 떠 있는지 확인 후 재시도."

cat <<EOF

완료. 로컬 '범용 LLM'으로 쓰려면:
   export OLLAMA_MODEL=$TAG
   # (권장) study/.env 에 저장: echo 'OLLAMA_MODEL=$TAG' >> ~/projects/stemer/study/.env
후처리/문제은행 CLI 예:
   bash docker/run.sh postkatex --book 미적분 --verbose
   bash docker/run.sh problembank --topic 미적분-11-3
EOF
