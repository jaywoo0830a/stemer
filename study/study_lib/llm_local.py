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
        """OpenAI 호환 `/v1/chat/completions` 를 쓴다(현재 백엔드 diffuse LLaDA 는
        chat 엔드포인트만 노출 — raw `/completion` 이 없다).

        - 이전 llama-server 의 `reasoning_content` 를 chat 해석에서 무시해 content
          마크다운만 얻는다(R1 생각 누출 방지).
        - LOCAL_LLM_CHAT=0 을 주면 예전 llama raw `/completion` 을 쓴다(호환용).
        """
        import httpx  # 선택 의존성
        mode = os.environ.get("LOCAL_LLM_CHAT", "1").strip().lower()

        def _post(url: str, body: dict) -> "httpx.Response":
            with httpx.Client(timeout=self.timeout) as c:
                return c.post(url, json=body)

        def _extract(data: dict) -> str:
            if "choices" in data:
                msg = data["choices"][0].get("message", {})
                content = msg.get("content")
            else:
                content = data.get("content")
            if content is None:
                content = ""
            if isinstance(content, list):
                parts: list[str] = []
                for p in content:
                    parts.append(p.get("text", "") if isinstance(p, dict)
                                 else str(p))
                content = "".join(parts)
            return str(content or "").strip()

        use_chat = mode != "0"        # 기본 chat; LOCAL_LLM_CHAT=0 → raw /completion
        if use_chat:
            url = f"{self.base_url}/v1/chat/completions"
            body = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "stream": False,
            }
        else:
            url = f"{self.base_url}/completion"
            body = {"prompt": (system + "\n\n" + user).strip(),
                    "max_tokens": max_tokens, "temperature": 0.0,
                    "stream": False}
        try:
            r = _post(url, body)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"local llm failed: {exc}") from None
        if r.status_code >= 400:
            raise LLMError(f"local llm HTTP {r.status_code} on {url}: "
                           f"{r.text[:200]}")
        return _extract(r.json())

    def count_tokens(self, text: str) -> int:
        """llama.cpp 서버의 `/tokenize` 로 정확한 토큰 수를 센다(추정이 아닌 실측).

        실패(구버전/장애)하면 문자 기반 근사로 폴백한다. 단일 프롬프트의 passage
        패킹(입력 ↓32768) 예산 계산에 쓰인다.
        """
        if not text:
            return 0
        import httpx  # 선택 의존성
        try:
            with httpx.Client(timeout=30.0) as c:
                r = c.post(f"{self.base_url}/tokenize",
                           json={"content": text})
            if r.status_code == 200:
                data = r.json()
                toks = data.get("tokens")
                if isinstance(toks, list):
                    return len(toks)
        except Exception:  # noqa: BLE001 — 폴백
            pass
        # 폴백 근사: 한글/혼합은 문당 대략 1~2토큰, 한글 3글자당 ~1토큰 가정.
        return max(1, (len(text) + 2) // 3)

    def complete(self, *, system: str, user: str, max_tokens: int = 1000,
                 json_object: bool = True) -> LLMResult:
        # reasoning 모델은 생각(reasoning)이 max 를 삼켜 content 가 빈 채
        # finish(len)될 수 있다 → 1회 비었으면 조금씩 늘려 재시도(최대 3회).
        # CPU/작은 ctx(16384)에서는 max_tokens 를 키워도 ctx 를 못 넘으니
        # 과도한 증배(×2)는 무의미 → 소폭(+2048)만 늘린다(권고안 D).
        mt = max_tokens
        raw = ""
        for _ in range(3):
            raw = strip_thinking(self._chat(system, user, mt) or "").strip()
            if raw:
                if not json_object:
                    return LLMResult(content=raw, usage=Usage())
                try:
                    return LLMResult(content=_parse_json(raw), usage=Usage())
                except LLMError:
                    # JSON 아님 — 헤드 몇 자를 에러에 담아 진단 가능하게
                    raise LLMError(
                        "local model did not return a JSON object; head: "
                        + _head(raw, 200)
                    ) from None
            mt = mt + 2048   # reasoning 만 다 쓴 것 → 소폭 예산 확대
        raise LLMError("local model returned empty content (0/3)" + _tail(raw))


_THINK_RE = re.compile(r"<think>\s*</think>|<think>.*?</think>", re.DOTALL)


def strip_thinking(text: str) -> str:
    """llama-server(Phi-4 등)가 content 에 태그로 던진 <think>…</think> 를 제거한다.

    - Phi-4-mini-reasoning 은 (chat transport) 최종 본문 앞에 <think>\n…\n</think> 를
      content 로 그대로 포함하므로 잘라야 한다(권고안.md).
    - 앞/중간/여러 개 겹침·빈 think 모두 제거 후 남은 본문의 공백만 정리.
    """
    if not text:
        return text
    prev = None
    t = text
    while prev != t:          # 겹침/중첩 대비 반복 제거
        prev = t
        t = _THINK_RE.sub("", t)
    return t.strip()


def _head(raw: str, n: int = 200) -> str:
    r = (raw or "").strip()
    return r[:n] + ("…" if len(r) > n else "")


def _tail(raw: str, n: int = 120) -> str:
    r = (raw or "").strip()
    return ("..." + r[-n:]) if len(r) > n else r


def _find_json_object(text: str):
    """여러 {…} 후보 중 '실제로 완전한 dict'로 파싱되는 마지막 것을 반환.

    R1 과 같은 reasoning 모델은 응답 앞에 생각(중괄호 포함 가능)을 섞는다.
    첫 { … 마지막 } 만으로 자르면 가짜 조각이 잡힌다 → 깊이 균형을 맞춰 끝나는
    각 후보를 json.loads 로 시도해, dict 인 것 중 마지막(진짜 답)을 고른다.
    """
    best = None
    start = 0
    while True:
        i = text.find("{", start)
        if i < 0:
            break
        depth = 0
        j = i
        while j < len(text):
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    seg = text[i:j + 1]
                    try:
                        obj = json.loads(seg)
                        if isinstance(obj, dict):
                            best = obj  # 뒤 후보가 실제 답일 가능성 높아 덮는다
                    except Exception:  # noqa: BLE001
                        pass
                    start = j + 1
                    break
            j += 1
        else:
            start = j
    return best


def _parse_json(raw: str) -> Any:
    """모델 응답에서 온전한 JSON 객체(dict) 추출. 못 찾으면 LLMError."""
    text = (raw or "")
    if not text.strip():
        raise LLMError("local model returned empty content (no JSON)")
    obj = _find_json_object(text)
    if obj is None:
        raise LLMError("local model did not return a JSON object")
    return obj


def pick_generate_llm():
    """LOCAL_LLM=1 이면 로컬 llama.cpp server(LocalClient), 아니면 DeepSeek."""
    if os.environ.get("LOCAL_LLM", "0") == "1":
        return LocalClient()
    from .llm import FlashClient  # 지연 import — DeepSeek 경로는 키 필요 시에만
    return FlashClient()
