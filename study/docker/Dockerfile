# Study-note RAG pipeline image (CPU only).
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# build-essential: a few wheels still need a C toolchain at install time.
# libgl1/libglib2.0-0 + libxcb*: required by the opencv build that docling's
# tableformer imports (missing libxcb.so.1 crashes ingestion in slim images).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libgl1 libglib2.0-0 \
        libxcb1 libxcb-icccm4 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 \
        libxkbcommon0 libxkbcommon-x11-0 \
        libxrender1 libsm6 libxext6 libxfixes3 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only torch + torchvision FIRST, from the CPU wheel index, so
# downstream packages reuse them. Installing both in one call keeps their
# versions in sync — mixing PyPI's CUDA-built torchvision with CPU torch
# breaks with "operator torchvision::nms does not exist".
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# /data is the mounted study/ directory (see docker-compose.yml).
WORKDIR /data

# Run-once phase runner — study/pipeline.sh passes the phase as an argument
# (index|generate|prefetch|status|...) via `docker compose run --rm pipeline`.
CMD ["python", "-u", "tools/study.py", "status"]
