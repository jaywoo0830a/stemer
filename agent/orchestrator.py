"""orchestrator — 계획서 → 병렬 에이전트 → 취합 저장 (NEW-METHOD §5.2/§6).

흐름:
  1. plan 또는 tasks 리스트 입력.
  2. (필요 시) PlanParser(parser 서버) 로 티켓 정규화; 아니면 split_plan.
  3. 각 Task: RAG 로 근거 청크 조회 → role(worker/coder/reasoner) 서버 Gateway 선택
     → system/user 프롬프트 → chat. 병렬(ThreadPool) 실행.
  4. 결과를 WorkerResult 로 모아 markdown 파일(결과 디렉토리)로 저장/반환.

주입 설계:
  - registry            : role→서버 주소 (registry.Registry)
  - rag                 : retrieve(query,k)->list[Chunk] (선택; None 이면 근거 없음)
  - gateway_factory     : (url, role) -> Gateway (기본 build_gateway). 테스트용.
  - pool                : 다중 서버(worker/coder) 라운드 분배
동시성: llama-servers 는 동기 httpx 로 블로킹되므로 ThreadPoolExecutor 로 병렬화.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .gateway import Gateway, GatewayError
from .planner import PlanParser, Task, split_plan
from .registry import Registry, ServerPool
from .rag import Chunk, Retriever

ResultWriter = Callable[[str, str], Path]  # (markdown, stem) -> path


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


@dataclass(frozen=True)
class WorkerResult:
    task: int
    role: str
    url: str = ""
    output: str = ""
    error: Optional[str] = None
    sources: tuple[str, ...] = ()
    grounded: bool = True       # grounding gate 통과 여부 (틀 이탈 없음)
    grounding_note: str = ""

    @property
    def ok(self) -> bool:
        return self.output.strip() != "" and self.error is None


# 기본 writer: <root>/notes/<stem>.md (반복 실행이 같은 이름을 덮지 않게
# 실행자는 stem 에 시각을 붙여 호출하는 게 관례)
def default_writer(stem: str) -> Path:
    n = Path(os.environ.get("AGENT_NOTES_DIR", "notes"))
    n.mkdir(parents=True, exist_ok=True)
    return n / f"{stem}.md"


class Orchestrator:
    """병렬 멀티에이전트 실행기."""

    def __init__(
        self,
        *,
        registry: Optional[Registry] = None,
        rag: Optional[Retriever] = None,
        gateway_factory: Optional[Callable[[str, str], Gateway]] = None,
        rag_k: int = 4,
        context_chars: int = 6000,
        parser=None,               # PlanParser (None → 오케스트레이터는 split_plan 사용)
        max_workers: int = 6,
        note_dir: Optional[str | Path] = None,
        grounding_retries: int = 2,     # 검증 gate 재시도 (엄격 모드)
        verifier=None,                  # Tier-2 LLM 판사 인스턴스 (verify.Verifier). 정적 시.
        verifier_factory=None,          # (producer_role:str)->Verifier — 역할마다 다른 모델 판사
    ) -> None:
        self.registry = registry or Registry()
        self.pool = ServerPool(self.registry)
        self.rag = rag
        self.rag_k = rag_k
        self.context_chars = context_chars
        self.parser = parser
        self.max_workers = max(1, int(max_workers))
        self.grounding_retries = max(0, int(grounding_retries))
        self.verifier = verifier
        self._verifier_factory = verifier_factory
        self._factory = gateway_factory or _default_factory
        if note_dir is not None:
            os.environ["AGENT_NOTES_DIR"] = str(note_dir)

    # -- 진입점 1: 원문 Plan (자동 티켓화) --
    def run_plan(self, plan: str, *, note_stem: Optional[str] = None,
                 write: bool = True) -> tuple[list[WorkerResult], Optional[Path]]:
        tasks = self._plan_to_tasks(plan)
        results = self.run_tasks(tasks, note_stem=note_stem, write=write)
        return results

    def _plan_to_tasks(self, plan: str) -> list[Task]:
        if self.parser is not None:
            try:
                return self.parser.parse(plan)
            except GatewayError:
                return split_plan(plan)
        return split_plan(plan)

    # -- 진입점 2: 명시 Task list --
    def run_tasks(self, tasks: Sequence[Task], *, note_stem: Optional[str] = None,
                  write: bool = True) -> tuple[list[WorkerResult], Optional[Path]]:
        if not tasks:
            return [], None
        jobs = []

        def work(task: Task) -> WorkerResult:
            return self._run_one(task)

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(tasks))) as ex:
            jobs = list(ex.map(work, tasks))
        path: Optional[Path] = None
        if write:
            stem = note_stem or f"agent-{_utcnow()}"
            path = _write_result(jobs, stem)
        return jobs, path

    # -- 단일 티켓 실행 --
    # -- 단일 티켓 실행 (Tier1 lexical gate + Tier2 LLM 판사) --
    def _run_one(self, task: Task) -> WorkerResult:
        role = task.role or "worker"
        query = task.input.strip() or f"{task.action} {task.target}"

        chunks: list[Chunk] = []
        if self.rag is not None:
            try:
                chunks = list(self.rag.retrieve(query, k=self.rag_k))
            except Exception as exc:  # noqa: BLE001 — RAG 장애는 worker 실패로 처리
                return WorkerResult(task=task.id, role=role, error=f"RAG failed: {exc}")

        srv = self.pool.next(role)
        gw = self._factory(srv.url, role)
        from . import grounding, prompts

        qtext = task.input or task.desc
        system = prompts.system_prompt(role, task.id, len(chunks),
                                       action=task.action, target=task.target)
        original_user = prompts.user_prompt(qtext, chunks,
                                            task_action=task.action,
                                            task_target=task.target)

        last_reason = "verification failed"
        try:
            for attempt in range(1 + self.grounding_retries):
                user = original_user if attempt == 0 else grounding.correction_prompt(
                    qtext, chunks, last_reason)
                out = gw.chat(system=system, user=user, max_tokens=2000)

                # Tier-1 (무료, 네트워크 없음): 완전 drift/빈 답 조기 배제
                ok1, r1 = grounding.lexical_ok(qtext, chunks, out)
                if not ok1:
                    last_reason = r1
                    if attempt < self.grounding_retries:
                        continue

                # Tier-2 (LLM 판사): 근거 기반 참/거짓·오류·예외
                verdict = self._judge(qtext, chunks, out, role)
                if ok1 and verdict.ok and verdict.grounded:
                    return WorkerResult(task=task.id, role=role, url=srv.url,
                                        output=out, grounded=True,
                                        sources=tuple(c.source for c in chunks))
                # 실패 사유 → 재시도, 소진되면 UNGROUNDED
                last_reason = (verdict.human if not verdict.ok else
                               "not grounded: " + (verdict.reason or r1))
                if attempt < self.grounding_retries:
                    continue
                return WorkerResult(
                    task=task.id, role=role, url=srv.url, output=out,
                    grounded=False,
                    grounding_note=(
                        f"rejected x{self.grounding_retries + 1}: {last_reason}"),
                    sources=tuple(c.source for c in chunks))
        except GatewayError as exc:
            return WorkerResult(task=task.id, role=role, url=srv.url, error=str(exc))
        raise RuntimeError("unreachable")  # noqa: B904  (guard)

    def _judge(self, qtext, chunks, out: str, role: str):
        """Tier-2 평결. verifier 를 구성하면 다른(엄격한) 모델에게 심판시키고,
        없으면 Tier1(lexical) 결과로 통과(비용 0). 근거가 있는데 판사가 없으면
        엄밀도가 떨어지므로 'no judge' 사유를 적어 둔다.
        """
        if self.verifier is not None:
            return self.verifier.verify(qtext, chunks, out)
        if self._verifier_factory is not None:
            try:
                v = self._verifier_factory(role)
            except Exception:  # noqa: BLE001 — 판사 서버 부재 시 Tier1 로 폴백
                v = None
            if v is not None:
                return v.verify(qtext, chunks, out)
        from agent.verify import Verdict
        if chunks:
            return Verdict(ok=True, grounded=True, reason="no judge; Tier1 only")
        return Verdict(ok=True, grounded=True, reason="free answer (no source)")


def _default_factory(url: str, role: str) -> Gateway:
    return Gateway(base_url=url)


def _write_result(results: Sequence[WorkerResult], stem: str) -> Path:
    from . import prompts
    body = prompts.merge_results(results)
    path = default_writer(stem)
    path.write_text(_header() + body, encoding="utf-8")
    return path


def _header() -> str:
    return f"> generated {_utcnow()} by local multi-agent\n\n"


# --------------------------------------------------------------------------- #
# 고수준 run_plan — CLI/Script 에서 직접 호출하는 순수 함수.
# --------------------------------------------------------------------------- #
def run_plan(
    plan: str,
    *,
    parser: Optional[PlanParser] = None,
    rag: Optional[Retriever] = None,
    registry: Optional[Registry] = None,
    note_dir: Optional[str | Path] = None,
    note_stem: str = "agent",
) -> tuple[list[WorkerResult], Optional[Path]]:
    orch = Orchestrator(registry=registry, rag=rag, parser=parser,
                        note_dir=note_dir)
    return orch.run_plan(plan, note_stem=note_stem)
