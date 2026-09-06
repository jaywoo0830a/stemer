#!/usr/bin/env bash
# upload-books.sh — WSL에서 Windows/리눅스 경로의 교재(PDF)를 SCP 로 서버에 올리는 편의 래퍼.
#
# 설정: 환경변수 또는 ./upload-books.env (예제: upload-books.env.example)
#   SB_HOST=user@192.168.0.10     # 필수 (업로드 시)
#   SB_KEY=~/.ssh/id_ed25519      # 선택 (키 인증). 없으면 비밀번호 입력
#   SB_ROOT=~/study/books         # 서버 books 루트 (기본)
#   SB_PULL_SRC=~/study/data/notes  # --pull 시 가져올 서버 폴더
#
# 사용 예:
#   ./upload-books.sh 'C:\Users\me\Downloads\math-books'            # Windows 경로 자동 변환
#   ./upload-books.sh /mnt/c/Users/me/Downloads/math-books phys     # 서브폴더 지정(→ ~/study/books/phys)
#   ./upload-books.sh -n 'C:\...'                                   # 건조 실행(목록만)
#   ./upload-books.sh --pull 'C:\Users\me\study-notes'              # 서버 notes 를 로컬로 받기
set -euo pipefail

# ---- 설정 로드 (env 우선, 없으면 파일) ----
if [[ -f ./upload-books.env ]]; then
  set -a; # shellcheck disable=SC1091
  source ./upload-books.env
  set +a
elif [[ -f "$HOME/.config/study/upload-books.env" ]]; then
  set -a; # shellcheck disable=SC1091
  source "$HOME/.config/study/upload-books.env"
  set +a
fi

SB_HOST="${SB_HOST:-}"
SB_KEY="${SB_KEY:-}"
SB_ROOT="${SB_ROOT:-~/study/books}"     # 서버 기준 (원격에서 ~ 확장됨)
SB_PULL_SRC="${SB_PULL_SRC:-~/study/data/notes}"

# ---- 인자 파싱 ----
DRY=0
MODE=push
if [[ "${1:-}" == "-n" || "${1:-}" == "--dry-run" ]]; then DRY=1; shift || true; fi
if [[ "${1:-}" == "--pull" ]]; then MODE=pull; shift || true; fi
SRC_RAW="${1:-}"
SUBDIR="${2:-math}"     # 업로드 시 서버 서브폴더 (예: math)

[[ -z "$SRC_RAW" ]] && { echo "usage: $0 [-n] [--pull] <source-path> [subdir]" >&2; exit 2; }

# ---- Windows 경로 → WSL 경로 변환 ----
resolve() {
  local p="$1"
  if [[ "$p" =~ ^[A-Za-z]:[/\\] || "$p" =~ ^\\\\ ]]; then
    if command -v wslpath >/dev/null 2>&1; then
      wslpath -u "$p"
    else
      echo "error: Windows 경로인데 wslpath 를 찾을 수 없습니다." >&2
      return 1
    fi
  else
    printf '%s' "$p"
  fi
}

if [[ "$MODE" == "push" ]]; then
  SRC="$(resolve "$SRC_RAW")"
  [[ -d "$SRC" ]] || { echo "error: source is not a directory: $SRC" >&2; exit 1; }

  # PDF(+옵션 확장자) 수집
  mapfile -t FILES < <(find "$SRC" -maxdepth 1 -type f \( -iname '*.pdf' -o -iname '*.djvu' \) | sort)
  if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "no PDF/DJVU files in $SRC" >&2
    exit 1
  fi

  echo "== upload plan =="
  echo "  source : $SRC (${#FILES[@]} files)"
  echo "  target : ${SB_HOST:-'(host 미설정)'}:$SB_ROOT/$SUBDIR/"
  [[ "$DRY" -eq 1 ]] && { printf '  - %s\n' "${FILES[@]}"; echo "(dry-run — 종료)"; exit 0; }

  [[ -z "$SB_HOST" ]] && { echo "error: SB_HOST 미설정 (upload-books.env 참고)" >&2; exit 1; }

  ssh ${SB_KEY:+-i "$SB_KEY"} "$SB_HOST" "mkdir -p \"$SB_ROOT/$SUBDIR\""
  printf '  - %s\n' "${FILES[@]}"
  scp ${SB_KEY:+-i "$SB_KEY"} -r "${FILES[@]}" "$SB_HOST:$SB_ROOT/$SUBDIR/"
  echo "done. 서버에서: bash docker/run.sh ingest \"$SB_ROOT/$SUBDIR\" --subject $SUBDIR --profile fast --jobs 4"
else
  DEST="$(resolve "$SRC_RAW")"
  mkdir -p "$DEST"
  echo "== pull plan =="
  echo "  from : ${SB_HOST:-'(host 미설정)'}:$SB_PULL_SRC/"
  echo "  to   : $DEST"
  [[ "$DRY" -eq 1 ]] && { echo "(dry-run — 종료)"; exit 0; }
  [[ -z "$SB_HOST" ]] && { echo "error: SB_HOST 미설정" >&2; exit 1; }
  scp ${SB_KEY:+-i "$SB_KEY"} -r "$SB_HOST:$SB_PULL_SRC/" "$DEST/"
  echo "done."
fi
