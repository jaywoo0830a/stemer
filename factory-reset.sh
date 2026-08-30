#!/usr/bin/env bash
# ============================================================
# factory-reset.sh — 업로드한 PDF만 남기고 완전 초기 상태로
#
# 삭제: registry(books/topics/docs), index/ (rag.db + chroma),
#       books/markdown/ (파싱 캐시), books/figures/, notes/, problems/, logs/
# 유지: books/inbox/*.pdf + books/processed/*.pdf (→ inbox로 이동),
#       AGENTS.md, templates/warmup.md, HF 모델 캐시, 소스코드/설정
#
# 실행 후 books/inbox/ 의 PDF를 `study.py index`(또는 watcher)로 다시
# 전부 인덱싱하면 됩니다. (TOPICS.md의 주제 목록은 초기 스켈레톤으로 초기화됨)
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DOCKER_DIR="$ROOT/study/docker"
WATCH_PID_FILE="$ROOT/logs/pipeline-watch.pid"

command -v docker >/dev/null 2>&1 || {
  echo "❌ docker 없음 — 먼저 실행: bash study/setup.sh" >&2
  exit 1
}

echo "== factory-reset =="
echo "삭제: registry(index/topic), index/, books/markdown/, books/figures/, notes/, problems/, logs/"
echo "유지: 업로드한 PDF (books/inbox/ + books/processed/), AGENTS.md, templates/, 모델 캐시"
echo

# ① 파이프라인 watcher 중지 (동시 실행으로 DB/인덱스가 꼬이는 것 방지)
if [[ -f "$WATCH_PID_FILE" ]] && kill -0 "$(cat "$WATCH_PID_FILE")" 2>/dev/null; then
  kill "$(cat "$WATCH_PID_FILE")" 2>/dev/null || true
  rm -f "$WATCH_PID_FILE"
  echo "✅ 파이프라인 watcher 중지"
fi
# 수동으로 도는 index/generate 프로세스도 정리 (정리되지 않은 게 있으면)
pkill -f "study.py (index|generate)" 2>/dev/null || true

# ② 확인
read -r -p "정말 초기화할까요? (업로드 PDF는 유지) [y/N] " ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
  echo "취소됨 — 아무것도 변경하지 않았습니다."
  exit 1
fi

# ③ 컨테이너에서 reset-all --keep-pdfs 실행 (DB 테이블 드랍 + 파생 데이터 삭제)
echo "초기화 실행 중 ..."
docker compose -f "$DOCKER_DIR/docker-compose.yml" run --rm pipeline \
  python -u tools/study.py reset-all --yes --keep-pdfs

echo
echo "== factory-reset 완료 =="
echo "업로드한 PDF가 books/inbox/ 에 그대로 있습니다."
echo "인덱싱: bash study/pipeline.sh index   | 자동: ./worker-up.sh (watcher 재기동)"
echo "llama-server는 건드리지 않았습니다 (필요시 ./down.sh 로 정리)"
