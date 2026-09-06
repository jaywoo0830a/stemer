"""검색 계약 — 클라이언트 관점 테스트.

토픽(제목·섹션) → 생성용 컨텍스트: 매핑된 섹션 청크는 항상 `primary`, 그 외는
어휘+밀집 RRF 융합과 리랭크를 거쳐 `crossref` 로. 테스트는 의미 기반 더블
(`VocabularyEmbedder`/`OverlapReranker`)로 순위를 결정적으로 검증한다.
"""
import types

from study_lib.chunk import chunk_markdown
from study_lib.retrieve import rerank_top, retrieve, RetrievedChunk
from study_lib.store import IndexStore
from tests.doubles import OverlapReranker, ReverseReranker, VocabularyEmbedder

MD = """# Chapter 3 Limits
## 3.1 The Limit of a Function
The limit of a function f at a point describes its behavior nearby.
## 3.2 Computing Limits
Use continuity and algebraic rules to compute limits.
## 3.5 The Limit of a Sequence
A sequence (a_n) converges to L when its terms approach L.
# Chapter 4 Derivatives
## 4.1 The Derivative
The derivative measures the rate of change of a function.
"""


def _indexed(book_id: str = "calc"):
    store = IndexStore()
    embedder = VocabularyEmbedder(dim=256)
    chunks = chunk_markdown(MD, book_id=book_id)
    store.add_many(chunks, vectors=embedder.embed_texts([c.text for c in chunks]))
    return store, embedder


def _topic(book_id="calc", title="Limit of a function", section="3.5"):
    return types.SimpleNamespace(topic_id="limit-of-sequence", book_id=book_id,
                                 title=title, section=section)


def test_primary_contains_the_mapped_section_chunk():
    store, embedder = _indexed()
    ctx = retrieve(_topic(), store=store, embedder=embedder)
    assert len(ctx.primary) == 1
    assert ctx.primary[0].section.startswith("3.5")


def test_crossref_excludes_primary_and_keeps_related_section():
    store, embedder = _indexed()
    ctx = retrieve(_topic(), store=store, embedder=embedder)
    primary_ids = {c.chunk_id for c in ctx.primary}
    crossref_ids = {c.chunk_id for c in ctx.crossref}
    assert primary_ids.isdisjoint(crossref_ids)
    assert ctx.crossref                                          # 비어 있으면 안 됨
    assert any(c.section.startswith("3.1") for c in ctx.crossref)  # 같은 개념 섹션


def test_search_is_scoped_to_the_topic_book():
    store = IndexStore()
    embedder = VocabularyEmbedder(dim=256)
    variants = [("calc", MD), ("other", MD.replace("a sequence", "a series"))]
    for book, md in variants:
        chunks = chunk_markdown(md, book_id=book)
        store.add_many(chunks, vectors=embedder.embed_texts([c.text for c in chunks]))
    ctx = retrieve(_topic(book_id="calc"), store=store, embedder=embedder)
    assert ctx.book_id == "calc"
    assert all(c.book_id == "calc" for c in ctx.chunks)


def test_topic_without_section_has_no_primary_but_still_gets_crossref():
    store, embedder = _indexed()
    ctx = retrieve(_topic(section=None), store=store, embedder=embedder)
    assert ctx.primary == ()
    assert ctx.crossref


def test_overlap_reranker_pulls_the_matching_section_up():
    store, embedder = _indexed()
    topic = types.SimpleNamespace(topic_id="deriv", book_id="calc",
                                  title="derivative", section=None)
    ctx = retrieve(topic, store=store, embedder=embedder, reranker=OverlapReranker())
    assert ctx.crossref
    assert ctx.crossref[0].section.startswith("4.1")


def test_rerank_top_sorts_by_reranker_and_caps():
    candidates = [
        RetrievedChunk("a", "b", "s1", "text a", 0.0),
        RetrievedChunk("b", "b", "s2", "text b", 0.0),
        RetrievedChunk("c", "b", "s3", "text c", 0.0),
    ]

    class Ascending:
        def score(self, query, documents):
            return [float(i) for i in range(len(documents))]

    assert [c.chunk_id for c in rerank_top("q", candidates, Ascending(), n=2)] == ["c", "b"]
    # 리랭커가 없으면 원순서 유지 + 캡
    assert [c.chunk_id for c in rerank_top("q", candidates, None, n=2)] == ["a", "b"]
