"""openrouter — OpenRouter API 로 원격 LLM (예: Gemini Flash) 호출.

Docling RAG store 의 공부 범위를 Gemini 등으로 압축(compress)하는 진입 LLM.
기존 `LLMClient` 계약(complete / LLMResult)을 따르므로 study 파이프라인
(factory/compress/generate_free)에 그대로 끼울 수 있다.

- 백엔드: https://openrouter.ai/api/v1/chat/completions (OpenAI 호환 JSON).
- 인증: `Authorization: Bearer $OPENROUTER_API_KEY`.
- 모델: `OPENROUTER_MODEL` (기본 google/gemini-2.5-flash — OpenRouter 상의
  실제 모델 id와 일치하도록 env 로 조정).
- 구조화 JSON: json_object=True 면 `response_format: {type: json_object}` 시도.
  일부 provider 는 미지원 → 실패 시 prompt 기반 fallback(스키마 주입 생략)으로
  재시도한다. 이 프로젝트 사용처(compress)는 json_object=False 를 기본으로 쓰므로
  fallback 은 기본 경로에 영향을 주지 않는다.
- 비용: OpenRouter usage 는 DeepSeek 과 필드가 달라(prompt/completion_tokens) {
  `extract_usage` 를 범용 필드명으로 처리한다.
"""
from __future__ import annotations

import os
from typing import Any

from .llm import LLMError, LLMResult, Usage, parse_json_content


def _env_or(name: str, default: str) -> str:
    return os.environ.get(name) or default


class OpenRouterClient:
    """OpenRouter 원격 LLM — LLMClient(complete) 계약 구현.

    사용:
        c = OpenRouterClient()                      # OPENROUTER_API_KEY 유래
        res = c.complete(system=sys, user=usr, json_object=False)
        markdown = res.content                       # str (json_object=False 일 때)
        res.usage.cost_usd()                         # 입력/출력 토큰 기반 비용 추정
    """

    DEFAULT_MODEL = "google/gemini-2.5-flash"
    DEFAULT_BASE = "https://openrouter.ai/api/v1"

    def __init__(self, *, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, timeout: float = 180.0) -> None:
        self._model = (model or os.environ.get("OPENROUTER_MODEL")
                       or self.DEFAULT_MODEL)
        self._api_key = (api_key or os.environ.get("OPENROUTER_API_KEY"))
        if not self._api_key:
            raise LLMError("OpenRouterClient needs OPENROUTER_API_KEY env (or api_key=)")
        self._base = (base_url or os.environ.get("OPENROUTER_BASE_URL")
                      or self.DEFAULT_BASE).rstrip("/")
        self._timeout = timeout

    def count_tokens(self, text: str) -> int:
        """문자 근사 토큰 (배치 예산 추정에 사용, 서버 미존재 시)."""
        return max(1, (len(text) + 2) // 3)

    def complete(self, *, system: str, user: str, max_tokens: int = 2048,
                 json_object: bool = False) -> LLMResult:
        try:
            import httpx
        except ImportError:
            raise LLMError("OpenRouterClient needs httpx: pip install httpx") from None

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "stream": False,
        }
        if json_object:
            body["response_format"] = {"type": "json_object"}

        url = f"{self._base}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            import httpx as _h
            resp = _h.post(url, headers=headers, json=body, timeout=self._timeout)
        except _h.HTTPError as exc:
            # provider 가 response_format(=json_object) 미지원하면 400 → prompt 폴백
            if json_object:
                body.pop("response_format", None)
                try:
                    resp = _h.post(url, headers=headers, json=body,
                                   timeout=self._timeout)
                except _h.HTTPError as exc2:
                    raise LLMError(f"OpenRouter request failed: {exc2}") from exc2
            else:
                raise LLMError(f"OpenRouter request failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        usage = self._extract_usage(data)
        message = data.get("choices", [{}])[0].get("message", {}) or {}
        content = message.get("content")
        if content is None or str(content).strip() == "":
            # 추론형 모델이 content 대신 reasoning 을 주는 진단용
            finish = data.get("choices", [{}])[0].get("finish_reason")
            raise LLMError(
                "OpenRouter returned empty content: "
                f"finish_reason={finish} msg={str(message)[:400]}")
        content_str = str(content)
        value: Any = parse_json_content(content_str) if json_object else content_str
        return LLMResult(content=value, usage=usage)

    @staticmethod
    def _extract_usage(data: dict) -> Usage:
        """OpenRouter/OpenAI 호환 usage 필드로 변환 (부재 시 0)."""
        u = data.get("usage", {}) or {}
        return Usage(
            prompt_tokens=int(u.get("prompt_tokens", 0) or 0),
            completion_tokens=int(u.get("completion_tokens", 0) or 0),
        )