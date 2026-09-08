"""planner 계약 — 자유 텍스트 → 티켓 분해(결정적 + parser 서버 경로)."""
import pytest

from agent.gateway import Gateway
from agent.planner import (PlanParser, Task, ROLE_BY_ACTION, split_plan,
                           task_from_dict, tasks_to_dicts)
from agent.tests._fakes import FakeTransport


PLAN = """\
[Task 1: Bug Analysis]
File: heat_solver.py, function `explicit_euler_step`
Analyze why the explicit euler diverges. Focus on boundary indexing.

[Task 2: Fix Proposal]
Provide minimal corrected boundary code.

[Task 3: Concept]
Explain why integral of sin(w x) over symmetric interval is zero.

[Task 4: Proof]
Show step-by-step orthogonality of fourier basis.
"""


def test_split_plan_builds_ordered_tasks():
    tasks = split_plan(PLAN)
    assert [t.id for t in tasks] == [1, 2, 3, 4]
    # Task1 은 fix→coder, Task2 code, Task3 explain/worker, Task4 proof→reasoner
    roles = {t.id: t.role for t in tasks}
    assert roles[1] == "coder"
    assert roles[2] == "coder"
    assert roles[3] == "worker"
    assert roles[4] == "reasoner"


def test_split_plan_keeps_body_and_target():
    tasks = split_plan(PLAN)
    t1 = next(t for t in tasks if t.id == 1)
    assert t1.target == "heat_solver.py"
    assert "explicit_euler_step" in t1.input
    assert "boundary indexing" in t1.input


def test_split_plan_empty():
    assert split_plan("") == []
    assert split_plan("   \n") == []


def test_default_role_map_sensible():
    assert ROLE_BY_ACTION["fix"] == "coder"
    assert ROLE_BY_ACTION["proof"] == "reasoner"
    assert ROLE_BY_ACTION["explain"] == "worker"


def test_task_from_dict_defaults():
    t = task_from_dict({"id": 1, "action": "fix", "input": "patch"})
    assert t.role == "coder"
    assert t.target == ""
    # 명시 role 우선
    t2 = task_from_dict({"id": 2, "action": "explain", "role": "reasoner", "input": "x"})
    assert t2.role == "reasoner"


def test_tasks_roundtrip_dicts():
    ts = split_plan(PLAN)[:2]
    back = [task_from_dict(d) for d in tasks_to_dicts(ts)]
    assert [t.id for t in back] == [t.id for t in ts]


def test_labeled_plan_shortcircuits_parser_and_honors_label():
    """[Task …: explain] 라벨이 있으면 결정적 split_plan 이 worker 로 고정.
    (파서가 explain→reasoner 로 업그레이드하던 드리프트 재현 방지 — 회귀 가드)"""
    fake = FakeTransport(chat_json_reply={
        "tasks": [{"id": 1, "action": "explain", "role": "reasoner",
                    "input": "why"}]})
    gw = Gateway("http://x:8081", transport=fake)
    pp = PlanParser(gw)
    tasks = pp.parse("[Task 1: explain] why is integral zero symmetric")
    assert len(tasks) == 1 and tasks[0].role == "worker"   # 라벨 존중 (파서 무시)
    assert fake.calls == []                                   # 파서 안 불림


def test_plan_parser_uses_parser_gateway_for_free_text(monkeypatch):
    fake = FakeTransport(chat_json_reply={
        "tasks": [{"id": 9, "action": "proof", "input": "orthogonality of sin",
                   "role": "reasoner"}]})
    gw = Gateway("http://x:8081", transport=fake)
    pp = PlanParser(gw)
    tasks = pp.parse("some free text without task markers")
    assert tasks[0].id == 9 and tasks[0].role == "reasoner"
    # parser 서버(/v1/chat/completions) 호출됐는지 확인
    assert fake.calls[0]["url"].endswith("/v1/chat/completions")


def test_plan_parser_falls_back_on_gateway_error_free_text():
    class Boom(Gateway):
        def chat_json(self, **kw):  # noqa: ARG002
            from agent.gateway import GatewayError
            raise GatewayError("parser down")

    pp = PlanParser(Boom(base_url="http://x:8081"))  # type: ignore[abstract]
    # 라벨 없는 자유 텍스트 → parser 서버가 죽으면 빈 결과(fallback)
    out = pp.parse("free text question no markers")
    assert out == []


def test_plan_parser_no_fallback_raises():
    class Boom(Gateway):
        def chat_json(self, **kw):  # noqa: ARG002
            from agent.gateway import GatewayError
            raise GatewayError("parser down")

    pp = PlanParser(Boom(base_url="http://x:8081"), allow_fallback=False)
    # allow_fallback=False + 라벨 없는 자유 텍스트 → parser 호출 후 raise
    with pytest.raises(Exception):
        pp.parse("free text question no markers")
