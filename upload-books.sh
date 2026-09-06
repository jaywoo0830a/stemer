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

# ---- env 파일(선택) 로드 ----
if [[ -f ./upload-books.env ]]; then
  set -a; # shellcheck disable=SC1091
  source ./upload-books.env
  set +a
fi

SERVER="${1:-$SB_HOST}"
KEY="${SB_KEY:-}"
LOCAL="${2:-}"
REMOTE="${3:-}"

# ---- 누락 항목 프롬프트 ----
ask() { local -n _v="$1"; local msg="$2"; if [[ -z "$_v" ]]; then read -r -p "$msg: " _v; fi; }
ask SERVER "서버 (user@host)            "
[[ -z "$LOCAL" ]] && read -r -p "Windows/로컬 폴더 경로     " LOCAL
[[ -z "$REMOTE" ]] && read -r -p "서버 업로드 폴더(예 ~/study/books/math): " REMOTE

# 붙여넣을 때 섞여 들어온 따옴표 제거
stripq() { local v="$1"; v="${v#\"}"; v="${v#\'}"; v="${v%\"}"; v="${v%\'}"; printf '%s' "$v"; }
SERVER="$(stripq "$SERVER")"
LOCAL="$(stripq "$LOCAL")"
REMOTE="$(stripq "$REMOTE")"

[[ -z "$SERVER" || -z "$LOCAL" || -z "$REMOTE" ]] && { echo "모든 입력이 필요합니다." >&2; exit 2; }

# ---- Windows 경로 → WSL 경로 변환 ----
if [[ "$LOCAL" =~ ^[A-Za-z]:[/\\] || "$LOCAL" =~ ^\\\\ ]]; then
  if command -v wslpath >/dev/null 2>&1; then
    LOCAL="$(wslpath -u "$LOCAL")"
  else
    echo "error: Windows 경로인데 wslpath 가 없습니다." >&2; exit 1
  fi
fi
[[ -d "$LOCAL" ]] || { echo "error: 폴더가 없습니다: $LOCAL" >&2; exit 1; }

# ---- 업로드할 파일 수집 ----
mapfile -t FILES < <(find "$LOCAL" -maxdepth 1 -type f \( -iname '*.pdf' -o -iname '*.djvu' \) | sort)
if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "해당 폴더에 PDF/DJVU 가 없습니다: $LOCAL" >&2; exit 1
fi

echo "== 업로드 계획 =="
echo "  파일 ${#FILES[@]}개 (PDF/DJVU): $LOCAL"
echo "  → $SERVER:$REMOTE/"
printf '   - %s\n' "${FILES[@]}"

# ---- 서버 폴더 생성 후 SCP ----
ssh ${KEY:+-i "$KEY"} "$SERVER" "mkdir -p $REMOTE"
scp ${KEY:+-i "$KEY"} "${FILES[@]}" "$SERVER:$REMOTE/"
echo "완료 ✅  서버에서 인덱싱: bash docker/run.sh ingest \"$REMOTE\" --subject math --profile fast --jobs 4"

