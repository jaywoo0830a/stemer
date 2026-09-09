"""agent/api — 웹 API (FastAPI). 동기 POST /run-plans 로 계획서 → 병렬 에이전트 → 결과.

실행 (실제 llama/Ollama 서버 — live 기본):
    uvicorn agent.api:app --host 0.0.0.0 --port 18080   # agent API 포트 = 18080
    # (myllm Script Runner 가 8000 을 쓰므로 agent 는 18080 으로 전용)

오프라인 mock (서버 없이 파이프라인/API 테스트):
    python -c "
    from agent.api import create_app
    from starlette.testclient import TestClient
    c = TestClient(create_app(live=False)); print(c.post('/run-plans',json={'plan':'[Task 1: explain] hi'}).json())
    "

create_app(live=True, rag=None, note_dir="notes")   # 실제 추론 서버 (기본)
create_app(live=False)                               # MockGateway echo

엔드포인트:
    GET   /health           역할·서버·live 모드·myllm 기동 요약
    GET   /                 사용 안내 HTML
    POST  /split-plans      {"plan": "..."} → 티켓 분해 (모델 안 부름)
    POST  /run-plans        {"plan": "...", "use_parser": true, "rag": null} → 병렬 실행
                            → notes/*.md 저장 + JSON(각 task 결과 + markdown)
                            (live+자동부팅 켜짐이면 실행 전 myllm으로 모델 기동)

동시성: httpx/ThreadPool 은 블로킹 → 이벤트 루프 executor 로 위임(루프 비차단).
요청마다 독립 Orchestrator 를 만들고 내부 스레드 풀이 worker 를 병렬 호출.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .gateway import Gateway
from .myllm_client import MyllmClient
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
        None, description="'book-store' | 'store' | 'code' | None (근거 없음)")
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
    grounded: bool = True
    grounding_note: str = ""
    judge_role: str = ""
    error_codes: List[str] = []


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
    live: Optional[bool] = None,
    registry: Optional[Registry] = None,
    rag=None,
    rag_k: int = 4,
    note_dir: str | Path | None = None,
    judge: Optional[bool] = None,     # live 일 때 LLM 판사(다른 모델 검증) 사용 여부 (기본 on)
    myllm: Optional[MyllmClient] = None,   # myllm(모델 기동) 클라이언트
    boot_on_run: Optional[bool] = None,     # /run-plans 시 자동 모델 부팅 (live+기본 True)
    mock_echo: bool = True,
) -> FastAPI:
    """FastAPI 앱.

    live: True → 실제 llama/Ollama, False → MockGateway(echo),
          None(기본) → env AGENT_MODE (mock|live, 기본 live).
    note_dir: 결과 저장 디렉터리. None → env AGENT_NOTES_DIR, 없으면 'notes'.
    judge: live 이면 다른(논리적) 모델로 답을 검증(LlmVerifier). 기본 True.
           env AGENT_JUDGE 로 판사 주소 재정의. offline(mock)에선 항상 off.
    myllm: myLLM Script Runner 클라이언트(DOC/1). None → live 이면 MyllmClient() 자동
           생성(env MYLLM_API/MYLLM_TOKEN). mock 이거나 주입 안 하면 비활성.
    boot_on_run: /run-plans 진입 시 myllm 으로 항시 모델(start_all) 부팅 여부.
           None → env AGENT_BOOT_MODELS(기본 '1'). mock/live 무관 myllm 활성일 때만.
    """
    if live is None:
        live = os.environ.get("AGENT_MODE", "live").strip().lower() != "mock"
    reg = registry or load_registry()
    note_dir = str(note_dir or os.environ.get("AGENT_NOTES_DIR", "notes"))
    Path(note_dir).mkdir(parents=True, exist_ok=True)
    use_judge = bool(judge) if judge is not None else live

    # myllm(모델 기동) — 명시 주입 우선, live 기본이면 자동 생성.
    if myllm is None and live:
        try:
            myllm = MyllmClient()
        except Exception:  # noqa: BLE001 — 설정 오류 시 비활성(추론은 기존처럼 그냥 호출)
            myllm = None
    get_boot = (boot_on_run if boot_on_run is not None
                else os.environ.get("AGENT_BOOT_MODELS", "1").strip().lower() != "0")

    if live:
        def factory(url: str, role: str) -> Gateway:  # noqa: ARG001
            return Gateway(base_url=url)
    else:
        def factory(url: str, role: str) -> MockGateway:
            return MockGateway(url, role, echo=mock_echo)

    # 판사: 생산자와 다른 모델로 (self-confirmation 방지). 역할별로 동적 선택.
    problems_verifier = None
    if live and use_judge:
        from .verify import LlmVerifier, ProblemsVerifier, JUDGE_PROBLEMS, pick_judge_server

        def verifier_factory(producer_role: str):
            try:
                judge_url = pick_judge_server(producer_role, reg)
            except ValueError:
                return None
            judge_role = judge_url.rpartition(":")[2]
            return LlmVerifier(Gateway(base_url=judge_url), judge_role=judge_role)

        # 출제(problems) 전용 판사: 한 추론 모델을 판사로.
        try:
            p_url = pick_judge_server("problems", reg)
            problems_verifier = ProblemsVerifier(
                Gateway(base_url=p_url), judge_role=p_url.rpartition(":")[2])
        except Exception:  # noqa: BLE001
            problems_verifier = None
        # JUDGE_PROBLEMS 를 참조해(코드/문서 연결) 미사용 경고 방지
        _ = JUDGE_PROBLEMS
    else:
        verifier_factory = None

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
            gateway_factory=factory, parser=parser, note_dir=note_dir,
            verifier_factory=verifier_factory,
            problems_verifier=problems_verifier)

    @app.get("/health")
    def health() -> Dict[str, Any]:
        judge_info = {}
        if use_judge and live:
            judge_info = {
                "enabled": True,
                "default": None,  # 역할별로 다를 수 있음 — 예: worker답→reasoner
                "override": os.environ.get("AGENT_JUDGE") or "(auto)",
            }
        else:
            judge_info = {"enabled": False, "note": "offline(mock) or judge disabled"}
        myllm_info = None
        if myllm is not None:
            myllm_info = {
                "configured": True,
                "base_url": myllm.base_url,
                "token_set": bool(myllm.token),
                "boot_on_run": bool(get_boot),
            }
        else:
            myllm_info = {"configured": False, "note": "myllm client disabled (mock or none)"}
        return {
            "ok": True, "mode": "live" if live else "mock",
            "roles": {name: list(r.urls) for name, r in reg.roles().items()},
            "note_dir": note_dir,
            "rag_connected": rag is not None,
            "judge": judge_info,
            "store": _store_diagnostics(),
            "myllm": myllm_info,
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
        if myllm is not None and get_boot:
            _boot_models(myllm, req.plan)
        orch = build_orchestrator(req.use_parser, req.rag)
        loop = asyncio.get_running_loop()
        results, path, stem = await loop.run_in_executor(
            None, _run_sync, orch, req.plan, req.note_stem)
        return _to_response(results, path, stem, live)

    return app


# --------------------------------------------------------------------------- #
# 헬퍼 / mock / html
# --------------------------------------------------------------------------- #
def _store_diagnostics() -> Dict[str, Any]:
    """book-store 가 붙을 store 의 청크 현황(진단). study_lib 없으면 None."""
    try:
        from .rag import describe_store
        v = describe_store()
        if v is None:
            return {"available": False, "reason": "store non-empty? unable to load", "stats": None}
        return {"available": True, "stats": v}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc), "stats": None}


def _run_sync(orch: Orchestrator, plan: str, stem: Optional[str]):
    results, path = orch.run_plan(plan, note_stem=stem, write=True)
    return results, path, (path.stem if path else (stem or "plan"))


def _boot_models(client: MyllmClient, plan: str) -> None:
    """/run-plans 진입 시 myllm 으로 항시 모델(+필요시 14B setter) 부팅.

    - 항시 셋(parser+worker+coder+reasoner) 은 start_all 로 보장 (DOC/2 on-demand).
    - plan 에 문제 출제(action=problems) 가 있으면 14B setter 를 start_heavy 로
      (상호배타 — DOC/2/3). 판사는 reasoner 폴백이므로 judge 는 부팅하지 않는다.
    - 부팅 실패는 추론을 막지 않도록 WARN 후 무시(myllm 이 이미 떠 있으면 무해).
    """
    import sys

    def warn(msg: str) -> None:
        print(f"[agent] myllm: {msg}", file=sys.stderr, flush=True)

    try:
        client.start_all()
    except Exception as exc:  # noqa: BLE001 — 비차단
        warn(f"start_all failed (continuing): {exc}")
        return
    tasks = split_plan(plan)
    if any((t.action or "").strip().lower() == "problems" for t in tasks):
        try:
            client.start_heavy("setter")   # 14B 문제생성 (상호배타)
        except Exception as exc:  # noqa: BLE001
            warn(f"start_heavy(setter) failed (continuing): {exc}")


def _to_response(results: Sequence[WorkerResult], path, stem: str,
                 live: bool) -> RunPlanResponse:
    markdown = path.read_text(encoding="utf-8") if path else ""
    tasks = [TaskOut(task=r.task, role=r.role, url=r.url, ok=r.ok,
                     output=r.output, error=r.error, sources=list(r.sources),
                     grounded=getattr(r, "grounded", True),
                     grounding_note=getattr(r, "grounding_note", ""),
                     judge_role=getattr(r, "judge_role", ""),
                     error_codes=list(getattr(r, "error_codes", ())))
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
    if name in ("store", "rag-store", "book"):   # 'store' 별칭 → 책 store 에 근거
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
<li><code>GET /health</code> 상태/서버/모드/myllm</li>
</ul></body></html>"""


app = create_app()
