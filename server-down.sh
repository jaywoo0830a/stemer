#!/usr/bin/env bash
# agent API 도커 컨테이너를 내린다. (스크립트로 지운 이미지·데이터는 그대로 유지)
#   bash server-down.sh
# 추가: 컨테이너만 정지·제거(bind 볼륨/notes 는 보존). 이미지 제거는
#   docker image rm agent-gateway:latest
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

require_cmd docker

step "agent-gateway 컨테이너 정지/제거"
docker compose -f docker-compose.agent.yml down

info "done. (notes 는 ./agent-notes 에 남아 있음 — 이미지/데이터 유지)"
