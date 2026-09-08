"""verify — LLM 판사(Tier-2) 계약. 결정성 fake transport 로."""
import pytest

from agent.gateway import Gateway, GatewayError
from agent.rag import Chunk
from agent.verify import (Verdict, LlmVerifier, StubVerifier,
                          pick_judge_server, JUDGE_SYSTEM)
from agent.registry import Registry
from agent.tests._fakes import FakeTransport


def test_judge_system_forces_json_schema():
    import re
    flat = re.sub(r"\s+", " ", JUDGE_SYSTEM)
    # 스키마 유도: ok/grounded/errors/error_codes 가 와야 하고, strict JSON 지시 포함.
    assert '"ok": bool' in flat
    assert '"grounded": bool' in flat
    assert '"errors":' in flat
    assert '"error_codes":' in flat      # v2 판사가 내보내는 분류 코드(stable)
    assert "Return ONLY a strict JSON object" in JUDGE_SYSTEM
    # source-authority 가 판사에도 실려 있는지 (fidelity 핵심)
    assert "authoritative" in JUDGE_SYSTEM.lower()


def test_pick_judge_reasoner_for_worker():
    reg = Registry()
    assert pick_judge_server("worker", reg).endswith("8088")   # DeepSeek reasoner
    assert pick_judge_server("coder", reg).endswith("8088")


def test_pick_judge_coder_when_producer_is_reasoner():
    reg = Registry()
    # producer 가 reasoner 면 coder(다른 모델)가 판사
    assert pick_judge_server("reasoner", reg).endswith("8086")


def test_pick_judge_env_override(monkeypatch):
    import os
    monkeypatch.setenv("AGENT_JUDGE", "http://127.0.0.1:9999")
    assert pick_judge_server("worker", Registry()) == "http://127.0.0.1:9999"


def _fake(reply):
    return Gateway("http://x:8088", transport=FakeTransport(chat_json_reply=reply))


def test_llm_verifier_parses_rejection():
    v = LlmVerifier(_fake({
        "ok": False, "grounded": False,
        "errors": ["answer drops the upper bound", "invents 1/(n+1)"],
        "exceptions": [], "reason": "contradicts reference remainder bound"}),
        judge_role="reasoner")
    chunk = Chunk(source="calc", section="11.3",
                  text="R_tail \\int _ { n } ^ f dx")
    verdict = v.verify("bound?", [chunk], "R ~ 1/(n+1)")
    assert verdict.ok is False and verdict.grounded is False
    assert verdict.errors and "upper bound" in verdict.errors[0]
    assert verdict.judge_role == "reasoner"


def test_llm_verifier_parses_accept():
    v = LlmVerifier(_fake({"ok": True, "grounded": True, "errors": [],
                           "exceptions": [], "reason": "matches source"}))
    verdict = v.verify("?", [Chunk(source="a", section="", text="t")],
                       "yes matches")
    assert verdict.ok is True and verdict.grounded is True


def test_ultralight_problem_judge_code_expansion():
    """v3 초경량 문제판사 {ok, code}: ERR_* 를 판사 없이 stable error_code + short
    reason 으로 확장(교정 재시도가 사유를 잃지 않도록)."""
    v = LlmVerifier(_fake({"ok": False, "code": "ERR_SYMPY"}),
                    judge_role="reasoner")  # 기본(JSON 유도) system 사용
    verdict = v.verify("find n", [Chunk(source="a", section="", text="t")], "ans")
    assert verdict.ok is False and verdict.grounded is False
    assert verdict.error_codes == ["hand_calculation_error"]
    assert "hand_calculation_error" in verdict.human


def test_ultralight_problem_judge_ok():
    v = LlmVerifier(_fake({"ok": True, "code": "OK"}), judge_role="reasoner")
    verdict = v.verify("?", [Chunk(source="a", section="", text="t")], "ok set")
    assert verdict.ok is True and verdict.grounded is True
    assert verdict.error_codes == [] and "OK" in verdict.human


def test_llm_verifier_unreachable_defers_accept():
    class Boom(Gateway):
        def chat_json(self, **kw):
            raise GatewayError("judge down")

    v = LlmVerifier(Boom(base_url="http://x:8088"))
    verdict = v.verify("?", [Chunk(source="a", section="", text="t")], "ans")
    assert verdict.ok is True
    assert "deferred" in verdict.reason


def test_stub_verifier_records_trace():
    t = []
    sv = StubVerifier(Verdict(ok=False, grounded=False, reason="nope",
                              judge_role="stub"), trace=t)
    v = sv.verify("q", [Chunk(source="a", section="", text="t")], "ans")
    assert v.ok is False
    assert t and t[0]["n"] == 1 and t[0]["answer_head"] == "ans"
