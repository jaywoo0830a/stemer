"""품질 강화 의존성 계약 — BM25 어휘 검색·교차인코더 리랭커.

설치한 라이브러리가 실제로 품질에 연결되는지 확인한다.
- rank-bm25: 검색_text 가 단순 단어 겹침 대신 BM25 로 문서를 순위화
- sentence-transformers CrossEncoder: 리랭커 어댑터(없으면 설치 안내 오류)
"""
import importlib.util

import pytest

from study_lib.retrieve import CrossEncoderReranker
from study_lib.store import IndexStore, IndexedChunk


def _doc(store, cid, book, text):
    store.add(IndexedChunk(cid, book, "1.1", text, seq=0))


def test_bm25_ranks_docs_containing_all_query_terms_first():
    store = IndexStore()
    _doc(store, "both", "calc", "The limit of a sequence is L.")
    _doc(store, "only", "calc", "A limit can be undefined.")
    hits = store.search_text("limit sequence", k=5, book_id="calc")
    assert hits[0].chunk_id == "both"   # 두 단어를 다 가진 문서가 위로


def test_bm25_respects_book_filter():
    store = IndexStore()
    _doc(store, "a", "calc", "derivative slope")
    _doc(store, "b", "other", "derivative slope")
    assert {h.chunk_id for h in store.search_text("derivative", k=5, book_id="calc")} == {"a"}


@pytest.mark.skipif(importlib.util.find_spec("sentence_transformers") is not None,
                    reason="sentence-transformers installed")
def test_cross_encoder_reranker_without_dependency_gives_actionable_error():
    with pytest.raises(RuntimeError, match="sentence-transformers"):
        CrossEncoderReranker()
