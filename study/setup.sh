#!/usr/bin/env bash
# One-time setup on the server: Docker + pipeline image + model prefetch.
# Run: bash study/setup.sh
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found — installing docker.io + compose plugin (needs sudo)."
  sudo apt-get update
  sudo apt-get install -y docker.io docker-compose-v2 || sudo apt-get install -y docker.io docker-compose-plugin
  sudo systemctl enable --now docker || true
  sudo usermod -aG docker "$USER"
  echo "NOTE: log out and back in (or run 'newgrp docker') so docker works without sudo."
fi

cd docker
[ -f .env ] || cp .env.example .env
echo "Building the pipeline image ..."
docker compose build pipeline

echo "Prefetching embedding + reranker models (one-time download, several GB) ..."
docker compose run --rm pipeline python -u tools/study.py prefetch

echo
echo "Done. Drop PDFs into: $(cd .. && pwd)/books/inbox/  (SCP/SFTP)"
echo "The scheduler picks them up automatically. Run: bash $(cd .. && pwd)/pipeline.sh watch"
echo "Logs: study/logs/pipeline.log"
