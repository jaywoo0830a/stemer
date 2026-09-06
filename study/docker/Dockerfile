# study — 교재 RAG 공장 (v3) 도커 이미지
#
# 빌드(임베딩 없이, 로컬 스모크): docker build -f docker/Dockerfile -t study:latest .
# 빌드(실제 임베딩 포함, 서버):   docker build --build-arg EMBED=1 -f docker/Dockerfile -t study:latest .
FROM python:3.12-slim

ARG EMBED=0
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STUDY_REGISTRY=/data/registry.json \
    STUDY_STORE=/data/store \
    STUDY_NOTES=/data/notes

WORKDIR /app

# 의존성 레이어 (코드 변경과 분리)
COPY requirements.txt requirements-embed.txt ./
RUN pip install --no-cache-dir -r requirements.txt pytest \
    && if [ "$EMBED" = "1" ]; then pip install --no-cache-dir -r requirements-embed.txt; fi

# 코드/구성/테스트
COPY study_lib ./study_lib
COPY config ./config
COPY guides ./guides
COPY tests ./tests
COPY pyproject.toml ./

# 데이터는 호스트에서 bind: ./books -> /books(입력), ./data -> /data(registry/store/notes)
CMD ["python", "-m", "study_lib.cli", "status"]
