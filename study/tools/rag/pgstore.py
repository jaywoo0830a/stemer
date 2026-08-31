"""PostgreSQL + pgvector chunk store (BM25-ish lexical + dense vectors).

Production backend activated by setting DATABASE_URL (see config.database_url).
When unset, the pipeline keeps using the SQLite+Chroma backend (store.py +
embed_index.py) so local tests and existing data keep working.

Schema:
    books  (book_id, title, source_pdf, indexed_at)
    chunks (chunk_id, book_id, chapter, section, text, seq, embedding vector(1024))

    - lexical:  pg_trgm GIN index over text (works for Korean + English)
    - dense:    pgvector HNSW (cosine) index over embedding

Requires the `db` compose service (pgvector/pgvector:pg17) and psycopg3.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .chunk import Chunk
from .store import Hit

log = logging.getLogger("rag")

_SECTION_RE = re.compile(r"\d{1,3}\.\d{1,3}")


class PgStore:
    """Postgres chunk store exposing the same interface as rag.store.Store."""

    def __init__(self, dsn: str, embed_dim: int = 1024):
        self.dsn = dsn
        self.embed_dim = embed_dim
        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        self._schema()

    @property
    def conn(self):
        """Raw psycopg connection (row_factory = dict_row)."""
        return self._conn

    # ---------------- schema ----------------
    def _schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS books (
                    book_id    TEXT PRIMARY KEY,
                    title      TEXT,
                    source_pdf TEXT,
                    indexed_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id  TEXT PRIMARY KEY,
                    book_id   TEXT,
                    chapter   TEXT,
                    section   TEXT,
                    text      TEXT,
                    seq       INTEGER,
                    embedding vector({self.embed_dim})
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_book ON chunks(book_id)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_embed ON chunks "
                "USING hnsw (embedding vector_cosine_ops)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_text_trgm ON chunks "
                "USING gin (text gin_trgm_ops)"
            )
        self._conn.commit()

    # ---------------- write path ----------------
    def add_book(self, book_id: str, title: str, source_pdf: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO books(book_id, title, source_pdf) VALUES(%s,%s,%s) "
                "ON CONFLICT(book_id) DO UPDATE SET title=EXCLUDED.title, "
                "source_pdf=EXCLUDED.source_pdf, indexed_at=now()",
                (book_id, title, source_pdf),
            )
        self._conn.commit()

    def delete_book(self, book_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE book_id = %s", (book_id,))
            cur.execute("DELETE FROM books WHERE book_id = %s", (book_id,))
        self._conn.commit()

    def add_chunks(self, chunks: Iterable[Chunk]) -> None:
        """Insert chunk rows (embeddings are filled later via set_embeddings)."""
        with self._conn.cursor() as cur:
            for c in chunks:
                cur.execute(
                    "INSERT INTO chunks(chunk_id, book_id, chapter, section, text, seq)"
                    " VALUES(%s,%s,%s,%s,%s,%s)"
                    " ON CONFLICT(chunk_id) DO NOTHING",
                    (c.chunk_id, c.book_id, c.chapter, c.section, c.text, c.seq),
                )
        self._conn.commit()

    def set_embeddings(self, book_id: str, chunk_ids: list[str], embeddings: list[list[float]]) -> None:
        """Bulk-update embeddings for one book's chunks."""
        with self._conn.cursor() as cur:
            for cid, vec in zip(chunk_ids, embeddings):
                cur.execute(
                    "UPDATE chunks SET embedding = %s WHERE chunk_id = %s",
                    (vec, cid),
                )
        self._conn.commit()
        log.info("pgvector updated for %s (%d embeddings).", book_id, len(chunk_ids))

    def has_embeddings(self, book_id: str) -> bool:
        """True if the book already has any stored embeddings (crash-resume guard)."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM chunks WHERE book_id = %s AND embedding IS NOT NULL LIMIT 1",
                (book_id,),
            )
            return cur.fetchone() is not None

    # ---------------- read path ----------------
    def book_title(self, book_id: str) -> str:
        with self._conn.cursor() as cur:
            cur.execute("SELECT title FROM books WHERE book_id = %s", (book_id,))
            row = cur.fetchone()
        return row["title"] if row else book_id

    def book_row(self, book_id: str) -> dict | None:
        """(title, source_pdf) for reindexing, or None."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT title, source_pdf FROM books WHERE book_id = %s", (book_id,)
            )
            return cur.fetchone()

    def list_book_rows(self) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT book_id, title FROM books ORDER BY book_id")
            return cur.fetchall()

    def stats(self) -> dict:
        with self._conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM chunks")
            n_chunks = cur.fetchone()["n"]
            cur.execute(
                "SELECT book_id, COUNT(*) AS chunks, COALESCE(MAX(seq),0) AS max_seq"
                " FROM chunks GROUP BY book_id ORDER BY book_id"
            )
            per_book = cur.fetchall()
            cur.execute("SELECT book_id, title, source_pdf FROM books ORDER BY book_id")
            books = cur.fetchall()
        return {"chunks": n_chunks, "per_book": per_book, "books": books}

    def bm25_search(self, query: str, k: int, book_id: str | None = None) -> list[Hit]:
        """Lexical search via pg_trgm similarity (handles Korean particles)."""
        sql = (
            "SELECT chunk_id, book_id, chapter, section, text,"
            "       similarity(text, %s) AS score"
            " FROM chunks WHERE text %% %s"
        )
        params: list[Any] = [query, query]
        if book_id:
            sql += " AND book_id = %s"
            params.append(book_id)
        sql += " ORDER BY score DESC, seq LIMIT %s"
        params.append(k)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [Hit(**r) for r in rows]

    def dense_search(self, query_vec: list[float], k: int, book_id: str | None = None) -> list[dict]:
        """Cosine-similarity search via pgvector, optionally restricted to one book."""
        sql = (
            "SELECT chunk_id, book_id, chapter, section, text,"
            "       (1 - (embedding <=> %s)) AS score"
            " FROM chunks WHERE embedding IS NOT NULL"
        )
        params: list[Any] = [query_vec, query_vec]
        if book_id:
            sql += " AND book_id = %s"
            params.append(book_id)
        sql += " ORDER BY embedding <=> %s LIMIT %s"
        params.append(query_vec)
        params.append(k)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def find_by_refs(
        self, refs: list[str], topic: str, book_id: str | None = None, limit: int = 8
    ) -> list[Hit]:
        """Exact-ish section lookup — mirrors rag.store.Store.find_by_refs."""
        words = [w for w in re.split(r"[^a-z0-9]+", topic.lower()) if len(w) >= 4]
        patterns = [p for p in list(refs) + words if p]
        hits: list[Hit] = []
        seen: set[str] = set()
        for pat in patterns[:8]:
            like = f"%{pat}%"
            sql = (
                "SELECT chunk_id, book_id, chapter, section, text FROM chunks"
                " WHERE (section ILIKE %s OR chapter ILIKE %s)"
            )
            params: list[Any] = [like, like]
            if book_id:
                sql += " AND book_id = %s"
                params.append(book_id)
            sql += " ORDER BY seq LIMIT %s"
            params.append(limit)
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                for r in cur.fetchall():
                    if r["chunk_id"] not in seen:
                        seen.add(r["chunk_id"])
                        hits.append(Hit(**r))
            if len(hits) >= limit:
                break
        return hits
