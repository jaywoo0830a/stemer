#!/usr/bin/env bash
# study 이미지 빌드. EMBED=1 이면 실제 임베딩(bge-m3, torch) 포함.
#   bash docker/build.sh          # 임베딩 없음 (빠른 스모크)
#   EMBED=1 bash docker/build.sh  # 서버 실사용
set -euo pipefail
cd "$(dirname "$0")/.."
EMBED="${EMBED:-0}"
docker compose build --build-arg "EMBED=$EMBED"
echo "built study:latest (EMBED=$EMBED)"
