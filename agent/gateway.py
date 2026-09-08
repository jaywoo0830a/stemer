"""gateway — 다중 역할 추론 서버 호출 (OpenAI 호환 + Ollama 임베딩).

설계 (study_lib.llm_local 관례):
- 각 llama.cpp llama-server 는 OpenAI 호환 `/v1/chat/completions` 를 띄운다.
- 임베딩: Ollama `/api/embed`(1차) → 지원 안 하면 OpenAI 호환 `/v1/embeddings`(2차,
  llama.cpp `--embeddings`·Ollama 겸용). 띄운 서버에 --embeddings 가 없으면 최종
  GatewayError 를 내고, 호출부(rag)는 어휘(BM25) 검색으로 자동 저하한다.
- 실제 네트워크는 httpx. 테스트는 fake transport 로 계약 고정.
- chat 응답에서 <think>…</think> 를 제거하고(R1/reasoning), 빈 내용/HTTP 오류는
  GatewayError 로.

정의:
    gw = Gateway(base_url="http://127.0.0.1:8081", timeout=60.0)
    out = gw.chat(system=..., user=...)          # -> str (content)
    emb = gw.embed(texts=[...])                  # -> list[list[float]]
"""
from __future__ import annotations

import json
from typing import Any, List, Optional, Protocol, Sequence


class GatewayError(Exception):
    pass


def strip_thinking(text: str) -> str:
    """<think>…</think> (겹침/중첩 포함) 제거 — reasoning 모델 content 정화.

    정규식 단일 패턴은 중첩된 <think> 를 제대로 닫지 못하므로, 태그 깊이를 세며
    열림-닫힘 쌍만 통째로 제거한다 (스택/카운터 스캔, 회귀 가드 test 참고).
    """
    if not text:
        return text
    out = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("<think>", i):
            depth += 1
            i += len("<think>")
            continue
        if text.startswith("</think>", i):
            depth = max(0, depth - 1)
            i += len("</think>")
            continue
        if depth == 0:
            out.append(text[i])
        i += 1
    return "".join(out).strip()


class Transport(Protocol):
    """HTTP 호출 추상화 — 실제는 httpx, 테스트는 in-memory fake."""

    def post_json(self, url: str, body: dict, timeout: float) -> dict: ...
    def post_text(self, url: str, body: dict, timeout: float) -> Any: ...


class _HttpxTransport:
    """기본 구현 — httpx.Client (선택 의존성, 실제 배포에서만)."""

    def post_json(self, url: str, body: dict, timeout: float) -> dict:
        import httpx  # 선택 의존성
        with httpx.Client(timeout=timeout) as c:
            r = c.post(url, json=body)
        if r.status_code >= 400:
            raise GatewayError(f"HTTP {r.status_code} on {url}: {r.text[:200]}")
        return r.json()

    def post_text(self, url: str, body: dict, timeout: float) -> Any:
        return self.post_json(url, body, timeout)


class Gateway:
    """하나의 base_url(역할)에 대한 chat + embed 호출자.

    Args:
        base_url: 서버 루트 (예: http://127.0.0.1:8081)
        transport: 주입 가능 (기본 httpx). 테스트는 fake 로 교체.
    """

    def __init__(self, base_url: str, *, transport: Transport | None = None,
                 timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport or _HttpxTransport()

    # ---- chat ----
    def chat(self, *, system: str, user: str,
             max_tokens: int = 2000,
             temperature: float = 0.0,
             model: str | None = None) -> str:
        """llama-server용 저장된 LLM이 content 에 심는 생각을 제거하고 본문만 반환."""
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if model:
            body["model"] = model
        data = self._transport.post_text(
            f"{self.base_url}/v1/chat/completions", body, self.timeout)
        try:
            content = data["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError(f"malformed chat response: {data!r:.300}") from None
        if content is None:
            # reasoning 모델은 content 가 비고 thinking만 채웠을 수 있음
            if isinstance(data.get("choices"), list) and data["choices"]:
                content = data["choices"][0].get("message", {}).get("reasoning_content") or ""
            content = content or ""
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        return strip_thinking(str(content))

    def chat_json(self, *, system: str, user: str, max_tokens: int = 2000,
                  temperature: float = 0.0, model: str | None = None) -> Any:
        """chat 후 JSON 객체(dict)만 추출. 파싱 실패 → GatewayError."""
        raw = self.chat(system=system, user=user, max_tokens=max_tokens,
                        temperature=temperature, model=model)
        obj = _find_json_object(raw)
        if obj is None:
            raise GatewayError("chat did not return a JSON object; head: "
                               + _head(raw, 240))
        return obj

    # ---- embed ----
    def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        """텍스트 → 벡터. 두 백엔드를 두 경로로 시도한다.

        - 1차: Ollama 스타일 `POST /api/embed`  (model + {"input": [...]}).
        - 2차(저하): 응답이 "does not support embeddings"(llama --embeddings 없음)이면
          OpenAI 호환 `POST /v1/embeddings` 로 재시도 — llama-server 를 `--embeddings`
          로 띄운 뒤에도 이 메서드 하나로 동작하도록. 요청이 "임베딩 미지원"이 아니면
          1차 실패를 그대로 되돌린다(단순 4xx 는 masking 하지 않음).
        """
        if not texts:
            return []
        first = self._embed_via("/api/embed", texts, model)
        if first is not None:
            return first
        # 1차가 '임베딩 미지원' 으로 실패 → llama --embeddings(OpenAI) 로 폴백
        second = self._embed_via("/v1/embeddings", texts, model)
        if second is not None:
            return second
        raise GatewayError(
            "embedding disabled on this server (no /api/embed and no /v1/embeddings; "
            "start llama-server with --embeddings or point to Ollama)")

    def _embed_via(self, route: str, texts, model) -> Optional[list[list[float]]]:
        body: dict[str, Any] = {}
        if model and route == "/api/embed":
            body["model"] = model          # Ollama: model 필수
        if route == "/api/embed":
            body["input"] = list(texts)    # Ollama payload
        else:
            body["model"] = model or "bge-m3"
            body["input"] = list(texts)    # OpenAI 호환
        try:
            data = self._transport.post_json(
                f"{self.base_url}{route}", body, self.timeout)
        except GatewayError as exc:
            if "does not support embeddings" in str(exc):
                return None
            raise
        except Exception as exc:  # noqa: BLE001 - 잘못된 transport 는 폴백 지시로
            if "does not support embeddings" in str(exc):
                return None
            raise
        embs = data.get("embeddings")
        if isinstance(embs, list):
            return [list(map(float, e)) for e in embs]
        # OpenAI 호환 응답은 data["data"][i]["embedding"]
        if isinstance(data.get("data"), list):
            vals = [d.get("embedding") for d in data["data"]]
            if vals and all(isinstance(v, list) for v in vals):
                return [list(map(float, v)) for v in vals]
        raise GatewayError(f"embed response unrecognized on {route}: {str(data)[:200]}")


class OllamaEmbed:
    """Gateway.embed 를 study_lib Embedder 계약(embed_texts → vectors)로 감싼 어댑터.

    study_lib/store 의 search_dense 는 벡터를 cosine 거리로 계산하므로,
    normalize 된 임베딩이면 코사인 유사도와 일치한다 (Ollama bge/qwen은 정규화).
    """

    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 *, model: str = "qwen2.5:3b",
                 transport: Transport | None = None,
                 timeout: float = 120.0, dim: int = 3072) -> None:
        self._gw = Gateway(base_url, transport=transport, timeout=timeout)
        self.model = model
        self.dim = dim

    def embed_texts(self, texts: Sequence[str], *, batch_size: int = 32) -> list[tuple]:
        vecs = self._gw.embed(texts, model=self.model)
        return [tuple(v) for v in vecs]


# --------------------------------------------------------------------------- #
# JSON 추출 헬퍼 (이유: reasoning 모델은 { } 를 생각에 섞음)
# --------------------------------------------------------------------------- #
def _head(raw: str, n: int = 200) -> str:
    r = (raw or "").strip()
    return r[:n] + ("…" if len(r) > n else "")


def _find_json_object(text: str):
    """깊이 균형으로 완전한 dict 조각 중 마지막으로 실제 파싱되는 것을 반환."""
    best = None
    start = 0
    t = text or ""
    while True:
        i = t.find("{", start)
        if i < 0:
            break
        depth, j = 0, i
        while j < len(t):
            if t[j] == "{":
                depth += 1
            elif t[j] == "}":
                depth -= 1
                if depth == 0:
                    seg = t[i:j + 1]
                    try:
                        obj = json.loads(seg)
                        if isinstance(obj, dict):
                            best = obj
                    except Exception:  # noqa: BLE001
                        pass
                    start = j + 1
                    break
            j += 1
        else:
            start = j
    return best
