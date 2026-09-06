"""임베딩 계약 — 클라이언트 관점 테스트.

임베딩 모델은 무겁다(선택 의존성). 그래서 계약은 결정적 `StubEmbedder` 위에서 고정하고,
실제 모델 어댑터(`TransformerEmbedder`)는 의존성이 없을 때 **설치 안내 오류**를 보장한다.
"""
import importlib.util

import pytest

from study_lib.embed import StubEmbedder, TransformerEmbedder, embed_text


def test_embed_texts_returns_one_fixed_dim_vector_per_text():
    e = StubEmbedder(dim=8)
    vecs = e.embed_texts(["derivative", "the limit of a sequence", "미분"])
    assert len(vecs) == 3
    assert all(len(v) == 8 for v in vecs)


def test_stub_embedder_is_deterministic_and_identical_texts_match():
    e = StubEmbedder(dim=8)
    assert e.embed_texts(["same text"]) == e.embed_texts(["same text"])
    a, b = e.embed_texts(["limit", "limit"])
    assert a == b


def test_batch_size_does_not_change_results():
    e = StubEmbedder(dim=8)
    texts = [f"term {i}" for i in range(10)]
    assert e.embed_texts(texts, batch_size=1) == e.embed_texts(texts, batch_size=100)


def test_empty_input_returns_empty_list():
    e = StubEmbedder(dim=8)
    assert e.embed_texts([]) == []


def test_non_positive_batch_size_is_rejected():
    e = StubEmbedder(dim=8)
    with pytest.raises(ValueError, match="batch_size"):
        e.embed_texts(["x"], batch_size=0)


def test_embed_text_returns_a_vector_of_embedder_dim():
    e = StubEmbedder(dim=4)
    assert len(embed_text(e, "hello")) == 4


@pytest.mark.skipif(importlib.util.find_spec("sentence_transformers") is not None,
                    reason="sentence-transformers installed")
def test_transformer_embedder_without_dependency_gives_actionable_error():
    with pytest.raises(RuntimeError, match="sentence-transformers"):
        TransformerEmbedder("BAAI/bge-m3")
