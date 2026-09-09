#!/usr/bin/env bash
# ============================================================
# lib.sh — 루트 운영 스크립트 공통 라이브러리
#   server-up.sh / server-down.sh / upload-books.sh 가 공유.
#
#   사용법 (스크립트 최상단, set -euo pipefail 다음):
#     SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#     source "$SCRIPT_DIR/lib.sh"
#
#   이 lib 를 source 하는 스크립트가 어느 디렉터리에서 실행되어도
#   git 저장소 루트(stemer/) 를 STEMER_ROOT 로 자동 탐지한다.
# ============================================================

# --- 저장소 루트 탐지 -------------------------------------------------
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if git -C "$_LIB_DIR" rev-parse --show-toplevel >/dev/null 2>&1; then
  STEMER_ROOT="$(git -C "$_LIB_DIR" rev-parse --show-toplevel)"
else
  # git 이 없으면(전송된 스냅숏 등) lib 가 있는 곳을 루트로 간주.
  STEMER_ROOT="$_LIB_DIR"
fi

# --- 로그 (TTY 인 경우에만 컬러) --------------------------------
if [[ -t 1 ]]; then
  _C_GREEN=$'\e[1;32m'
  _C_YELLOW=$'\e[1;33m'
  _C_RED=$'\e[1;31m'
  _C_CYAN=$'\e[1;36m'
  _C_RESET=$'\e[0m'
else
  _C_GREEN= _C_YELLOW= _C_RED= _C_CYAN= _C_RESET=
fi

log()  { printf '[stemer] %s\n' "$*"; }
info() { printf '%s[info] %s%s\n' "$_C_GREEN" "$*" "$_C_RESET"; }
warn() { printf '%s[warn] %s%s\n' "$_C_YELLOW" "$*" "$_C_RESET" >&2; }
die()  { printf '%s[error] %s%s\n' "$_C_RED" "$*" "$_C_RESET" >&2; exit 1; }
step() { printf '%s[step] %s%s\n' "$_C_CYAN" "$*" "$_C_RESET"; }

# --- 명령 가드 ------------------------------------------------------
# require_cmd docker ssh curl
require_cmd() {
  local name
  for name in "$@"; do
    command -v "$name" >/dev/null 2>&1 \
      || die "필요한 명령 '$name' 이 없습니다."
  done
}

# docker 데몬 사용 가능한지 (명령 포함)
docker_available() {
  require_cmd docker
  docker info >/dev/null 2>&1 \
    || die "도커 데몬에 연결할 수 없습니다 (데몬 시작 여부 확인)."
}

# --- 경로 ----------------------------------------------------------
# 저장소 루트로 이동 (이 시점 이후 경로는 루트 기준)
cd_root() {
  cd "$STEMER_ROOT" || die "저장소 루트로 이동 실패: $STEMER_ROOT"
}

# --- env 파일 -------------------------------------------------------
# load_env_optional [--required] <path>
#  - 파일이 존재하면 set -a 로 등록(source)해 내보내기 환경으로 만든다.
#  - --required 이고 파일이 없으면 에러.
load_env_optional() {
  local required=0
  [[ "$1" == "--required" ]] && { required=1; shift; }
  local path="$1"
  if [[ -f "$path" ]]; then
    set -a
    # shellcheck disable=SC1090
    source -- "$path"
    set +a
    info "env 로드: $path"
  elif [[ "$required" -eq 1 ]]; then
    die "설정 파일이 없습니다: $path"
  fi
}

# --- 입력 유틸 ------------------------------------------------------
# 사용자 입력 프롬프트 (누락 시에만) — ask <변수명> <메시지>
ask() {
  local -n _v="$1"
  local msg="$2"
  if [[ -z "$_v" ]]; then
    read -r -p "$msg: " _v || die "입력이 취소되었습니다."
  fi
}

# 붙여넣을 때 섞여 들어온 따옴표 제거
stripq() {
  local v="$1"
  v="${v#\"}"; v="${v#\'}"
  v="${v%\"}"; v="${v%\'}"
  printf '%s' "$v"
}