#!/usr/bin/env bash
# study CLI 를 도커로 실행하는 얇은 래퍼.
#   bash docker/run.sh status
#   bash docker/run.sh ingest /books/math --subject math --profile fast --jobs 4
#   bash docker/run.sh topics discover --book stewart
#   bash docker/run.sh generate --book stewart
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

cd_study
ensure_study_image
run_study_cli "$@"
