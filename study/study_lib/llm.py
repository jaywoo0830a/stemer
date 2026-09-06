"""LLM 호출 — DeepSeek Flash (OpenAI 호환, json_object).

클라이언트 관점:
    result = client.complete(system=..., user=..., max_tokens=...)
    payload = result.content        # 파싱된 JSON 객체 (dict)
    result.usage.cost_usd()         # 실단가 기반 비용 (피크 ×2 옵션)

- 실제 네트워크는 `FlashClient`(httpx, 선택 의존성). 테스트는 `LLMClient` 인터페이스 +
  가짜 클라이언트로 계약을 고정한다.
- `usage` 에서 캐시 히트/미스·출력 토큰을 추출해 비용 로깅에 쓴다.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

# 실단가 (1M 토큰당 USD, 오프피크) — 피크 시간대는 ×2.
RATE_CACHE_HIT = 0.007
RATE_CACHE_MISS = 0.22
RATE_OUTPUT = 0.66


class LLMError(Exception):
    """LLM 호출/파싱 실패 — 클라이언트가 읽을 수 있는 메시지 포함."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0

    @property
    def input_tokens(self) -> int:
        return self.cache_hit_tokens + self.cache_miss_tokens

    def cost_usd(self, *, peak: bool = False) -> float:
        mult = 2.0 if peak else 1.0
        return mult * (
            self.cache_hit_tokens / 1_000_000 * RATE_CACHE_HIT
            + self.cache_miss_tokens / 1_000_000 * RATE_CACHE_MISS
            + self.completion_tokens / 1_000_000 * RATE_OUTPUT
        )


@dataclass(frozen=True)
class LLMResult:
    content: Any
    usage: Usage


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str, max_tokens: int = 1000,
                 json_object: bool = True) -> LLMResult: ...


def parse_json_content(raw: str) -> Any:
    """모델이 준 JSON 문자열 → 객체. (```json 펜스 허용)."""
    text = (raw or "").strip()
    if not text:
        raise LLMError("model returned empty content (no JSON)")
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:200].replace("\n", " ")
        raise LLMError(
            f"model returned invalid JSON: {exc} | raw[:200]={snippet!r}"
        ) from exc


def extract_usage(data: dict) -> Usage:
    """API 응답 dict → Usage (DeepSeek 필드명, 없으면 0)."""
    u = data.get("usage", {}) or {}
    return Usage(
        prompt_tokens=int(u.get("prompt_tokens", 0) or 0),
        completion_tokens=int(u.get("completion_tokens", 0) or 0),
        cache_hit_tokens=int(u.get("prompt_cache_hit_tokens", 0) or 0),
        cache_miss_tokens=int(u.get("prompt_cache_miss_tokens", 0) or 0),
    )


def build_messages(system: str, user: str) -> list[dict]:
    """캐시 프리픽스 순서 보장: 고정 system 이 맨 앞, 가변 user 는 마지막."""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


class FlashClient:
    """DeepSeek — OpenAI 호환 /chat/completions. httpx 선택 의존성.

    모델 기본값은 DEEPSEEK_MODEL env (없으면 deepseek-v4-flash).
    reasoning 수준은 DEEPSEEK_REASONING_EFFORT env (기본 low):
    low/high/max — 낮출수록 추론 토큰(비용) 감소, 답변은 더 직관적.
    """

    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, timeout: float = 60.0,
                 reasoning_effort: str | None = None) -> None:
        model = model or os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"
        api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise LLMError("FlashClient needs DEEPSEEK_API_KEY env (or api_key=)")
        effort = reasoning_effort or os.environ.get("DEEPSEEK_REASONING_EFFORT", "low")
        if effort not in ("low", "high", "max"):
            raise LLMError(f"invalid reasoning_effort {effort!r}; use low/high/max")
        self._model = model
        self._base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL")
                          or "https://api.deepseek.com/v1").rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._reasoning_effort = effort

    def complete(self, *, system: str, user: str, max_tokens: int = 1000,
                 json_object: bool = True) -> LLMResult:
        try:
            import httpx
        except ImportError:
            raise LLMError("FlashClient needs httpx: pip install httpx") from None

        body: dict[str, Any] = {
            "model": self._model,
            "messages": build_messages(system, user),
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "stream": False,
            # v4 추론형: effort 를 낮춰 reasoning 토큰(비용) 절감
            "thinking": {"type": "enabled"},
            "reasoning_effort": self._reasoning_effort,
        }
        if json_object:
            body["response_format"] = {"type": "json_object"}

        try:
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"request failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"API {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        usage = extract_usage(data)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:400]}") from exc
        raw = message.get("content")
        if raw is None or str(raw).strip() == "":
            # 추론형 모델 등 content 대신 다른 키에 답을 넣을 수 있다 → 진단
            finish = data.get("choices", [{}])[0].get("finish_reason")
            raise LLMError(
                "model returned empty content: "
                f"finish_reason={finish} message_keys={sorted(message)} "
                f"msg={str(message)[:400]}"
            )
        return LLMResult(content=parse_json_content(raw), usage=usage)
