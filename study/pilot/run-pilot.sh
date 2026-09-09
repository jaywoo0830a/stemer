#!/usr/bin/env bash
# Flash 파일럿 (EXECUTION-PLAN Phase 1)
#
# 전제조건 (본인 터미널에서 직접):
#   export DEEPSEEK_API_KEY=sk-...        # 비밀값 — 반드시 직접 입력
#   export DEEPSEEK_MODEL=deepseek-flash  # 실제 모델 id(계정 콘솔 확인)
#   # (권장) 실제 임베딩:  pip install 'sentence-transformers'
#   # 그렇지 않으면 STUDY_EMBEDDER=stub 로 파이프라인만 검증 가능(검색 품질 낮음)
#
# 실행:
#   bash study/pilot/run-pilot.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/docker/lib.sh"

cd_study
require_cmd python3

REG="${STUDY_REGISTRY:-pilot/registry.json}"
STORE="${STUDY_STORE:-pilot/store}"
NOTES="${STUDY_NOTES:-pilot/notes}"
EMBEDDER="${STUDY_EMBEDDER:-auto}"
BOOK="calc"
SRC="pilot/sample-calculus.md"

cli() { python3 -m study_lib.cli "$@"; }

# idempotent 책/토픽 등록
if ! cli books list --registry "$REG" | grep -q "^$BOOK "; then
  cli books add --id "$BOOK" --title "Calculus (sample)" --subject math \
      --source "$SRC" --registry "$REG"
fi
for t in "Limit of a function:3.1" "Limit of a sequence:3.5" "Monotone convergence:3.6"; do
  title="${t%%:*}"
  section="${t##*:}"
  id=$(echo "$title" | tr 'A-Z ' 'a-z-')
  if ! cli topics list --registry "$REG" | grep -q " $id "; then
    cli topics add --book "$BOOK" --title "$title" --section "$section" --registry "$REG"
  fi
done

step "status (before)"
cli status --registry "$REG"

step "index"
cli index "$SRC" --book "$BOOK" --profile text --embedder "$EMBEDDER" \
    --registry "$REG" --store "$STORE"

step "generate"
cli generate --book "$BOOK" --registry "$REG" --store "$STORE" --notes "$NOTES"

step "notes"
ls -1 "$NOTES" 2>/dev/null || info "(no notes yet)"
