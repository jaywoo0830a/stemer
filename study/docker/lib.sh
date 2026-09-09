#!/usr/bin/env bash
# ============================================================
# docker/lib.sh — study 모듈 도커 빌드/실행 공통 라이브러리
#   build.sh / run.sh / pilot/run-pilot.sh 가 공유.
#
#   사용법 (스크립트 최상단, set -euo pipefail 다음):
#     source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
#
#   STUDY_ROOT 를 study/ 로 설정한다. study/는 독립 패키지이므로
#   루트 lib.sh 에 의존하지 않는다(로그/가드만 자체 보유).
# ============================================================

# --- study 루트 탐지 (이 파일 위치 = study/docker/lib.sh) ----------
STUDY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- 로그 (TTY 인 경우에만 컬러) --------------------------------
if [[ -t 1 ]]; then
  _S_GREEN=$'\e[1;32m'
  _S_YELLOW=$'\e[1;33m'
  _S_RED=$'\e[1;31m'
  _S_CYAN=$'\e[1;36m'
  _S_RESET=$'\e[0m'
else
  _S_GREEN= _S_YELLOW= _S_RED= _S_CYAN= _S_RESET=
fi

log()  { printf '[study] %s\n' "$*"; }
info() { printf '%s[info] %s%s\n' "$_S_GREEN" "$*" "$_S_RESET"; }
warn() { printf '%s[warn] %s%s\n' "$_S_YELLOW" "$*" "$_S_RESET" >&2; }
die()  { printf '%s[error] %s%s\n' "$_S_RED" "$*" "$_S_RESET" >&2; exit 1; }
step() { printf '%s[step] %s%s\n' "$_S_CYAN" "$*" "$_S_RESET"; }

# --- 명령 가드 ------------------------------------------------------
require_cmd() {
  local name
  for name in "$@"; do
    command -v "$name" >/dev/null 2>&1 \
      || die "필요한 명령 '$name' 이 없습니다."
  done
}

# --- 경로 ----------------------------------------------------------
cd_study() {
  cd "$STUDY_ROOT" || die "study/ 로 이동 실패: $STUDY_ROOT"
}

# --- study 이미지 가드 ----------------------------------------------
# 이미지가 없으면 (임베딩 포함) 빌드한다.
ensure_study_image() {
  require_cmd docker
  if ! docker image inspect study:latest >/dev/null 2>&1; then
    step "image study:latest 없음 → 빌드 (EMBED=${EMBED:-1})"
    EMBED="${EMBED:-1}" docker compose build
  fi
}

# --- study CLI 실행 --------------------------------------------------
# run_study_cli <CLI 인자...>
run_study_cli() {
  require_cmd docker
  docker compose run --rm study python -m study_lib.cli "$@"
}