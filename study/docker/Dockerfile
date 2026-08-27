# Study-note RAG pipeline image (CPU only).
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# build-essential: a few wheels still need a C toolchain at install time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only torch first so downstream packages reuse it.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# /data is the mounted study/ directory (see docker-compose.yml).
WORKDIR /data

CMD ["python", "-u", "tools/pipeline.py", "--watch"]
