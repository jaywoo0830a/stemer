#!/usr/bin/env bash
# study 이미지 빌드. EMBED=1 이면 실제 임베딩(bge-m3, torch) 포함.
#   bash docker/build.sh                          # 임베딩 없음 (빠른 스모크)
#   EMBED=1 bash docker/build.sh                  # 서버 실사용
#   EMBED=1 DOCLING=1 bash docker/build.sh        # + docling 풀 파싱 (복잡 레이아웃/스캔)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

cd_study
EMBED="${EMBED:-0}"
DOCLING="${DOCLING:-0}"

step "study 이미지 빌드 (EMBED=$EMBED DOCLING=$DOCLING)"
docker compose build --build-arg "EMBED=$EMBED" --build-arg "DOCLING=$DOCLING"

info "built study:latest (EMBED=$EMBED DOCLING=$DOCLING)"
