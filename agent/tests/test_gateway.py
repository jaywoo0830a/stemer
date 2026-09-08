"""gateway 계약 — llama.cpp(OpenAI 호환) + Ollama 임베딩 호출/파싱 결정성."""
import pytest

from agent.gateway import (Gateway, GatewayError, OllamaEmbed, strip_thinking,
                           _find_json_object)
from agent.tests._fakes import FakeTransport


def test_chat_returns_cleaned_content():
    t = FakeTransport(chat_reply="<think>hmm</think>Final answer.")
    gw = Gateway("http://x:8081", transport=t)
    assert gw.chat(system="s", user="u") == "Final answer."


def test_chat_strips_overlapping_think():
    t = FakeTransport(chat_reply="<think><think>a</think>b</think>done")
    gw = Gateway("http://x:8081", transport=t)
    assert gw.chat(system="s", user="u") == "done"


def test_chat_uses_chat_completions_endpoint():
    t = FakeTransport(chat_reply="ok")
    gw = Gateway("http://x:8081", transport=t)
    gw.chat(system="sys", user="user1")
    assert t.calls[0]["url"] == "http://x:8081/v1/chat/completions"
    body = t.calls[0]["body"]
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "sys"
    assert body["messages"][1]["content"] == "user1"


def test_chat_json_returns_dict():
    t = FakeTransport(chat_json_reply={"tasks": [{"id": 1, "action": "xf"}]})
    gw = Gateway("http://x:8081", transport=t)
    obj = gw.chat_json(system="Return ONLY a JSON object", user="", max_tokens=100)
    assert obj == {"tasks": [{"id": 1, "action": "xf"}]}


def test_chat_json_parses_noise_around_object():
    t = FakeTransport(chat_reply='prefix {"a":1} tail {"b":2}')
    # chat_json 이 chat() 을 호출하되 .chat_reply 스크립트 → 마지막 온전한 dict
    gw = Gateway("http://x:8081", transport=t)
    obj = gw.chat_json(system="s", user="u")
    assert obj == {"b": 2}


def test_chat_json_empty_raises():
    t = FakeTransport(chat_reply="no object here")
    gw = Gateway("http://x:8081", transport=t)
    with pytest.raises(GatewayError):
        gw.chat_json(system="s", user="u")


def test_embed_uses_api_embed():
    t = FakeTransport(embed_reply=[[0.1, 0.2], [0.3, 0.4]])
    gw = Gateway("http://x:11434", transport=t)
    vecs = gw.embed(["a", "b"], model="qwen2.5:3b")
    assert vecs == [[0.1, 0.2], [0.3, 0.4]]
    assert t.calls[0]["url"] == "http://x:11434/api/embed"
    assert t.calls[0]["body"]["model"] == "qwen2.5:3b"


def test_embed_falls_back_to_openai_v1_when_llama_no_embeddings():
    """11434 가 llama(--embeddings 없음) 면 /api/embed 로 501 → /v1/embeddings 재시도."""
    class LlamaEmbed:
        def __init__(self):
            self.calls = []
        def post_json(self, url, body, timeout):
            self.calls.append(url)
            if url.endswith("/api/embed"):
                raise GatewayError("HTTP 501 … does not support embeddings. Start it with `--embeddings`")
            # llama --embeddings(OpenAI 호환)
            return {"data": [{"embedding": [0.5, 0.6]}]}

    tr = LlamaEmbed()
    gw = Gateway("http://x:11434", transport=tr)
    vecs = gw.embed(["hello"], model="qwen2.5:3b")
    assert vecs == [[0.5, 0.6]]
    assert tr.calls == ["http://x:11434/api/embed", "http://x:11434/v1/embeddings"]


def test_transport_http_error_wrapped(monkeypatch):
    class Boom:
        def post_json(self, url, body, timeout):
            raise GatewayError("HTTP 500 crash")

    gw = Gateway("http://x", transport=Boom())
    with pytest.raises(GatewayError):
        gw.embed(["a"])


def test_ollama_embed_adapter():
    ft = FakeTransport(embed_reply=[[0.1, 0.2]])
    emb = OllamaEmbed("http://x:11434", model="m", transport=ft, dim=2)
    vecs = emb.embed_texts(["hello"])
    assert vecs[0] == (0.1, 0.2)


def test_strip_thinking_base():
    assert strip_thinking("<think>skip</think>out") == "out"
    assert strip_thinking("") == ""
    assert strip_thinking("no tag") == "no tag"


def test_find_json_object_balanced():
    assert _find_json_object('{"x": [1,{"y":2}]}') == {"x": [1, {"y": 2}]}
    assert _find_json_object("intro {a} mid") is None  # a 미정의 → json.loads 실패
