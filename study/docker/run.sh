#!/usr/bin/env bash
# study CLI 를 도커로 실행하는 얇은 래퍼.
#   bash docker/run.sh status
#   bash docker/run.sh ingest /books/math --subject math --profile fast --jobs 4
#   bash docker/run.sh topics discover --book stewart
#   bash docker/run.sh generate --book stewart
set -euo pipefail
cd "$(dirname "$0")/.."

# 이미지가 없으면 (임베딩 포함) 빌드
if ! docker image inspect study:latest >/dev/null 2>&1; then
  echo "image study:latest not found -> building (EMBED=${EMBED:-1})" >&2
  EMBED="${EMBED:-1}" docker compose build
fi

docker compose run --rm study python -m study_lib.cli "$@"
