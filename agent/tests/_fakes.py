"""공용 fake — 실제 llama 서버/네트워크를 건드리지 않는 결정적 계약 테스트용.

- FakeTransport  : Gateway 에 주입. post_json/post_text 를 scriptable 응답으로.
- FakeRetriever  : RAG 근거로 쓸 고정 Chunk 목록.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from agent.rag import Chunk


@dataclass
class FakeTransport:
    """Gateway._HttpxTransport 를 대체. url 별 응답 스크립트."""

    chat_reply: str = "hello from worker"
    chat_json_reply: dict | None = None   # chat_json 이 별도 body 기대할 때
    embed_reply: List[List[float]] | None = None
    calls: List[dict] = field(default_factory=list)
    raise_on: str | None = None           # "chat"|"embed" 에서 GatewayError(HTTP 장애) 재현

    # Transport 규약(post_json/post_text) 구현
    def post_json(self, url: str, body: dict, timeout: float) -> dict:
        self.calls.append({"url": url, "body": body, "transport": "json"})
        if self.raise_on == "embed":
            raise RuntimeError("embed backend unreachable")
        if url.endswith("/api/embed"):
            return {"embeddings": self.embed_reply or [[0.1, 0.2]]}
        return self._chat(body)

    def post_text(self, url: str, body: dict, timeout: float) -> Any:
        self.calls.append({"url": url, "body": body, "transport": "text"})
        if self.raise_on == "chat":
            raise RuntimeError("chat backend unreachable")
        return self._chat(body)

    def _chat(self, body: dict) -> dict:
        messages = body.get("messages", [])
        blob = " ".join(str(m.get("content", "")) for m in messages)
        is_json_req = ("Return ONLY a JSON object" in blob
                       or "Return ONLY a strict JSON object" in blob
                       or '"tasks":' in blob)
        # chat_json(parser)이면 스크립트된 JSON 을, 그냥 chat 이면 chat_reply 를.
        content = (self.chat_json_reply and json.dumps(self.chat_json_reply)) \
            if is_json_req else self.chat_reply
        return {
            "id": "cmpl-fake",
            "choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "total_tokens": 2},
        }


class FakeRetriever:
    def __init__(self, chunks: List[Chunk] | None = None) -> None:
        self.chunks = chunks or [
            Chunk(source="calc", section="3.5", text="integral of symmetric "
                  "interval is zero (orthogonality)"),
            Chunk(source="heat", section="boundary", text=(
                "clamped boundary: u[0]=0, u[n-1]=0 — index 1..n-2 interior")),
        ]

    def retrieve(self, query: str, k: int = 5) -> List[Chunk]:
        return self.chunks[:k]
