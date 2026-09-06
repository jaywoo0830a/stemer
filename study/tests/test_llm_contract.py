"""LLM 계약 — 클라이언트 관점 테스트.

- `usage`: 캐시 히트/미스/출력 토큰 → 실단가 비용 (피크 ×2).
- 파싱: 모델이 json_object 로 준 원문 → 파이썬 객체.
- 실제 네트워크(`FlashClient`) 대신 `LLMClient` 인터페이스 + 가짜로 계약을 고정.
"""
import importlib.util

import pytest

from study_lib import llm
from study_lib.llm import (
    LLMResult,
    Usage,
    FlashClient,
    build_messages,
    extract_usage,
    parse_json_content,
)


def test_usage_cost_matches_flash_unit_prices():
    assert Usage(cache_hit_tokens=1_000_000).cost_usd() == pytest.approx(0.007)
    assert Usage(cache_miss_tokens=1_000_000).cost_usd() == pytest.approx(0.22)
    assert Usage(completion_tokens=1_000_000).cost_usd() == pytest.approx(0.66)


def test_usage_cost_doubles_at_peak():
    usage = Usage(cache_hit_tokens=1_000_000, completion_tokens=1_000_000)
    assert usage.cost_usd(peak=True) == pytest.approx(2 * usage.cost_usd(peak=False))


def test_usage_input_tokens_are_hit_plus_miss():
    usage = Usage(prompt_tokens=800, cache_hit_tokens=500, cache_miss_tokens=300)
    assert usage.input_tokens == 800


def test_parse_json_content_handles_plain_and_fenced():
    assert parse_json_content('{"a": 1}') == {"a": 1}
    assert parse_json_content('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(llm.LLMError, match="invalid JSON"):
        parse_json_content("{not json")


def test_extract_usage_maps_deepseek_fields_and_defaults():
    data = {"usage": {"prompt_tokens": 7000, "completion_tokens": 300,
                      "prompt_cache_hit_tokens": 5000,
                      "prompt_cache_miss_tokens": 2000}}
    usage = extract_usage(data)
    assert (usage.prompt_tokens, usage.completion_tokens) == (7000, 300)
    assert (usage.cache_hit_tokens, usage.cache_miss_tokens) == (5000, 2000)
    assert extract_usage({}).prompt_tokens == 0


def test_build_messages_keeps_system_prefix_first():
    messages = build_messages("SYSTEM-FIXED", "USER-VARIABLE")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == "SYSTEM-FIXED"
    assert messages[1]["content"] == "USER-VARIABLE"


def test_client_interface_with_a_fake_returns_content_and_usage():
    class FakeFlash:
        def complete(self, *, system, user, max_tokens=1000, json_object=True):
            return LLMResult(
                content={"ok": True},
                usage=Usage(cache_hit_tokens=7000, cache_miss_tokens=2000,
                            completion_tokens=300),
            )

    result = FakeFlash().complete(system="s", user="u")
    assert result.content == {"ok": True}
    assert result.usage.completion_tokens == 300


def test_flash_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="DEEPSEEK_API_KEY"):
        FlashClient()


def test_flash_client_defaults_to_v4_flash_model(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    client = FlashClient()
    assert client._model == "deepseek-v4-flash"


@pytest.mark.skipif(importlib.util.find_spec("httpx") is not None,
                    reason="httpx installed")
def test_flash_client_complete_without_httpx_gives_actionable_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    client = FlashClient()
    with pytest.raises(llm.LLMError, match="httpx"):
        client.complete(system="s", user="u")
