"""임베딩 — 텍스트 → 벡터 (모델 인터페이스 + 어댑터).

클라이언트 관점:
    embedder = TransformerEmbedder("BAAI/bge-m3")   # 실제 모델 (선택 의존성)
    embedder = StubEmbedder(dim=8)                  # 계약 테스트용 (결정적 해시)
    vectors = embedder.embed_texts([c.text for c in chunks])  # 순서 보존
    store.add_many(chunks, vectors=vectors)

규칙: 같은 텍스트 → 같은 벡터(결정적), `batch_size`는 결과에 영향 없음,
모든 벡터의 길이는 `dim`.
"""
from __future__ import annotations

import hashlib
from typing import Protocol, Sequence

Vector = tuple[float, ...]


class Embedder(Protocol):
    dim: int

    def embed_texts(self, texts: Sequence[str], *, batch_size: int = 32) -> list[Vector]: ...


def embed_text(embedder: Embedder, text: str) -> Vector:
    return embedder.embed_texts([text])[0]


class BaseEmbedder:
    """공통 배치 루프 — `batch_size` 와 무관하게 같은 결과를 보장."""

    dim: int

    def embed_texts(self, texts: Sequence[str], *, batch_size: int = 32) -> list[Vector]:
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        out: list[Vector] = []
        for i in range(0, len(texts), batch_size):
            out.extend(self.encode(list(texts[i:i + batch_size])))
        return out

    def encode(self, texts: list[str]) -> list[Vector]:
        raise NotImplementedError


class StubEmbedder(BaseEmbedder):
    """결정적 해시 임베딩 — 계약 테스트용 (의미 유사성은 없음)."""

    def __init__(self, dim: int = 8) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self.dim = dim

    def encode(self, texts: list[str]) -> list[Vector]:
        out: list[Vector] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            out.append(tuple(digest[i % len(digest)] / 255.0 for i in range(self.dim)))
        return out


class TransformerEmbedder(BaseEmbedder):
    """bge-m3 계열 실제 모델 — 선택 의존성(sentence-transformers + torch)."""

    def __init__(self, model: str = "BAAI/bge-m3", *, device: str | None = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError:
            raise RuntimeError(
                "TransformerEmbedder needs 'sentence-transformers' (+torch): "
                "pip install 'sentence-transformers'"
            ) from None
        self._model = SentenceTransformer(model, device=device)
        dim_getter = getattr(self._model, "get_embedding_dimension", None)
        if callable(dim_getter):
            self.dim = int(dim_getter())          # sentence-transformers 최신 API
        else:
            self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> list[Vector]:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [tuple(float(x) for x in row) for row in vecs]
