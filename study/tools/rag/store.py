"""SQLite chunk store with BM25 keyword search.

Keyword search uses SQLite FTS5 (bm25 rank) when available; otherwise it
falls back to a pure-Python BM25 over token lists cached in memory.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .chunk import Chunk
from .config import settings

_TOKEN_RE = re.compile(r"[a-z0-9\uac00-\ud7a3]+")  # latin + Korean syllables


@dataclass
class Hit:
    chunk_id: str
    book_id: str
    chapter: str
    section: str
    text: str
    score: float = 0.0


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _has_hangul(text: str) -> bool:
    return any("\uac00" <= ch <= "\ud7a3" for ch in text)


def _fts_quote(term: str) -> str:
    quoted = '"' + term.replace('"', '""') + '"'
    # Korean words carry particles (정규분포 -> 정규분포의), so prefix-match
    # Hangul terms; ASCII terms stay exact.
    return quoted + "*" if _has_hangul(term) else quoted


class Store:
    """Chunk store: books, chunks and a term-frequency index for BM25."""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._schema()
        self.use_fts = self._try_fts()
        self._cache: dict | None = None

    # ---------------- schema ----------------
    def _schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS books (
                book_id    TEXT PRIMARY KEY,
                title      TEXT,
                source_pdf TEXT,
                indexed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                book_id  TEXT,
                chapter  TEXT,
                section  TEXT,
                text     TEXT,
                seq      INTEGER,
                tokens   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_book ON chunks(book_id);
            CREATE TABLE IF NOT EXISTS df (
                term TEXT PRIMARY KEY,
                n    INTEGER
            );
            """
        )
        self.conn.commit()

    def _try_fts(self) -> bool:
        try:
            self.conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    book_id  UNINDEXED,
                    text
                );
                """
            )
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    # ---------------- write path ----------------
    def add_book(self, book_id: str, title: str, source_pdf: str) -> None:
        import datetime as dt

        self.conn.execute(
            "INSERT OR REPLACE INTO books(book_id, title, source_pdf, indexed_at) VALUES(?,?,?,?)",
            (book_id, title, source_pdf, dt.datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def delete_book(self, book_id: str) -> None:
        # Decrement document frequencies for the removed chunks.
        rows = self.conn.execute(
            "SELECT tokens FROM chunks WHERE book_id = ?", (book_id,)
        ).fetchall()
        for (tokens_json,) in rows:
            for term in set(json.loads(tokens_json or "[]")):
                self.conn.execute("UPDATE df SET n = n - 1 WHERE term = ?", (term,))
        self.conn.execute("DELETE FROM df WHERE n <= 0")
        self.conn.execute("DELETE FROM chunks WHERE book_id = ?", (book_id,))
        if self.use_fts:
            self.conn.execute("DELETE FROM chunks_fts WHERE book_id = ?", (book_id,))
        self.conn.execute("DELETE FROM books WHERE book_id = ?", (book_id,))
        self.conn.commit()
        self._cache = None

    def add_chunks(self, chunks: Iterable[Chunk]) -> None:
        for c in chunks:
            tokens = _tokenize(c.text)
            self.conn.execute(
                "INSERT INTO chunks(chunk_id, book_id, chapter, section, text, seq, tokens)"
                " VALUES(?,?,?,?,?,?,?)",
                (c.chunk_id, c.book_id, c.chapter, c.section, c.text, c.seq, json.dumps(tokens)),
            )
            if self.use_fts:
                self.conn.execute(
                    "INSERT INTO chunks_fts(chunk_id, book_id, text) VALUES(?,?,?)",
                    (c.chunk_id, c.book_id, c.text),
                )
            for term in set(tokens):
                self.conn.execute(
                    "INSERT INTO df(term, n) VALUES(?,1)"
                    " ON CONFLICT(term) DO UPDATE SET n = n + 1",
                    (term,),
                )
        self.conn.commit()
        self._cache = None

    # ---------------- read path ----------------
    def book_title(self, book_id: str) -> str:
        row = self.conn.execute(
            "SELECT title FROM books WHERE book_id = ?", (book_id,)
        ).fetchone()
        return row["title"] if row else book_id

    def bm25_search(self, query: str, k: int, book_id: str | None = None) -> list[Hit]:
        terms = list(dict.fromkeys(_tokenize(query)))
        if not terms:
            return []
        if self.use_fts:
            return self._fts_search(terms, k, book_id)
        return self._py_bm25(terms, k, book_id)

    def _fts_search(self, terms: list[str], k: int, book_id: str | None) -> list[Hit]:
        match_expr = " OR ".join(_fts_quote(t) for t in terms)
        sql = (
            "SELECT c.chunk_id, c.book_id, c.chapter, c.section, c.text,"
            "       -bm25(chunks_fts) AS score"
            " FROM chunks_fts AS fts JOIN chunks AS c ON c.chunk_id = fts.chunk_id"
            " WHERE chunks_fts MATCH ?"
        )
        params: list = [match_expr]
        if book_id:
            sql += " AND fts.book_id = ?"
            params.append(book_id)
        sql += " ORDER BY score DESC LIMIT ?"
        params.append(k)
        rows = self.conn.execute(sql, params).fetchall()
        return [Hit(**dict(r)) for r in rows]

    def _load_cache(self) -> None:
        if self._cache is not None:
            return
        rows = self.conn.execute(
            "SELECT chunk_id, book_id, chapter, section, text, tokens FROM chunks"
        ).fetchall()
        self._cache = {
            r["chunk_id"]: {
                "chunk_id": r["chunk_id"],
                "book_id": r["book_id"],
                "chapter": r["chapter"],
                "section": r["section"],
                "text": r["text"],
                "tokens": json.loads(r["tokens"] or "[]"),
                "tf": Counter(json.loads(r["tokens"] or "[]")),
            }
            for r in rows
        }

    def _py_bm25(self, terms: list[str], k: int, book_id: str | None) -> list[Hit]:
        self._load_cache()
        docs = list(self._cache.values())
        n_docs = len(docs)
        if n_docs == 0:
            return []
        avgdl = sum(len(d["tokens"]) for d in docs) / n_docs
        k1, b = 1.5, 0.75
        idf = {}
        for term in terms:
            row = self.conn.execute("SELECT n FROM df WHERE term = ?", (term,)).fetchone()
            df = row["n"] if row else 0
            idf[term] = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))

        scored: list[Hit] = []
        for doc in docs:
            if book_id and doc["book_id"] != book_id:
                continue
            length = len(doc["tokens"])
            norm = k1 * (1.0 - b + b * length / avgdl)
            score = 0.0
            for term in terms:
                tf = doc["tf"].get(term, 0)
                if tf == 0 and _has_hangul(term):
                    # same particle problem as FTS5: count prefix matches
                    tf = sum(cnt for tok, cnt in doc["tf"].items() if tok.startswith(term))
                if tf:
                    score += idf[term] * tf * (k1 + 1.0) / (tf + norm)
            if score > 0.0:
                scored.append(
                    Hit(
                        chunk_id=doc["chunk_id"],
                        book_id=doc["book_id"],
                        chapter=doc["chapter"],
                        section=doc["section"],
                        text=doc["text"],
                        score=score,
                    )
                )
        scored.sort(key=lambda h: -h.score)
        return scored[:k]

    def find_by_refs(
        self, refs: list[str], topic: str, book_id: str | None = None, limit: int = 8
    ) -> list[Hit]:
        """Exact-ish section lookup: first-priority candidates from TOPICS.md.

        Tries each section reference (e.g. "3.5") against chapter/section
        headings; falls back to topic words.
        """
        hits: list[Hit] = []
        seen: set[str] = set()
        words = [w for w in re.split(r"[^a-z0-9]+", topic.lower()) if len(w) >= 4]
        patterns = [p for p in list(refs) + words if p]

        for pat in patterns[:8]:
            like = f"%{pat}%"
            sql = (
                "SELECT chunk_id, book_id, chapter, section, text FROM chunks"
                " WHERE (section LIKE ? OR chapter LIKE ?)"
            )
            params: list = [like, like]
            if book_id:
                sql += " AND book_id = ?"
                params.append(book_id)
            sql += " ORDER BY seq LIMIT ?"
            params.append(limit)
            for r in self.conn.execute(sql, params):
                if r["chunk_id"] in seen:
                    continue
                seen.add(r["chunk_id"])
                hits.append(
                    Hit(
                        chunk_id=r["chunk_id"],
                        book_id=r["book_id"],
                        chapter=r["chapter"],
                        section=r["section"],
                        text=r["text"],
                        score=0.0,
                    )
                )
            if hits:
                break
        return hits
