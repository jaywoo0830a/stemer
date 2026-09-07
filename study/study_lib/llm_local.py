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


# 기본 로컬 생성 모델 이름 (llama.cpp server 는 한 gguf 모델이라 이름은 아직 참조용)
def _model_default():
    return os.environ.get("OLLAMA_MODEL", "lfm2.5:1.2b-instruct")


def _local_base():
    """llama.cpp server(OpenAI 호환) 기본 주소 — host 네트워크에서 127.0.0.1:8080."""
    return os.environ.get("LOCAL_LLM_BASE", "http://127.0.0.1:8080").rstrip("/")


class LocalClient:
    """로컬 추론 서버로 구조화 JSON 생성 — LLMClient(DeepSeek) 계약 호환.

    백엔드는 llama.cpp llama-server 의 OpenAI-호환 /v1/chat/completions (기본
    127.0.0.1:8080). model 은 서버가 단일 gguf 를 띄우므로 body 에 넣지 않는다.
    (이전 Ollama 벡엔드 대응이 필요하면 LOCAL_LLM_BASE 를 openai 호환 ollama
    serve 주소로 바꾸면 됨.)
    """

    def __init__(self, *, base_url: str | None = None,
                 timeout: float = 1200.0) -> None:
        self.base_url = (base_url or _local_base())
        self.model = _model_default()
        self.timeout = timeout

    def _chat(self, system: str, user: str, max_tokens: int) -> str:
        import httpx  # 선택 의존성 (DeepSeek 경로와 동일)
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(f"{self.base_url}/v1/chat/completions", json=body)
            if r.status_code >= 400:
                raise LLMError(f"local llm HTTP {r.status_code}: {r.text[:200]}")
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"local llm failed: {exc}") from None
        try:
            choices = r.json()["choices"]
            return choices[0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"local llm bad response: {exc}") from None

    def complete(self, *, system: str, user: str, max_tokens: int = 1000,
                 json_object: bool = True) -> LLMResult:
        raw = self._chat(system, user, max_tokens)
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
    """LOCAL_LLM=1 이면 로컬 llama.cpp server(LocalClient), 아니면 DeepSeek."""
    if os.environ.get("LOCAL_LLM", "0") == "1":
        return LocalClient()
    from .llm import FlashClient  # 지연 import — DeepSeek 경로는 키 필요 시에만
    return FlashClient()
