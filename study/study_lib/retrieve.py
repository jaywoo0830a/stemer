"""검색 — 토픽(제목·섹션) → 생성용 컨텍스트.

클라이언트 관점:
    ctx = retrieve(topic, store=store, embedder=embedder, reranker=reranker)
    ctx.primary    # 토픽의 매핑 섹션 청크 (항상 포함)
    ctx.crossref   # RRF(어휘+밀집) 융합 후 리랭크 상위 N
    for chunk in ctx.chunks:   # primary 먼저, 그다음 crossref
        ...
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from .embed import Embedder, embed_text
from .store import IndexStore

_SECTION_NUM_RE = re.compile(r"\d+(?:\.\d+)+")


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    book_id: str
    section: str
    text: str
    score: float


@dataclass(frozen=True)
class RetrievedContext:
    topic_id: str
    book_id: str
    primary: tuple[RetrievedChunk, ...]
    crossref: tuple[RetrievedChunk, ...]

    @property
    def chunks(self) -> tuple[RetrievedChunk, ...]:
        return self.primary + self.crossref

    def texts(self) -> list[str]:
        return [c.text for c in self.chunks]


class Reranker(Protocol):
    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


def _wanted_sections(section: str | None) -> set[str]:
    if not section:
        return set()
    return {s.strip() for s in re.split(r"[,;]", section) if s.strip()}


def matches_section(heading: str, wanted: set[str]) -> bool:
    """헤딩 '3.5 The Limit ...' 과 매핑 섹션 {'3.5'} 이 같은지 (번호 토큰 비교)."""
    if not wanted:
        return False
    heading_nums = set(_SECTION_NUM_RE.findall(heading))
    return bool(heading_nums & wanted)


def _rchunk(obj: object, score: float) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=obj.chunk_id, book_id=obj.book_id,
                          section=obj.section, text=obj.text, score=score)


def _rrf_merge(*rankings: Sequence[object], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion — 여러 순위 목록을 {chunk_id: 점수} 로 합산."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            cid = item.chunk_id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return scores


def rerank_top(query: str, candidates: Sequence[RetrievedChunk],
               reranker: Reranker | None, n: int) -> list[RetrievedChunk]:
    """리랭커로 후보를 재정렬하고 상위 n 개를 자른다. 없으면 원순서 유지."""
    if reranker is None:
        return list(candidates[:n])
    docs = [c.text for c in candidates]
    scores = reranker.score(query, docs)
    ordered = sorted(zip(candidates, scores), key=lambda t: (-t[1], t[0].chunk_id))
    return [c for c, _ in ordered[:n]]


def retrieve(topic: object, *, store: IndexStore, embedder: Embedder,
             reranker: Reranker | None = None, n_crossref: int = 5,
             n_candidates: int = 20, rrf_k: int = 60) -> RetrievedContext:
    """클라이언트가 쓰는 진입점 — 토픽 → 생성용 컨텍스트."""
    book_id = topic.book_id
    query = topic.title or topic.topic_id
    wanted = _wanted_sections(topic.section)

    # ① 섹션 매핑 — 매핑된 섹션 번호가 붙은 청크는 항상 primary
    primary_ids: set[str] = set()
    primary: list[RetrievedChunk] = []
    for c in store.chunks(book_id=book_id):
        if matches_section(c.section, wanted):
            primary_ids.add(c.chunk_id)
            primary.append(_rchunk(c, 1.0))

    # ② 후보 — 어휘 + 밀집 검색을 RRF 로 융합 (primary 제외)
    query_vec = embed_text(embedder, query)
    dense = store.search_dense(query_vec, k=n_candidates, book_id=book_id)
    lexical = store.search_text(query, k=n_candidates, book_id=book_id)
    fused = _rrf_merge(dense, lexical, k=rrf_k)

    sources = {h.chunk_id: h for h in (*dense, *lexical)}
    candidates: list[RetrievedChunk] = []
    for cid in sorted(fused, key=lambda c: (-fused[c], c)):
        if cid in primary_ids or cid not in sources:
            continue
        candidates.append(_rchunk(sources[cid], fused[cid]))

    # ③ 리랭크 → crossref
    crossref = rerank_top(query, candidates, reranker, n_crossref)
    return RetrievedContext(topic_id=topic.topic_id, book_id=book_id,
                            primary=tuple(primary), crossref=tuple(crossref))
