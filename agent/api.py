"""agent/api — 웹 API (FastAPI). 동기 POST /run-plans 로 계획서 → 병렬 에이전트 → 결과.

실행 (실제 llama/Ollama 서버 — live 기본):
    uvicorn agent.api:app --host 0.0.0.0 --port 8000

오프라인 mock (서버 없이 파이프라인/API 테스트):
    python -c "
    from agent.api import create_app
    from starlette.testclient import TestClient
    c = TestClient(create_app(live=False)); print(c.post('/run-plans',json={'plan':'[Task 1: explain] hi'}).json())
    "

create_app(live=True, rag=None, note_dir="notes")   # 실제 추론 서버 (기본)
create_app(live=False)                               # MockGateway echo

엔드포인트:
    GET   /health           역할·서버·live 모드 요약
    GET   /                 사용 안내 HTML
    POST  /split-plans      {"plan": "..."} → 티켓 분해 (모델 안 부름)
    POST  /run-plans        {"plan": "...", "use_parser": true, "rag": null} → 병렬 실행
                            → notes/*.md 저장 + JSON(각 task 결과 + markdown)

동시성: httpx/ThreadPool 은 블로킹 → 이벤트 루프 executor 로 위임(루프 비차단).
요청마다 독립 Orchestrator 를 만들고 내부 스레드 풀이 worker 를 병렬 호출.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .gateway import Gateway
from .orchestrator import Orchestrator, WorkerResult
from .planner import PlanParser, split_plan
from .registry import Registry, load_registry

# --------------------------------------------------------------------------- #
# 요청/응답 모델
# --------------------------------------------------------------------------- #
class SplitPlanRequest(BaseModel):
    plan: str = Field(..., min_length=1, description="사용자 계획서(마크다운)")


class RunPlanRequest(SplitPlanRequest):
    note_stem: Optional[str] = Field(
        None, description="결과 파일 stem (없으면 시각 자동 생성)")
    rag: Optional[str] = Field(
        None, description="'book-store' | 'code' | None (기본 근거 없음)")
    use_parser: bool = Field(
        True, description="True면 parser(8081)로 티켓 정규화, False면 로컬 split")


class TaskOut(BaseModel):
    task: int
    role: str
    url: str = ""
    ok: bool
    output: str = ""
    error: Optional[str] = None
    sources: List[str] = []


class RunPlanResponse(BaseModel):
    stem: str
    note_path: str
    markdown: str
    total: int
    ok_count: int
    tasks: List[TaskOut]
    mode: str


# --------------------------------------------------------------------------- #
# 앱 팩토리
# --------------------------------------------------------------------------- #
def create_app(
    *,
    live: bool = True,
    registry: Optional[Registry] = None,
    rag=None,
    rag_k: int = 4,
    note_dir: str | Path = "notes",
    mock_echo: bool = True,
) -> FastAPI:
    """FastAPI 앱. live=True → 실제 llama/Ollama, False → MockGateway(echo)."""
    reg = registry or load_registry()
    note_dir = str(note_dir)
    Path(note_dir).mkdir(parents=True, exist_ok=True)

    if live:
        def factory(url: str, role: str) -> Gateway:  # noqa: ARG001
            return Gateway(base_url=url)
    else:
        def factory(url: str, role: str) -> MockGateway:
            return MockGateway(url, role, echo=mock_echo)

    app = FastAPI(title="agent — local multi-agent orchestrator",
                  version="0.1.0", description=__doc__)

    def build_orchestrator(use_parser: bool, use_rag: Optional[str]) -> Orchestrator:
        parser = None
        if use_parser and live:
            try:
                parser = PlanParser(Gateway(reg.role("parser").base_url))
            except Exception:  # noqa: BLE001 — parser 미기동 → 로컬 split 폴백
                parser = None
        rag_eff = rag
        if rag_eff is None and use_rag:
            rag_eff = _make_rag_from_name(use_rag, reg, live)
        return Orchestrator(
            registry=reg, rag=rag_eff, rag_k=rag_k,
            gateway_factory=factory, parser=parser, note_dir=note_dir)

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {
            "ok": True, "mode": "live" if live else "mock",
            "roles": {name: list(r.urls) for name, r in reg.roles().items()},
            "note_dir": note_dir,
            "rag_connected": rag is not None,
        }

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        return _index_html()

    @app.post("/split-plans")
    def split_plans(req: SplitPlanRequest) -> Dict[str, Any]:
        try:
            tasks = split_plan(req.plan)
            return {"ok": True, "tasks": [asdict(t) for t in tasks],
                    "count": len(tasks)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "tasks": [], "count": 0}

    @app.post("/run-plans")
    async def run_plans(req: RunPlanRequest):
        orch = build_orchestrator(req.use_parser, req.rag)
        loop = asyncio.get_running_loop()
        results, path, stem = await loop.run_in_executor(
            None, _run_sync, orch, req.plan, req.note_stem)
        return _to_response(results, path, stem, live)

    return app


# --------------------------------------------------------------------------- #
# 헬퍼 / mock / html
# --------------------------------------------------------------------------- #
def _run_sync(orch: Orchestrator, plan: str, stem: Optional[str]):
    results, path = orch.run_plan(plan, note_stem=stem, write=True)
    return results, path, (path.stem if path else (stem or "plan"))


def _to_response(results: Sequence[WorkerResult], path, stem: str,
                 live: bool) -> RunPlanResponse:
    markdown = path.read_text(encoding="utf-8") if path else ""
    tasks = [TaskOut(task=r.task, role=r.role, url=r.url, ok=r.ok,
                     output=r.output, error=r.error, sources=list(r.sources))
             for r in results]
    return RunPlanResponse(
        stem=stem, note_path=str(path) if path else "", markdown=markdown,
        total=len(results), ok_count=sum(1 for r in results if r.ok),
        tasks=tasks, mode="live" if live else "mock")


def _make_rag_from_name(name: str, reg: Registry, live: bool):
    """'book-store'/'code' → retriever. offline/설정 오류면 None(요청은 계속)."""
    if not live:
        return None
    if name == "book-store":
        from .rag import StudyStoreRetriever
        try:
            embed = reg.role("embed")
            return StudyStoreRetriever.load(
                store_dir=None, embed_base=embed.base_url,
                embed_model=(embed.model or "qwen2.5:3b"))
        except Exception:  # noqa: BLE001
            return None
    if name == "code":
        from .rag import CodeIndex
        return CodeIndex()
    return None


class MockGateway:
    """offline — 실제 서버 없이 echo 반환 (파이프라인/API 검증용)."""

    def __init__(self, url: str, role: str, *, echo: bool = True) -> None:
        self.url, self.role, self.echo = url, role, echo

    def chat(self, *, system: str, user: str, max_tokens: int = 2000,
             temperature: float = 0.0, model: str | None = None) -> str:
        q = user.split("QUESTION / TICKET INPUT:")[-1].split("REFERENCE CONTEXT")[0]
        q = q.strip().rstrip(".")
        return f"({self.role} echo) {q}" if self.echo else f"({self.role} @ {self.url})"

    def chat_json(self, **kw):  # noqa: ARG002
        from .gateway import GatewayError
        raise GatewayError("MockGateway has no JSON parser (live parser only)")


def _index_html() -> str:
    return """<html><body><h2>agent · local multi-agent API</h2>
<ul>
<li><code>POST /run-plans</code> <code>{"plan":"[Task 1: explain] ..."}</code></li>
<li><code>POST /split-plans</code> <code>{"plan":"..."}</code></li>
<li><code>GET /health</code> 상태/서버/모드</li>
</ul></body></html>"""


app = create_app()
