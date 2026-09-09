# agent API(멀티에이전트 오케스트레이터 웹) 도커 이미지.
# 빌드 컨텍스트는 repo 루트(stemer/) — agent/ + study/study_lib(RAG 근거) 모두 COPY.
#   docker build -f docker/agent-gateway.Dockerfile -t agent-gateway:latest .
#
# 실행(호스트 network) — 컨테이너가 호스트의 llama.cpp(8081-8088)·Ollama(11434)를 봄:
#   docker compose -f docker-compose.agent.yml up -d
# (myllm 이 띄운 parser/worker/coder/reasoner/Ollama 는 호스트 프로세스이므로 host network 필수)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    AGENT_NOTES_DIR=/app/notes

WORKDIR /app

# 의존성 (웹 API 전용 — 무겁지 않게: fastapi/uvicorn/pydantic/httpx)
COPY agent/requirements-api.txt ./requirements-api.txt
RUN pip install --no-cache-dir -r requirements-api.txt \
    && rm -f requirements-api.txt

# 코드 (구조를 소스와 동일하게 유지 → agent.rag 가 study_lib 를 lazy import 로 찾음)
COPY agent ./agent
COPY study/study_lib ./study/study_lib

# host network 로 127.0.0.1:8081-8088 / 11434 을 그대로 사용
# (역할 주소는 기본값 또는 env AGENT_* 로 오버라이드)
# agent API(우리 앱) 포트 = 8080. 외부 LLM 서비스(myllm Script Runner) 는 18080.
CMD ["python", "-m", "uvicorn", "agent.api:app", "--host", "0.0.0.0", "--port", "8080"]
