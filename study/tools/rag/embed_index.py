"""Dense embedding index (Chroma) built from chunk texts."""
from __future__ import annotations

import logging

import chromadb
from tqdm import tqdm

from .chunk import Chunk
from .config import settings

log = logging.getLogger("rag")

_embed_model = None
_client = None


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer

        log.info("Loading embedding model %s ...", settings.embed_model)
        _embed_model = SentenceTransformer(settings.embed_model)
    return _embed_model


def get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(settings.index_dir / "chroma"))
    return _client


def get_collection():
    return get_client().get_or_create_collection(
        name="chunks", metadata={"hnsw:space": "cosine"}
    )


def _embed_batch(texts: list[str]) -> list[list[float]]:
    model = get_embed_model()
    out = model.encode(
        texts,
        batch_size=settings.embed_batch,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [vec.tolist() for vec in out]


def index_book(book_id: str, chunks: list[Chunk]) -> None:
    """Replace all vectors of one book with fresh embeddings."""
    if not chunks:
        return
    col = get_collection()
    try:
        col.delete(where={"book_id": book_id})
    except Exception as exc:  # noqa: BLE001 — delete-by-where may fail on old chroma
        log.warning("Could not clear old vectors for %s: %s", book_id, exc)

    texts = [c.text for c in chunks]
    ids = [c.chunk_id for c in chunks]
    metas = [c.metadata for c in chunks]

    log.info("Embedding %d chunks for %s ...", len(chunks), book_id)
    embeddings: list[list[float]] = []
    for i in tqdm(range(0, len(texts), settings.embed_batch), desc="embed"):
        embeddings.extend(_embed_batch(texts[i : i + settings.embed_batch]))

    col.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metas)
    log.info("Vector index updated for %s (%d chunks).", book_id, len(chunks))


def dense_search(query: str, k: int, book_id: str | None = None) -> list[dict]:
    """Cosine-similarity search, optionally restricted to one book."""
    col = get_collection()
    model = get_embed_model()
    q = model.encode([query], normalize_embeddings=True, convert_to_numpy=True)
    where = {"book_id": book_id} if book_id else None
    res = col.query(
        query_embeddings=[q[0].tolist()],
        n_results=k,
        where=where,
        include=["metadatas", "documents", "distances"],
    )
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    out: list[dict] = []
    for i, cid in enumerate(ids):
        m = metas[i] or {}
        out.append(
            {
                "chunk_id": cid,
                "book_id": m.get("book_id", ""),
                "chapter": m.get("chapter", ""),
                "section": m.get("section", ""),
                "text": docs[i] if i < len(docs) else "",
                "score": 1.0 - (dists[i] if i < len(dists) and dists[i] is not None else 0.0),
            }
        )
    return out
