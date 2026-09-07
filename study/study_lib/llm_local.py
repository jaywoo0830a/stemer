"""llm_local — DeepSeek(API) 대신 로컬 Ollama 로 구조화 JSON 생성을 수행.

factory.generate_one 은 `LLMClient.complete(system,user,max_tokens,json_object) ->
LLMResult(content=dict, usage)` 계약에만 의존한다. 그러므로 아래 클라이언트가
그 계약을 로컬 Ollama 로 구현하면, 파이프라인(validate→patch→render→lint→저장)은
손대지 않고 "진입 LLM 만 로컬" 로 교체된다(LOCAL_LLM=1).

- 모델: OLLAMA_MODEL (기본 lfm2.5:1.2b-instruct) — host network 로 호스트 127.0.0.1.
- thinking off, 구조화 JSON 만 강제(fence 제거 + json.loads).
- 사용: os.environ['LOCAL_LLM']='1'
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .llm import LLMError, LLMResult, Usage
from .postproc_katex import default_ollama_client


# 기본 로컬 생성 모델 (env OLLAMA_MODEL 이 우선; 명시 없으면 최속 LFM2.5 CPU 경량)
def _model_default():
    return os.environ.get("OLLAMA_MODEL", "lfm2.5:1.2b-instruct")


class LocalClient:
    """Ollama 로 구조화 JSON 생성 — LLMClient(DeepSeek) 계약 호환."""

    def __init__(self, *, model: str | None = None,
                 base_url: str | None = None, timeout: float = 900.0) -> None:
        self._ollama = default_ollama_client()
        # model/base_url은 env 로 기본 지정된 클라이언트가 이미 사용하되,
        # 명시로 덮어쓸 수도 있다.
        if model:
            self._ollama.model = model
        if base_url:
            self._ollama.base_url = base_url.rstrip("/")
        self._ollama.timeout = timeout
        self.model = self._ollama.model

    def _raw(self, prompt: str, system: str, num_predict: int) -> str:
        # OllamaClient 내부 _call 은 /api/generate 로 system+prompt 를 보낸다.
        try:
            return self._ollama._call(prompt, system=system)  # noqa: SLF001
        except Exception as exc:  # 연결/타임아웃 → LLMError 로 승격
            raise LLMError(f"local ollama failed: {exc}") from None

    def complete(self, *, system: str, user: str, max_tokens: int = 1000,
                 json_object: bool = True) -> LLMResult:
        raw = self._raw(user, system, max_tokens)
        if json_object:
            payload = _parse_json(raw)
        else:
            payload = (raw or "").strip()  # 지금 생성엔 json_object 만 사용
        return LLMResult(content=payload, usage=Usage())


def _parse_json(raw: str) -> Any:
    """모델 응답에서 JSON 객체 추출(```json fence 포함 허용)."""
    text = (raw or "").strip()
    if not text:
        raise LLMError("local model returned empty content (no JSON)")
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 모델이 앞/뒤에 텍스트를 섞으면 첫 { ... 마지막 } 구간만 취한다.
    if not text.startswith("{"):
        i = text.find("{")
        j = text.rfind("}")
        if i >= 0 and j > i:
            text = text[i:j + 1]
    try:
        obj = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"local model JSON parse failed: {exc}") from None
    if not isinstance(obj, dict):
        raise LLMError("local model did not return a JSON object")
    return obj


def pick_generate_llm():
    """LOCAL_LLM=1 이면 LocalClient, 아니면 (기존) DeepSeek FlashClient."""
    if os.environ.get("LOCAL_LLM", "0") == "1":
        return LocalClient(model=_model_default())
    from .llm import FlashClient  # 지연 import — DeepSeek 경로는 키 필요 시에만
    return FlashClient()
