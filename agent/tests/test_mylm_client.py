"""myllm_client 계약 — myLLM Script Runner(DOC/1) 연동 결정성.

실제 네트워크 없이 fake transport 로:
- /v1/run 액션 구성(up/down/start_all/start_heavy/status)
- Bearer 토큰 헤더 전달 · 화이트리스트(allowlist) · ok:false 에러 처리
- 잘못된 action/slug 는 클라이언트에서 미리 거부
"""
from __future__ import annotations

import pytest

from agent.myllm_client import MyllmClient, MyllmError, DEFAULT_MYLLM_URL
from agent.tests._fakes import FakeMyllmTransport


def _client(replies=None, token="secret", fail=False):
    return MyllmClient(base_url="http://127.0.0.1:8000", token=token,
                       transport=FakeMyllmTransport(replies, fail))


def test_default_url_and_token_from_env(monkeypatch):
    monkeypatch.delenv("MYLLM_API", raising=False)
    monkeypatch.delenv("MYLLM_TOKEN", raising=False)
    c = MyllmClient()
    assert c.base_url == DEFAULT_MYLLM_URL


def test_run_start_all_body():
    t = FakeMyllmTransport()
    c = MyllmClient(base_url="http://x:8000", token=None, transport=t)
    c.start_all()
    assert t.calls[0]["body"] == {"action": "start_all"}


def test_start_heavy_setter_and_judge():
    for heavy in ("setter", "judge"):
        t = FakeMyllmTransport()
        c = MyllmClient(base_url="http://x:8000", token=None, transport=t)
        c.start_heavy(heavy)
        assert t.calls[-1]["body"] == {"action": "start_heavy", "arg": heavy}


def test_start_heavy_rejects_unknown_slug():
    c = _client()
    with pytest.raises(MyllmError):
        c.start_heavy("reasoner")   # 오직 setter|judge 만


def test_unknown_action_rejected():
    c = _client()
    with pytest.raises(MyllmError):
        c.run("nope")


def test_bearer_header_sent_when_token():
    t = FakeMyllmTransport()
    c = MyllmClient(base_url="http://x:8000", token="my-secret", transport=t)
    c.status()
    h = t.calls[0]["headers"]
    assert h == {"Authorization": "Bearer my-secret"}


def test_no_header_when_token_none():
    t = FakeMyllmTransport()
    c = MyllmClient(base_url="http://x:8000", token=None, transport=t)
    c.status()
    assert t.calls[0]["headers"] == {}


def test_ok_false_raises_and_stdout_ok_returned():
    t = FakeMyllmTransport(replies={"run": {"ok": True, "stdout": "✅ parser up"}})
    c = MyllmClient(base_url="http://x:8000", token=None, transport=t)
    rep = c.up("parser")
    assert rep["ok"] is True and "parser" in rep["stdout"]

    bad = FakeMyllmTransport(replies={"run": {"ok": False, "stderr": "boom"}})
    c2 = MyllmClient(base_url="http://x:8000", token=None, transport=bad)
    with pytest.raises(MyllmError):
        c2.up("parser")


def test_allowlist_parses_list_and_wrapped():
    # top-level list
    t = FakeMyllmTransport(replies={"allowlist": [{"action": "up"}]})
    c = MyllmClient(base_url="http://x:8000", token=None, transport=t)
    assert c.allowlist() == [{"action": "up"}]
    # wrapped in dict under "actions"
    t2 = FakeMyllmTransport(replies={"allowlist": {"actions": ["up"], "x": 1}})
    c2 = MyllmClient(base_url="http://x:8000", token=None, transport=t2)
    assert c2.allowlist() == ["up"]


def test_health_returns_dict():
    t = FakeMyllmTransport(replies={"health": {"ok": True, "scr": "..."}})
    c = MyllmClient(base_url="http://x:8000", token=None, transport=t)
    assert c.health() == {"ok": True, "scr": "..."}


def test_run_unreachable_wraps_error():
    c = _client(fail=True)
    with pytest.raises(MyllmError):
        c.status()


def test_allowlist_unreachable_wraps_error():
    c = _client(fail=True)
    with pytest.raises(MyllmError):
        c.allowlist()