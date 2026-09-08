import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple

import httpx
import yaml
from tenacity import retry, stop_after_attempt, wait_fixed

# ----------------------------------------------------------------------
# 1️⃣ 설정 모델 – 서비스 정의를 한곳에 선언
# ----------------------------------------------------------------------
class ServiceInfo(NamedTuple):
    """각 AI 에이전트(파서, 임베더, 검색, 생성 등)의 메타데이터"""
    name: str               # e.g. "parser", "embedder", "retriever", "generator"
    url: str                # base URL, e.g. "http://localhost:8001"
    timeout: float = 10.0   # 초 (재시도 시 동일하게 사용)

# ----------------------------------------------------------------------
# 2️⃣ 비동기 HTTP 호출 + 재시도 로직
# ----------------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
async def _post(
    client: httpx.AsyncClient,
    endpoint: str,
    payload: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    """POST 요청 → JSON 응답 반환. 실패 시 재시도"""
    resp = await client.post(endpoint, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

# ----------------------------------------------------------------------
# 3️⃣ 파이프라인 단계 함수 (파서, 임베더, 검색, 생성)
# ----------------------------------------------------------------------
async def run_parser(
    client: httpx.AsyncClient,
    service: ServiceInfo,
    source_path: str,
    profile: str,
    book_id: str,
) -> Dict[str, Any]:
    payload = {"path": source_path, "profile": profile, "book_id": book_id}
    return await _post(client, f"{service.url}/parse", payload, service.timeout)

async def run_embedder(
    client: httpx.AsyncClient,
    service: ServiceInfo,
    texts: List[str],
) -> List[List[float]]:
    payload = {"texts": texts}
    resp = await _post(client, f"{service.url}/embed", payload, service.timeout)
    return resp.get("vectors", [])

async def run_retriever(
    client: httpx.AsyncClient,
    service: ServiceInfo,
    query_vector: List[float],
    k: int = 5,
) -> List[Dict[str, Any]]:
    payload = {"vector": query_vector, "k": k}
    resp = await _post(client, f"{service.url}/retrieve", payload, service.timeout)
    return resp.get("hits", [])

async def run_generator(
    client: httpx.AsyncClient,
    service: ServiceInfo,
    topic: Dict[str, Any],
    context: List[Dict[str, Any]],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    payload = {
        "topic": topic,
        "context": context,
        "schema": schema,
        "json_object": True,
    }
    return await _post(client, f"{service.url}/generate", payload, service.timeout)

# ----------------------------------------------------------------------
# 4️⃣ 메인 파이프라인 – 비동기 흐름 제어
# ----------------------------------------------------------------------
async def run_pipeline(
    source_path: str,
    profile: str,
    book_id: str,
    services: List[ServiceInfo],
    notes_dir: Path,
) -> Dict[str, Any]:
    """전체 파이프라인을 한 번에 실행. 반환값은 generate_one 결과와 메타데이터."""
    # 서비스 매핑 (이름 → ServiceInfo)
    svc = {s.name: s for s in services}

    # study_lib 패키지를 import 할 수 있도록 경로 추가
    sys.path.append(str(Path(__file__).resolve().parents[1] / "study" / "study_lib"))

    async with httpx.AsyncClient() as client:
        # ① 파싱
        parsed = await run_parser(client, svc["parser"], source_path, profile, book_id)

        # ② 청크화 (로컬 유틸 함수 사용)
        from study_lib.chunk import chunk_markdown
        chunks = chunk_markdown(parsed["markdown"], book_id=book_id)

        # ③ 임베딩 (비동기 병렬)
        texts = [c["text"] for c in chunks]
        vectors = await run_embedder(client, svc["embedder"], texts)

        # ④ 인덱스 저장 (로컬 IndexStore 사용)
        from study_lib.store import IndexStore, JsonDurableSink
        store_path = notes_dir.parent / "store"
        store = IndexStore(JsonDurableSink(store_path))
        for c, v in zip(chunks, vectors):
            store.add(c, vector=tuple(v))
        store.flush()

        # ⑤ 검색 – 첫 번째 청크를 쿼리 벡터로 사용 (예시)
        query_vec = vectors[0] if vectors else []
        hits = await run_retriever(client, svc["retriever"], query_vec, k=5)

        # ⑥ 스키마 로드
        from study_lib.protocol import load_schema, validate
        schema = load_schema()

        # ⑦ 생성 (LLM)
        # 토픽 메타데이터는 간단히 만든다 – 실제 환경에서는 discover_topics 로부터 가져와야 함
        topic = {"topic_id": f"{book_id}_t1", "book_id": book_id, "title": "Demo", "kind": "exam"}
        gen_payload = await run_generator(client, svc["generator"], topic, hits, schema)

        # ⑧ 검증 (스키마)
        validate(gen_payload, schema)   # 예외 발생 시 오류

        # ⑨ 파일 출력
        note_path = notes_dir / f"{topic['topic_id']}.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(json.dumps(gen_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # ⑩ 최종 결과 반환
        return {
            "topic": topic,
            "note_path": str(note_path),
            "payload": gen_payload,
            "status": "draft",
        }

# ----------------------------------------------------------------------
# 5️⃣ 실행 스크립트 (settings.yaml 사용)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # settings.yaml 은 현재 작업 디렉터리(프로젝트 루트) 아래에 위치한다 가정한다.
    cfg_path = Path(__file__).resolve().parents[1] / "settings.yaml"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"settings.yaml not found at {cfg_path}")
    with cfg_path.open() as f:
        cfg = yaml.safe_load(f)

    svc_list = [ServiceInfo(**s) for s in cfg.get("services", [])]

    # 예시 실행 – 실제 파일 경로와 프로파일을 맞게 수정한다.
    result = asyncio.run(
        run_pipeline(
            source_path="sample.md",
            profile="text",
            book_id="demo",
            services=svc_list,
            notes_dir=Path("./notes"),
        )
    )
    print("✅ pipeline finished →", result["note_path"])