"""openrouter 계약 - fake httpx 모듈 주입으로 OpenRouter(Gemini Flash) 클라이언트를 고정.

httpx 가 설치되어 있지 않아도 동작하도록 sys.modules['httpx'] 에 순수 python fake 를
주입한다(OpenRouterClient 는 complete() 안에서 `import httpx` 한다).

- OPENROUTER_API_KEY 없으면 LLMError.
- /chat/completions로 system+user 를 보내고 usage 를 채운다.
- json_object=True 는 content 를 JSON 객체로 파싱, False 는 raw 문자열 유지.
"""
import sys
import types

import pytest

from study_lib.llm import LLMError
from study_lib.openrouter import OpenRouterClient


class _HTTPError(Exception):
    pass


class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.text = "ERR"

    def json(self):
        return self._data


def _ok(content="", pt=10, ct=20):
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct}}


@pytest.fixture
def fake_httpx(monkeypatch):
    state = {"url": None, "body": None}

    def install(resp):
        def post(url, *, headers, json, timeout):
            state["url"] = url
            state["body"] = json
            return resp

        mod = types.SimpleNamespace(post=post, HTTPError=_HTTPError)
        monkeypatch.setitem(sys.modules, "httpx", mod)
        return state

    return install


def test_api_key_required(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterClient()


def test_complete_text_chat(fake_httpx):
    state = fake_httpx(_Resp(_ok("## Key\nbody")))
    c = OpenRouterClient(api_key="sk", base_url="https://ex/v1")
    r = c.complete(system="sys", user="usr", max_tokens=500)
    assert r.content == "## Key\nbody"
    assert r.usage.prompt_tokens == 10 and r.usage.completion_tokens == 20
    assert state["url"] == "https://ex/v1/chat/completions"
    assert state["body"]["messages"][1]["content"] == "usr"
    assert state["body"]["max_tokens"] == 500
    assert "response_format" not in state["body"]


def test_json_object_mode(fake_httpx):
    state = fake_httpx(_Resp(_ok('{"problems": ["a"]}')))
    c = OpenRouterClient(api_key="sk", base_url="https://ex/v1")
    r = c.complete(system="s", user="u", json_object=True)
    assert state["body"]["response_format"] == {"type": "json_object"}
    assert r.content == {"problems": ["a"]}


def test_http_error_wrapped(fake_httpx):
    fake_httpx(_Resp({}, status=500))
    c = OpenRouterClient(api_key="sk", base_url="https://ex/v1")
    with pytest.raises(LLMError, match="500"):
        c.complete(system="s", user="u")
