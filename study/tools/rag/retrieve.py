"""Hybrid retrieval: BM25 + dense embeddings fused with RRF, then reranked.

Pipeline: 30 BM25 + 30 dense -> RRF fusion -> top-20 pool ->
bge-reranker-v2-m3 -> top-N cross-references.
"""
from __future__ import annotations

import logging

from . import embed_index
from .config import settings
from .store import Hit, Store

log = logging.getLogger("rag")

_reranker = None


def get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        log.info("Loading reranker %s ...", settings.rerank_model)
        _reranker = CrossEncoder(settings.rerank_model)
    return _reranker


def _to_hit(d: dict) -> Hit:
    return Hit(
        chunk_id=d["chunk_id"],
        book_id=d["book_id"],
        chapter=d["chapter"],
        section=d["section"],
        text=d["text"],
        score=float(d.get("score", 0.0)),
    )


def hybrid_candidates(store: Store, query: str, book_id: str | None, k: int) -> list[Hit]:
    bm = store.bm25_search(query, k, book_id)
    dense = [_to_hit(d) for d in embed_index.dense_search(query, k, book_id)]

    rrf: dict[str, float] = {}
    by_id: dict[str, Hit] = {}
    for ranked in (bm, dense):
        for rank, h in enumerate(ranked):
            rrf[h.chunk_id] = rrf.get(h.chunk_id, 0.0) + 1.0 / (60.0 + rank + 1)
            by_id[h.chunk_id] = h
    order = sorted(rrf, key=rrf.get, reverse=True)[: settings.rerank_pool]
    return [by_id[cid] for cid in order]


def retrieve(
    store: Store, query: str, book_id: str | None = None, k: int | None = None
) -> list[Hit]:
    """Return the top-k most relevant chunks for the query (cross-references)."""
    k = k or settings.n_crossref
    pool = hybrid_candidates(store, query, book_id, settings.top_k_candidates)
    if not pool:
        return []
    if len(pool) <= k:
        return pool

    reranker = get_reranker()
    pairs = [(query, h.text[:1200]) for h in pool]
    scores = reranker.predict(pairs, batch_size=16, show_progress_bar=False)
    scored = sorted(zip(pool, scores), key=lambda t: -float(t[1]))
    return [h for h, _ in scored[:k]]


def primary_sections(
    store: Store,
    topic: str,
    section_refs: list[str],
    book_id: str | None = None,
    limit: int = 8,
) -> list[Hit]:
    """First-priority candidates: the exact sections listed in TOPICS.md."""
    return store.find_by_refs(section_refs, topic, book_id, limit)
