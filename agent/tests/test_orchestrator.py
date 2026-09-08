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
