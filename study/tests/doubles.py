"""테스트 더블 — 검색/리랭크 순위를 의미적으로(결정적으로) 검증하기 위한 가짜.

`StubEmbedder`(해시)는 의미 유사성이 없어 순위 테스트에 못 쓴다.
여기 있는 `VocabularyEmbedder`는 **단어 공유 기반** 벡터라 "같은 단어를 쓰는 청크가
더 가까움"을 결정적으로 보장한다.
"""
from __future__ import annotations

import hashlib
import math

from study_lib.embed import BaseEmbedder
from study_lib.tokens import terms


class VocabularyEmbedder(BaseEmbedder):
    """어휘 공유 기반 임베딩 — 공유 단어가 많을수록 코사인 유사도가 높다."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def encode(self, texts: list[str]) -> list:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for term in terms(text):
                idx = int(hashlib.md5(term.encode("utf-8")).hexdigest()[:8], 16) % self.dim
                vec[idx] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append(tuple(x / norm for x in vec))
        return out


class ReverseReranker:
    """순서를 뒤집는 가짜 리랭커 — rerank_top 의 정렬·캡을 결정적으로 검증."""

    def score(self, query: str, documents: list[str]) -> list[float]:
        return [float(-i) for i in range(len(documents))]


class OverlapReranker:
    """쿼리와 공유 단어 수로 점수 — 실제 리랭커의 '관련도 재정렬'을 흉내."""

    def score(self, query: str, documents: list[str]) -> list[float]:
        q = terms(query)
        return [float(len(q & terms(doc))) for doc in documents]
