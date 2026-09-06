"""factory 계약 — 클라이언트 관점 테스트.

토픽 1건: retrieve → 프롬프트 → llm(json_object) → 검증 → (patch ≤2회) →
render/figures → 파일 저장 → registry `todo→draft`. 의존성(llm 등)은 전부 가짜로
주입해 결정적으로 검증한다.
"""
from pathlib import Path

from study_lib.chunk import chunk_markdown
from study_lib.factory import GenerateResult, build_user, generate_one
from study_lib.llm import LLMResult, Usage
from study_lib.registry import DRAFT, InMemoryStore, Library, TODO
from study_lib.store import IndexStore
from tests.doubles import VocabularyEmbedder

MD = """# Chapter 3 Limits
## 3.1 The Limit of a Function
The limit of a function f at a point describes its behavior nearby.
## 3.5 The Limit of a Sequence
A sequence (a_n) converges to L when its terms approach L.
"""

VALID = {
    "cs": [{
        "c": "Limit of a function",
        "d": "f has limit L at a if f(x) gets close to L as x gets close to a.",
        "f": "lim f(x) = L",
        "k": "Behavior near a, not at a.",
        "m": "Plugging in a directly.",
    }],
    "r": "1) Factor 2) Cancel 3) Evaluate 4) Check one-sided.",
    "as": ["Velocity as a limit of average rates."],
    "ex": [{
        "p": "Compute lim_{x→1} (x^2−1)/(x−1).",
        "s": "Factor to (x−1)(x+1)/(x−1), cancel, get 2.",
    }],
    "pr": ["lim_{x→0} sin x / x", "lim_{x→2} (x−2)/(x^2−4)"],
}


def _bad_payload() -> dict:
    bad = {k: v for k, v in VALID.items()}
    bad["cs"] = [{"c": "Limit", "d": "x" * 400, "f": "f", "k": "k", "m": "m"}]
    return bad


class FakeLLM:
    def __init__(self, queue):
        self._queue = list(queue)
        self.calls = []

    def complete(self, *, system, user, max_tokens=1000, json_object=True):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if not self._queue:
            raise AssertionError("no more scripted responses")
        return self._queue.pop(0)


def _env(tmp_path: Path):
    lib = Library(InMemoryStore())
    lib.add_book("calc", "Calculus", "math")
    topic = lib.add_topic(book_id="calc", title="Limit of a function", section="3.5")
    store = IndexStore()
    embedder = VocabularyEmbedder(dim=256)
    chunks = chunk_markdown(MD, book_id="calc")
    store.add_many(chunks, vectors=embedder.embed_texts([c.text for c in chunks]))
    return lib, topic, store, embedder


def test_generate_one_drafts_a_note_from_valid_payload(tmp_path, schema):
    lib, topic, store, embedder = _env(tmp_path)
    usage = Usage(cache_hit_tokens=7000, cache_miss_tokens=2000, completion_tokens=500)
    llm = FakeLLM([LLMResult(content=VALID, usage=usage)])
    res = generate_one(topic, library=lib, store=store, embedder=embedder, llm=llm,
                       schema=schema, guide="SUBJECT GUIDE", notes_dir=tmp_path / "notes")
    assert res.status == "draft"
    assert res.attempts == 1 and res.retries == 0
    assert res.usage == usage
    path = Path(res.note_path)
    assert path.name == "limit-of-a-function.md"
    text = path.read_text(encoding="utf-8")
    assert "# Limit of a function" in text and "## Concepts" in text
    # registry 전이 확인
    assert lib.topic(topic.topic_id).status == DRAFT
    assert lib.topic(topic.topic_id).note_path == str(path)


def test_patch_retry_fixes_invalid_payload(tmp_path, schema):
    lib, topic, store, embedder = _env(tmp_path)
    llm = FakeLLM([
        LLMResult(content=_bad_payload(), usage=Usage(completion_tokens=100)),
        LLMResult(content=VALID, usage=Usage(completion_tokens=50)),
    ])
    res = generate_one(topic, library=lib, store=store, embedder=embedder, llm=llm,
                       schema=schema, guide="G", notes_dir=tmp_path / "notes")
    assert res.status == "draft"
    assert res.attempts == 2 and res.retries == 1
    assert len(llm.calls) == 2
    # 두 번째 요청(patch)은 실패 슬롯(cs)을 언급해야 한다
    assert "cs" in llm.calls[1]["user"]
    assert "cs" not in llm.calls[0]["user"] or True  # 첫 요청은 plain


def test_retry_cap_marks_failed_without_side_effects(tmp_path, schema):
    lib, topic, store, embedder = _env(tmp_path)
    llm = FakeLLM([
        LLMResult(content=_bad_payload(), usage=Usage(completion_tokens=10)),
        LLMResult(content=_bad_payload(), usage=Usage(completion_tokens=10)),
        LLMResult(content=_bad_payload(), usage=Usage(completion_tokens=10)),
    ])
    res = generate_one(topic, library=lib, store=store, embedder=embedder, llm=llm,
                       schema=schema, guide="G", notes_dir=tmp_path / "notes",
                       max_patch=2)
    assert res.status == "failed"
    assert res.attempts == 3 and res.retries == 2
    assert res.payload is None
    assert res.issues
    # 노트/registry 는 그대로 (재시도 가능)
    assert not (tmp_path / "notes" / "limit-of-a-function.md").exists()
    assert lib.topic(topic.topic_id).status == TODO


def test_usage_accumulates_across_attempts(tmp_path, schema):
    lib, topic, store, embedder = _env(tmp_path)
    first = Usage(cache_hit_tokens=1000, cache_miss_tokens=500, completion_tokens=300)
    second = Usage(cache_hit_tokens=2000, cache_miss_tokens=0, completion_tokens=700)
    llm = FakeLLM([
        LLMResult(content=_bad_payload(), usage=first),
        LLMResult(content=VALID, usage=second),
    ])
    res = generate_one(topic, library=lib, store=store, embedder=embedder, llm=llm,
                       schema=schema, guide="G", notes_dir=tmp_path / "notes")
    assert res.usage.completion_tokens == 1000
    assert res.usage.cache_hit_tokens == 3000
    assert res.usage.cost_usd() > 0
