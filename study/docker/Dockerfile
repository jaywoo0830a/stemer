# study — 교재 RAG 공장 (v3) 도커 이미지
#
# 빌드(임베딩 없이, 로컬 스모크): docker build -f docker/Dockerfile -t study:latest .
# 빌드(실제 임베딩 포함, 서버):   docker build --build-arg EMBED=1 -f docker/Dockerfile -t study:latest .
# 빌드(+docling 스캔/복잡 레이아웃): docker build --build-arg EMBED=1 --build-arg DOCLING=1 ...
FROM python:3.12-slim

ARG EMBED=0
ARG DOCLING=0
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STUDY_REGISTRY=/data/registry.json \
    STUDY_STORE=/data/store \
    STUDY_NOTES=/data/notes

WORKDIR /app

# 의존성 레이어 (코드 변경과 분리)
# 서버는 CPU 전용 → torch 를 CPU 인덱스로 먼저 설치(작고 빠름, CUDA 제외).
#   EMBED=0: pypdf 만 (빠른 스모크/개발)
#   EMBED=1: bge-m3 임베딩 포함 (최초 1회는 수 분 소요)
#   DOCLING=1: docling 풀 파싱 포함 (매우 무거움 — 복잡 레이아웃/스캔본 전용)
#   docling 은 레이아웃 모델에 torchvision 이 필요 → docling 설치 전에
#   torch+torchvision 을 CPU 인덱스에서 먼저 설치해 PyPI CUDA 빌드와 충돌 방지.
COPY requirements.txt requirements-embed.txt requirements-docling.txt ./
RUN pip install --no-cache-dir -r requirements.txt pytest \
    && if [ "$EMBED" = "1" ]; then \
         pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
         && pip install --no-cache-dir -r requirements-embed.txt; fi \
    && if [ "$DOCLING" = "1" ]; then \
         pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
         && pip install --no-cache-dir -r requirements-docling.txt \
          && pip install --no-cache-dir --force-reinstall opencv-python-headless; fi \
          && pip install --no-cache-dir bitsandbytes

# 코드/구성/테스트
COPY study_lib ./study_lib
COPY config ./config
COPY guides ./guides
COPY tests ./tests
COPY pyproject.toml ./

# 데이터는 호스트에서 bind: ./books -> /books(입력), ./data -> /data(registry/store/notes)
CMD ["python", "-m", "study_lib.cli", "status"]
