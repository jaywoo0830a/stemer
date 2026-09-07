"""llm_local 계약 — 로컬 Ollama 구조화 JSON 클라이언트.

규칙(모든 목적은 factory.generate_one 의 LLMClient 계약 호환):
complete(*, system, user, max_tokens, json_object=True) -> LLMResult(content=dict)
- 모델 응답: JSON 객체(```json fence 포함 가능), 앞뒤 잡문을 첫 { ... }로 단면 처리.
- 깨진/빈/비-객체는 LLMError.
- LOCAL_LLM=1 이면 pick_generate_llm 은 LocalClient, 아니면 DeepSeek Flash.
여기선 실제 Ollama 호출 없이 순수 _parse_json 과 LocalClient(fake _call) 로 고정한다.
"""
import pytest

from study_lib.llm_local import LocalClient, _parse_json
from study_lib.llm import LLMError
import os


class _FakeOllama:
    def __init__(self, raw):
        self.raw = raw
        self.model = "qwen3-coder:30b"
        self.base_url = "http://x"
        self.timeout = 1.0

    def _call(self, prompt, system=""):
        return self.raw


def test_parse_json_returns_dict_payload():
    assert _parse_json('{"cs":[]}') == {"cs": []}


def test_parse_json_strips_fence_and_surrounding_text():
    raw = 'intro\n```json\n{"lecture":"hi"}\n```\ntrailing'
    assert _parse_json(raw) == {"lecture": "hi"}
    # 펜스 없이 앞뒤 잡문
    assert _parse_json('  prefix {"a":1} suffix ') == {"a": 1}


def test_parse_json_raises_on_empty_or_nonobject():
    with pytest.raises(LLMError):
        _parse_json("")
    with pytest.raises(LLMError):
        _parse_json("not json")       # { 없음
    with pytest.raises(LLMError):
        _parse_json("[1,2]")          # dict 아님


def test_local_client_complete_returns_dict():
    c = LocalClient()
    c._ollama = _FakeOllama('{"cs":[{"c":"X"}]}')
    res = c.complete(system="s", user="u", json_object=True)
    assert res.content == {"cs": [{"c": "X"}]}


def test_pick_generate_llm_local_vs_remote(monkeypatch):
    from study_lib.llm_local import pick_generate_llm
    monkeypatch.setenv("LOCAL_LLM", "1")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3-coder:30b")
    assert isinstance(pick_generate_llm(), LocalClient)

    monkeypatch.setenv("LOCAL_LLM", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    llm = pick_generate_llm()   # DeepSeek 경로
    from study_lib.llm import FlashClient
    assert isinstance(llm, FlashClient)
