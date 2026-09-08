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


def test_strip_thinking_removes_think_blocks():
    from study_lib.llm_local import strip_thinking
    assert strip_thinking("<think>let me solve</think>391.") == "391."
    assert strip_thinking(
        "<think>\nI need to compute\n</think>The delta is x.") == "The delta is x."
    assert strip_thinking("No think here.") == "No think here."
    # 겹침 / 앞뒤 이중 think / 빈 think 모두 제거
    assert strip_thinking("<think></think>fin start<think>skip</think> end.") \
        == "fin start end."
    assert strip_thinking("") == ""


def test_complete_free_strips_think_before_return(monkeypatch):
    """Phi-4 가 chat content 에 <think>…</think> 를 포함해 뱉어도 본문만 남긴다."""
    c = LocalClient()
    monkeypatch.setattr(
        c, "_chat",
        lambda system, user, max_tokens:
        ("<think>Okay solve prime x squared.</think>"
         "## Reading the Topic\\n\\nThe value is $x^2$."))
    res = c.complete(system="s", user="u", json_object=False)
    assert res.content.startswith("## ")
    assert "<think>" not in res.content and "</think>" not in res.content


def test_local_client_complete_returns_dict(monkeypatch):
    c = LocalClient()
    monkeypatch.setattr(c, "_chat",
                        lambda system, user, max_tokens: '{"cs":[{"c":"X"}]}')
    res = c.complete(system="s", user="u", json_object=True)
    assert res.content == {"cs": [{"c": "X"}]}


def test_local_client_uses_env_base_url(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_BASE", "http://127.0.0.1:9999")
    c = LocalClient()
    assert c.base_url == "http://127.0.0.1:9999"
    assert c.model == "lfm2.5:1.2b-instruct"


def test_complete_retries_when_reasoning_eats_budget(monkeypatch):
    c = LocalClient()
    calls = []

    def fake_chat(system, user, mt):
        calls.append(mt)
        return '{"ok":1}' if len(calls) >= 2 else ""

    monkeypatch.setattr(c, "_chat", fake_chat)
    res = c.complete(system="s", user="u", json_object=True, max_tokens=1000)
    assert res.content == {"ok": 1}
    assert calls[0] == 1000
    assert calls[1] >= 2000  # reasoning 삼킴 → 재시도는 더 큰 예산


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
