"""orchestrator 계약 — plan→(RAG)→병렬 worker/coder/reasoner → markdown 결과."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.gateway import Gateway
from agent.orchestrator import Orchestrator, WorkerResult
from agent.planner import Task, split_plan
from agent.registry import Registry
from agent.tests._fakes import FakeRetriever, FakeTransport
import time as _t


def _factory_with_tracker(tracker: list, transports: dict):
    """url 별 Gateway(가짜 transport) 생성 + (url,role) 기록."""
    def make(url: str, role: str):
        tracker.append((url, role))
        tr = transports.get(url)
        if tr is None:
            tr = FakeTransport(chat_reply=f"({role} @ {url}) reply")
            transports[url] = tr
        return Gateway(base_url=url, transport=tr)
    return make


def test_run_plan_parallel_and_aggregated(tmp_path):
    reg = Registry()
    tracker, transports = [], {}
    rag = FakeRetriever()
    orch = Orchestrator(registry=reg, rag=rag,
                        gateway_factory=_factory_with_tracker(tracker, transports),
                        note_dir=str(tmp_path))
    tasks = split_plan("""\
[Task 1: explain] why integral sin(wx) zero symmetric
[Task 2: proof] orthogonality of fourier basis
[Task 3: fix] boundary indexing in explicit euler step
""")
    results, path = orch.run_tasks(tasks, note_stem="plan1")

    assert len(results) == 3
    assert all(r.ok for r in results)
    assert path is not None and path.name == "plan1.md"
    txt = path.read_text(encoding="utf-8")
    # 네 worker 각각 결과가 파일에 취합됐는지
    assert "Task 1" in txt and "Task 2" in txt and "Task 3" in txt

    # 결과 source는 RAG 근거 코드에서 왔는지 — 근거가 주입됨을 확인
    # (transport 가 context 를 가진 user 를 받았는지 일부 확인)
    any_body = [c["body"] for c in transports[reg.role("worker").base_url].calls
                if c["url"].endswith("/v1/chat/completions")]
    assert any_body, "worker gateway must have been called"
    joined = " ".join(str(b.get("messages", "")) for b in any_body)
    assert "REFERENCE CONTEXT" in joined or "calc" in joined or "SOURCE calc" in joined


def test_roles_map_to_right_server():
    reg = Registry()
    tracker, transports = [], {}
    orch = Orchestrator(registry=reg, gateway_factory=_factory_with_tracker(tracker, transports))
    tasks = [
        Task(id=1, action="fix", input="index bug", role="coder"),
        Task(id=2, action="proof", input="prove x", role="reasoner"),
        Task(id=3, action="summary", input="summarize book", role="worker"),
    ]
    orch.run_tasks(tasks, write=False)
    roles = {r for _, r in tracker}
    assert roles == {"coder", "reasoner", "worker"}
    # coder 풀(8086/8087) 중 하나로 가는지
    coder_urls = {u for u, r in tracker if r == "coder"}
    assert any("8086" in u or "8087" in u for u in coder_urls)


def test_run_tasks_empty():
    orch = Orchestrator(note_dir="/tmp/x")
    res, path = orch.run_tasks([])
    assert res == [] and path is None


def test_worker_error_captured_not_raised():
    # transport 를 chat 장애(400)로
    class BoomTransport(FakeTransport):
        def post_text(self, url, body, timeout):
            from agent.gateway import GatewayError
            raise GatewayError("HTTP 500 nope")

    reg = Registry()
    orch = Orchestrator(
        registry=reg,
        gateway_factory=lambda url, role: Gateway(base_url=url, transport=BoomTransport()),
    )
    res, _ = orch.run_tasks([Task(id=1, action="explain", input="q", role="worker")],
                            write=False)
    assert res[0].error and not res[0].ok


def test_time_parallelism_better_than_serial(tmp_path):
    """sleep_fake_transport 로 3 worker 동시성(병렬) 검증 — 순차보다 빨라야."""
    reg = Registry()

    class SleepTransport(FakeTransport):
        def __init__(self, url):
            super().__init__(chat_reply=f"({url}) s")
            self._url = url
        def post_text(self, url, body, timeout):
            _t.sleep(0.15)
            return {"id": "x", "choices": [{"message": {"role": "assistant",
                    "content": f"ok {self._url}"}, "finish_reason": "stop"}]}

    orch = Orchestrator(registry=reg, max_workers=3,
                        gateway_factory=lambda url, role: Gateway(base_url=url,
                        transport=SleepTransport(url)))
    t0 = _t.monotonic()
    orch.run_tasks([Task(id=i, action="explain", input="q", role="worker")
                    for i in range(3)], write=False)
    dt = _t.monotonic() - t0
    # 0.15*3 순차면 ~0.45s 인데 병렬은 ~0.15-0.3s
    assert dt < 0.4


def test_default_factory_uses_gateway_type():
    orch = Orchestrator(registry=Registry())
    gw = orch._factory("http://127.0.0.1:8081", "parser")
    assert isinstance(gw, Gateway)


# --- 근거 충실도 gate (판사가 틀을 벗어난 답 거부/수용) ------------------------
from agent.rag import Chunk  # noqa: E402
from agent.verify import Verdict  # noqa: E402

TWO = Chunk(source="calc", section="Remainder Estimate",
            text="Theorem Integral Test Remainder Estimate: "
                 "\\int _ { n + 1 } ^ \\infty f dx \\leqslant R_n \\leqslant "
                 "\\int _ { n } ^ \\infty f dx .")
GOOD = ("The bound keeps both sides: \\int _ { n + 1 } ^ \\infty f dx "
        "\\leqslant R_n \\leqslant \\int _ { n } ^ \\infty f dx .")
BAD = ("R_n bound ~ 1/(n+1) only: \\int _ { n + 1 } ^ \\infty f dx")


class _StubRag:
    def __init__(self, chunks):
        self.chunks = list(chunks)
    def retrieve(self, query, k=5):
        return self.chunks[:k]


class _BoundPreservingVerifier:
    """판사 stub: 답이 두 하한(n, n+1)을 모두 담고 있어야 accept (의미 판정 대행)."""
    def __init__(self, trace=None):
        self.trace = trace if trace is not None else []
    def verify(self, question, chunks, answer) -> Verdict:
        self.trace.append(answer)
        has_upper = "_ { n } " in answer or "_{ n }" in answer or "{ n } ^" in answer
        if "\\int" in answer and has_upper:
            return Verdict(ok=True, grounded=True,
                           reason="both bounds preserved", judge_role="stub")
        return Verdict(ok=False, grounded=False,
                       errors=["answer narrows to a single (n+1) bound, drops n"],
                       reason="one-sided bound", judge_role="stub")


class _GoodAfterRetryTransport(FakeTransport):
    """1차는 틀린(편도) 답, 교정(REJECTED...) 후엔 올바른 답."""
    def __init__(self, call_log):
        super().__init__()
        self.log = call_log
    def post_text(self, url, body, timeout):
        msg = body["messages"][-1]["content"]
        self.log.append(msg[:12])
        content = GOOD if ("REJECTED" in msg or "VERIFY" in msg) else BAD
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_grounding_gate_retries_then_accepts():
    reg = Registry()
    log = []
    trace = []
    orch = Orchestrator(registry=reg, rag=_StubRag([TWO]), grounding_retries=2,
                        verifier=_BoundPreservingVerifier(trace),
                        gateway_factory=lambda url, role: Gateway(
                            base_url=url, transport=_GoodAfterRetryTransport(log)))
    res, _ = orch.run_tasks([Task(id=1, action="explain", input="q?" , role="worker")],
                            write=False)
    assert res[0].grounded is True
    assert res[0].ok
    assert len(log) == 2        # 1)틀림(BAD) → 2)교정(GOOD) accepted
    assert len(trace) == 2


def test_grounding_gate_exhausts_marks_ungrounded():
    class AlwaysBadTransport(FakeTransport):
        def post_text(self, url, body, timeout):
            return {"choices": [{"message": {"role": "assistant", "content": BAD}}]}

    reg = Registry()
    orch = Orchestrator(registry=reg, rag=_StubRag([TWO]), grounding_retries=2,
                        verifier=_BoundPreservingVerifier(),
                        gateway_factory=lambda url, role: Gateway(
                            base_url=url, transport=AlwaysBadTransport()))
    res, _ = orch.run_tasks([Task(id=1, action="explain", input="q?", role="worker")],
                            write=False)
    assert res[0].grounded is False
    assert "rejected x3" in res[0].grounding_note


# --- 출제(problems) 경로: 구조 gate만 통과·숫자 앵커 생략 (신규 생성 수용) -------
PROBSET = """PROBLEM 1 — [Integral Test]
Question: Decide convergence of Σ_{n=1}^{∞} 1/sqrt(n) by the Integral Test.
Solution key: ∫_1^∞ x^{-1/2} dx diverges; hence series diverges.
Difficulty: easy

PROBLEM 2 — [Remainder Estimate]
Question: For Σ 1/n^3 use R_n ≤ 1/(2 n^2); find n so the error < 0.001.
Solution key: 1/(2 n^2) < 0.001 → n ≥ 23 (use the source's method).
Difficulty: medium
"""


def test_problems_action_accepted_via_structure_gate():
    """numbers 가 source 의 '32'와 달라도(신규 출제) 문제모드로 수용돼야 한다."""
    class ProbTransport(FakeTransport):
        def post_text(self, url, body, timeout):
            return {"choices": [{"message": {"role": "assistant", "content": PROBSET}}]}

    reg = Registry()
    orch = Orchestrator(registry=reg, rag=_StubRag([TWO]),
                        gateway_factory=lambda url, role: Gateway(
                            base_url=url, transport=ProbTransport()))
    res, _ = orch.run_tasks(
        [Task(id=1, action="problems",
              input="make 2 practice problems from the Integral Test section",
              role="worker")], write=False)
    assert res[0].ok and res[0].grounded is True
    assert "problem set accepted" in res[0].grounding_note


def test_problems_action_failed_by_problems_judge():
    """구조 통과해도 출제-판사(ProblemsVerifier)가 거부하면 재시도 후 UNGROUNDED."""
    from agent.verify import Verdict
    from agent.rag import Chunk as _C2

    class ProbTransport(FakeTransport):
        def post_text(self, url, body, timeout):
            return {"choices": [{"message": {"role": "assistant", "content": PROBSET}}]}

    class RejectingProblemsJudge:
        def verify(self, question, chunks, answer) -> Verdict:
            return Verdict(ok=False, grounded=False,
                           errors=["contains absolute-convergence outside source"],
                           error_codes=["extra_claim"], reason="extra_claim",
                           judge_role="stub")

    reg = Registry()
    orch = Orchestrator(
        registry=reg,
        rag=_StubRag([_C2(source="calc", section="11.3",
                          text="Integral Test Remainder Estimate has both n and n+1 bounds.")]),
        grounding_retries=1,
        problems_verifier=RejectingProblemsJudge(),
        gateway_factory=lambda url, role: Gateway(base_url=url,
                                                  transport=ProbTransport()))
    res, _ = orch.run_tasks(
        [Task(id=1, action="problems", input="make problems", role="worker")],
        write=False)
    assert res[0].ok and res[0].output   # 답은 있으나
    assert res[0].grounded is False      # 출제-판사가 거부 → ungrounded
    assert "extra_claim" in res[0].grounding_note


# --- SYMPY 실행 계층(SYMPYMETHOD) 게이트: 손계산 드리프트 결정론 차단 ----
_NUMERIC_CLEAN = """PROBLEM 1 — [Remainder Estimate]
Question: For Σ 1/k^3 use R_n ≤ 1/(2 n^2); find the smallest n so the error < 0.0005.
Solution key: require 1/(2 n^2) < 0.0005 → n > sqrt(1000) ≈ 31.6, round up → 32.
```sympy
from sympy import *
n = ceiling(sqrt(1/(2*Rational(1,2000))))
answer = n
print(answer)
```
Difficulty: medium
"""

_NUMERIC_DRIFT = _NUMERIC_CLEAN.replace("→ 32.", "→ 45.")


def _problems_transport(payload: str):
    class ProbTransport(FakeTransport):
        def post_text(self, url, body, timeout):
            return {"choices": [{"message": {"role": "assistant",
                                             "content": payload}}]}
    return ProbTransport


def test_sympy_gate_accepts_clean_numeric_problem():
    """sympy 가 낸 수치(32)를 Solution key 가 그대로 쓰면 통과."""
    from agent.rag import Chunk as _C2
    reg = Registry()
    orch = Orchestrator(
        registry=reg,
        rag=_StubRag([_C2(source="calc", section="11.3",
                          text="Integral Test Remainder Estimate n and n+1 bounds.")]),
        gateway_factory=lambda url, role: Gateway(
            base_url=url, transport=_problems_transport(_NUMERIC_CLEAN)()))
    res, _ = orch.run_tasks(
        [Task(id=1, action="problems",
              input="make a problem needing a smallest-n", role="worker")],
        write=False)
    assert res[0].ok and res[0].grounded is True
    assert "sympy" in res[0].grounding_note


def test_sympy_gate_rejects_hand_calculation_drift():
    """Solution key 는 45라 적었는데 sympy 가 32를 낸다 → 판사 없이도 결정론 거부."""
    from agent.rag import Chunk as _C2
    reg = Registry()
    orch = Orchestrator(
        registry=reg,
        rag=_StubRag([_C2(source="calc", section="11.3",
                          text="Integral Test Remainder Estimate.")]),
        grounding_retries=0,          # 재시도 없이 즉시 거부
        gateway_factory=lambda url, role: Gateway(
            base_url=url, transport=_problems_transport(_NUMERIC_DRIFT)()))
    res, _ = orch.run_tasks(
        [Task(id=1, action="problems", input="make a problem", role="worker")],
        write=False)
    assert res[0].grounded is False
    assert "sympy gate" in res[0].grounding_note
    assert "hand-calc drift" in res[0].grounding_note or "32" in res[0].grounding_note



