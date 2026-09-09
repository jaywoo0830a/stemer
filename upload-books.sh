#!/usr/bin/env bash
# upload-books.sh — WSL에서 교재(PDF/DJVU)를 서버로 SCP 업로드 (업로드 전용, 간단판)
#
# 입력: ① 서버(user@host)  ② Windows/로컬 폴더  ③ 서버 업로드 폴더
#       → 인자로 주거나, 없으면 물어봅니다.
#    ./upload-books.sh "user@서버IP" 'C:\Users\me\Downloads\수학' ~/study/books/math
#    ./upload-books.sh                       # 셋 다 입력 프롬프트
#
# 참고: ./upload-books.env 에 SB_HOST/SB_KEY 를 적어두면 프롬프트를 건너뜁니다.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

# ---- env 파일(선택) 로드 ----
load_env_optional "$SCRIPT_DIR/upload-books.env"

SERVER="${1:-${SB_HOST:-}}"
KEY="${SB_KEY:-}"
LOCAL="${2:-}"
REMOTE="${3:-}"

# ---- 누락 항목 프롬프트 ----
ask SERVER "서버 (user@host)"
ask LOCAL "Windows/로컬 폴더 경로"
ask REMOTE "서버 업로드 폴더(예 ~/study/books/math)"

# 붙여넣을 때 섞여 들어온 따옴표 제거
SERVER="$(stripq "$SERVER")"
LOCAL="$(stripq "$LOCAL")"
REMOTE="$(stripq "$REMOTE")"

[[ -n "$SERVER" && -n "$LOCAL" && -n "$REMOTE" ]] \
  || die "모든 입력이 필요합니다."

# ---- Windows 경로 → WSL 경로 변환 ----
if [[ "$LOCAL" =~ ^[A-Za-z]:[/\\] || "$LOCAL" =~ ^\\\\ ]]; then
  require_cmd wslpath
  LOCAL="$(wslpath -u "$LOCAL")"
fi
[[ -d "$LOCAL" ]] || die "폴더가 없습니다: $LOCAL"

# ---- 업로드할 파일 수집 ----
mapfile -t FILES < <(find "$LOCAL" -maxdepth 1 -type f \( -iname '*.pdf' -o -iname '*.djvu' \) | sort)
if [[ ${#FILES[@]} -eq 0 ]]; then
  die "해당 폴더에 PDF/DJVU 가 없습니다: $LOCAL"
fi

step "업로드 계획: 파일 ${#FILES[@]}개 (PDF/DJVU): $LOCAL"
info "→ $SERVER:$REMOTE/"
printf '   - %s\n' "${FILES[@]}"

# ---- 서버 폴더 생성 후 SCP ----
require_cmd ssh scp
ssh ${KEY:+-i "$KEY"} "$SERVER" "mkdir -p $REMOTE"
scp ${KEY:+-i "$KEY"} "${FILES[@]}" "$SERVER:$REMOTE/"
info "완료 ✅  서버에서 인덱싱: bash docker/run.sh ingest \"$REMOTE\" --subject math --profile fast --jobs 4"

