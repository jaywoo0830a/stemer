"""인덱스 저장소 — RAM 스테이징 → 영속화 → 검색.

클라이언트 관점:
  store = IndexStore(sink=JsonDurableSink("index/"))
  store.add(chunk, vector=emb)     # ① RAM 에 스테이징
  store.flush()                    # ② 영속화 (책 단위 파일)
  store.search_dense(qvec, k=5)    # ③ 검색 (코사인)
  store.search_text("limit", k=5)  #    어휘 검색
  reloaded = IndexStore(sink=...)  # ④ 재시작 후
  reloaded.load_all()              #    sink 에서 복원
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    book_id: str
    section: str
    text: str
    seq: int = 0
    vector: tuple[float, ...] = ()


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    book_id: str
    section: str
    text: str
    score: float


@dataclass(frozen=True)
class IndexStats:
    books: int
    chunks: int
    by_book: dict[str, int]


class DurableSink(Protocol):
    def write(self, book_id: str, chunks: list[IndexedChunk]) -> None: ...
    def read(self, book_id: str) -> list[IndexedChunk]: ...
    def book_ids(self) -> list[str]: ...
    def delete(self, book_id: str) -> None: ...


class JsonDurableSink:
    """store/<book_id>.jsonl — 책 단위 덮어쓰기 영속화."""

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)

    def _path(self, book_id: str) -> Path:
        return self._dir / f"{book_id}.jsonl"

    @staticmethod
    def _to_line(c: IndexedChunk) -> str:
        rec = {
            "chunk_id": c.chunk_id,
            "book_id": c.book_id,
            "section": c.section,
            "text": c.text,
            "seq": c.seq,
            "vector": list(c.vector),
        }
        return json.dumps(rec, ensure_ascii=False)

    @staticmethod
    def _from_line(line: str) -> IndexedChunk:
        d = json.loads(line)
        return IndexedChunk(chunk_id=d["chunk_id"], book_id=d["book_id"],
                            section=d.get("section", ""), text=d["text"],
                            seq=d.get("seq", 0), vector=tuple(d.get("vector", [])))

    def write(self, book_id: str, chunks: list[IndexedChunk]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._path(book_id).open("w", encoding="utf-8") as fh:
            for c in chunks:
                fh.write(self._to_line(c) + "\n")

    def read(self, book_id: str) -> list[IndexedChunk]:
        path = self._path(book_id)
        if not path.exists():
            return []
        return [self._from_line(ln) for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip()]

    def book_ids(self) -> list[str]:
        if not self._dir.exists():
            return []
        return [p.stem for p in sorted(self._dir.glob("*.jsonl"))]

    def delete(self, book_id: str) -> None:
        self._path(book_id).unlink(missing_ok=True)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z가-힣]+")


def _terms(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t}


def _token_list(text: str) -> list[str]:
    """중복 보존 토큰 목록 (tf 계산용)."""
    return [t for t in _TOKEN_SPLIT.split(text.lower()) if t]


def _match_count(tokens: list[str], q: str) -> int:
    """정확일치 또는 접두사 일치(경량 스테밍, q 길이 ≥4): derivative ≈ derivatives."""
    if len(q) >= 4:
        return sum(1 for t in tokens if t == q or t.startswith(q))
    return tokens.count(q)


def _bm25_scores(tokenized_docs: list[list[str]], query_terms: set[str],
                 k1: float = 1.5, b: float = 0.75) -> list[float]:
    """안정적인 BM25 (양수 평활 idf + 접두사 스테밍) — 책 단위 소규모에도 음수 없음."""
    n = len(tokenized_docs)
    if n == 0:
        return []
    lengths = [len(d) for d in tokenized_docs]
    avg_len = sum(lengths) / n
    # 문서별 접두사 포함 여부로 df 계산
    df: dict[str, int] = {}
    for q in query_terms:
        df[q] = sum(1 for doc in tokenized_docs if _match_count(doc, q) > 0)
    scores = []
    for doc, length in zip(tokenized_docs, lengths):
        total = 0.0
        for q in query_terms:
            freq = _match_count(doc, q)
            doc_df = df[q]
            if freq == 0 or doc_df <= 0:
                continue
            idf = math.log(1.0 + (n - doc_df + 0.5) / (doc_df + 0.5))
            denom = freq + k1 * (1 - b + b * length / avg_len) if avg_len else freq
            total += idf * (freq * (k1 + 1)) / denom
        scores.append(total)
    return scores


class IndexStore:
    """메모리(RAM) 버퍼 + 선택적 영속 sink. 검색은 메모리에서 수행한다."""

    def __init__(self, sink: DurableSink | None = None) -> None:
        self._sink = sink
        self._mem: dict[str, IndexedChunk] = {}

    # ---- 쓰기 (RAM 스테이징) ----
    def add(self, chunk: object, *, vector: tuple[float, ...] = ()) -> IndexedChunk:
        """chunk는 chunk_id/book_id/section/text 속성을 가진 객체(예: chunk.Chunk)."""
        rec = IndexedChunk(chunk_id=chunk.chunk_id, book_id=chunk.book_id,
                           section=getattr(chunk, "section", ""),
                           text=chunk.text, seq=getattr(chunk, "seq", 0),
                           vector=tuple(vector))
        self._mem[rec.chunk_id] = rec
        return rec

    def add_many(self, chunks: list[object],
                 vectors: list[tuple[float, ...]] | None = None) -> int:
        for i, c in enumerate(chunks):
            vec = vectors[i] if vectors is not None else ()
            self.add(c, vector=vec)
        return len(chunks)

    # ---- 영속화 ----
    def flush(self, book_id: str | None = None) -> None:
        if self._sink is None:
            raise ValueError("no durable sink configured (IndexStore(sink=...))")
        targets = [book_id] if book_id else sorted({c.book_id for c in self._mem.values()})
        for bid in targets:
            chunks = [c for c in self._mem.values() if c.book_id == bid]
            self._sink.write(bid, chunks)

    def load_all(self) -> int:
        if self._sink is None:
            raise ValueError("no durable sink configured (IndexStore(sink=...))")
        loaded = 0
        for bid in self._sink.book_ids():
            for c in self._sink.read(bid):
                self._mem[c.chunk_id] = c
                loaded += 1
        return loaded

    def delete_book(self, book_id: str) -> None:
        for cid in [cid for cid, c in self._mem.items() if c.book_id == book_id]:
            del self._mem[cid]
        if self._sink is not None:
            self._sink.delete(book_id)

    # ---- 검색 ----
    def search_dense(self, vector: tuple[float, ...], k: int = 5, *,
                     book_id: str | None = None) -> list[SearchHit]:
        scored = []
        for c in self._mem.values():
            if book_id is not None and c.book_id != book_id:
                continue
            score = _cosine(vector, c.vector)
            if score > 0:  # 0(무관) 히트는 제외
                scored.append((score, c))
        scored.sort(key=lambda t: (-t[0], t[1].chunk_id))
        return [SearchHit(chunk_id=c.chunk_id, book_id=c.book_id, section=c.section,
                          text=c.text, score=score) for score, c in scored[:k]]

    def search_text(self, query: str, k: int = 5, *,
                    book_id: str | None = None) -> list[SearchHit]:
        docs = [c for c in self._mem.values()
                if book_id is None or c.book_id == book_id]
        if not docs:
            return []
        qterms = _terms(query)
        tokenized = [_token_list(c.text) for c in docs]
        bm25 = _bm25_scores(tokenized, qterms)

        scored: list[tuple[float, object]] = []
        for c, score in zip(docs, bm25):
            if score > 0:
                scored.append((float(score), c))
        if not scored and qterms:
            # 토큰화가 안 되는 쿼리(특수문자 등) → 정확 부분 문자열 폴백
            for c in docs:
                if query.lower() in c.text.lower():
                    scored.append((0.5, c))

        scored.sort(key=lambda t: (-t[0], t[1].chunk_id))
        return [SearchHit(chunk_id=c.chunk_id, book_id=c.book_id, section=c.section,
                          text=c.text, score=score) for score, c in scored[:k]]

    # ---- 접근 ----
    def chunks(self, *, book_id: str | None = None,
               section: str | None = None) -> list[IndexedChunk]:
        """책/섹션으로 청크를 조회한다 (책 내 seq 순)."""
        out = [
            c for c in self._mem.values()
            if (book_id is None or c.book_id == book_id)
            and (section is None or c.section == section)
        ]
        out.sort(key=lambda c: (c.book_id, c.seq, c.chunk_id))
        return out

    # ---- 통계 ----
    def stats(self, *, book_id: str | None = None) -> IndexStats:
        by_book: dict[str, int] = {}
        for c in self._mem.values():
            if book_id is not None and c.book_id != book_id:
                continue
            by_book[c.book_id] = by_book.get(c.book_id, 0) + 1
        return IndexStats(books=len(by_book), chunks=sum(by_book.values()),
                          by_book=by_book)


# ---- pgvector 영속 어댑터 (선택: psycopg + pgvector) ----


def _vec_from_text(text: str) -> tuple[float, ...]:
    inner = text.strip().strip("[]")
    if not inner:
        return ()
    return tuple(float(x) for x in inner.split(","))


def pg_create_sql(table: str, dim: int = 1024) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ("
        f"chunk_id TEXT PRIMARY KEY, book_id TEXT NOT NULL, "
        f"section TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, "
        f"seq INT NOT NULL DEFAULT 0, vector vector({dim}) NOT NULL);"
        f"CREATE INDEX IF NOT EXISTS {table}_book_idx ON {table}(book_id);"
        f"CREATE INDEX IF NOT EXISTS {table}_vec_idx ON {table} "
        f"USING hnsw (vector vector_cosine_ops);"
    )


def pg_upsert_sql(table: str) -> str:
    return (
        f"INSERT INTO {table} (chunk_id, book_id, section, text, seq, vector) "
        f"VALUES (%s, %s, %s, %s, %s, %s::vector) "
        f"ON CONFLICT (chunk_id) DO UPDATE SET book_id=EXCLUDED.book_id, "
        f"section=EXCLUDED.section, text=EXCLUDED.text, seq=EXCLUDED.seq, "
        f"vector=EXCLUDED.vector"
    )


def pg_read_sql(table: str) -> str:
    return (f"SELECT chunk_id, book_id, section, text, seq, vector::text "
            f"FROM {table} WHERE book_id = %s ORDER BY seq")


def pg_book_ids_sql(table: str) -> str:
    return f"SELECT DISTINCT book_id FROM {table} ORDER BY book_id"


def pg_delete_book_sql(table: str) -> str:
    return f"DELETE FROM {table} WHERE book_id = %s"


class PgDurableSink:
    """pgvector 영속 어댑터 — DurableSink 인터페이스 (선택 의존성)."""

    def __init__(self, dsn: str, *, table: str = "chunks", dim: int = 1024) -> None:
        try:
            import psycopg  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "PgDurableSink needs psycopg: pip install 'psycopg[binary]'"
            ) from None
        self._dsn = dsn
        self._table = table
        self._dim = dim

    def _connect(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def write(self, book_id: str, chunks: list[IndexedChunk]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(pg_create_sql(self._table, self._dim))
                cur.execute(pg_delete_book_sql(self._table), (book_id,))
                rows = [(c.chunk_id, c.book_id, c.section, c.text, c.seq,
                         list(c.vector)) for c in chunks]
                cur.executemany(pg_upsert_sql(self._table), rows)

    def read(self, book_id: str) -> list[IndexedChunk]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(pg_read_sql(self._table), (book_id,))
                return [IndexedChunk(chunk_id=r[0], book_id=r[1], section=r[2],
                                     text=r[3], seq=r[4],
                                     vector=_vec_from_text(r[5]))
                        for r in cur.fetchall()]

    def book_ids(self) -> list[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(pg_book_ids_sql(self._table))
                return [r[0] for r in cur.fetchall()]

    def delete(self, book_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(pg_delete_book_sql(self._table), (book_id,))
