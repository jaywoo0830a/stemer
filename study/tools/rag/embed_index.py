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


# chromadb rejects a single add() larger than its max batch size (5461 in
# 0.5.x) — stay safely below it.
CHROMA_ADD_BATCH = 5000


def index_book(book_id: str, chunks: list[Chunk], force: bool = False) -> None:
    """Replace all vectors of one book with fresh embeddings.

    Guard: if vectors already exist and force is False, skip embedding —
    retries after a crash must not redo the expensive embedding pass.
    """
    if not chunks:
        return

    texts = [c.text for c in chunks]
    ids = [c.chunk_id for c in chunks]

    if settings.use_pg:
        from .pgstore import PgStore

        pg = PgStore(settings.database_url)
        if not force and pg.has_embeddings(book_id):
            log.info("Vectors already present for %s — skipping embedding.", book_id)
            return
        # Chunk rows are added by index_one_book / reindex before this call;
        # add_chunks is idempotent (ON CONFLICT DO NOTHING), so just make sure
        # they exist, then fill the embedding column.
        pg.add_chunks(chunks)
        log.info("Embedding %d chunks for %s ...", len(chunks), book_id)
        embeddings: list[list[float]] = []
        for i in tqdm(range(0, len(texts), settings.embed_batch), desc="embed"):
            embeddings.extend(_embed_batch(texts[i : i + settings.embed_batch]))
        pg.set_embeddings(book_id, ids, embeddings)
        return

    # ---- chroma backend (default when DATABASE_URL is unset) ----
    col = get_collection()

    existing = col.get(where={"book_id": book_id}, limit=1)
    if not force and existing and existing.get("ids"):
        log.info("Vectors already present for %s — skipping embedding.", book_id)
        return

    try:
        col.delete(where={"book_id": book_id})
    except Exception as exc:  # noqa: BLE001 — delete-by-where may fail on old chroma
        log.warning("Could not clear old vectors for %s: %s", book_id, exc)

    metas = [c.metadata for c in chunks]

    log.info("Embedding %d chunks for %s ...", len(chunks), book_id)
    embeddings = []
    for i in tqdm(range(0, len(texts), settings.embed_batch), desc="embed"):
        embeddings.extend(_embed_batch(texts[i : i + settings.embed_batch]))

    # chromadb rejects huge add() calls — split into capped batches.
    for start in range(0, len(ids), CHROMA_ADD_BATCH):
        end = start + CHROMA_ADD_BATCH
        col.add(
            ids=ids[start:end],
            embeddings=embeddings[start:end],
            documents=texts[start:end],
            metadatas=metas[start:end],
        )
    log.info("Vector index updated for %s (%d chunks).", book_id, len(chunks))


def dense_search(query: str, k: int, book_id: str | None = None) -> list[dict]:
    """Cosine-similarity search, optionally restricted to one book."""
    model = get_embed_model()
    q = model.encode([query], normalize_embeddings=True, convert_to_numpy=True)

    if settings.use_pg:
        from .pgstore import PgStore

        return PgStore(settings.database_url).dense_search(q[0].tolist(), k, book_id)

    col = get_collection()
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
