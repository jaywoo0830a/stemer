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
        qterms = _terms(query)
        scored = []
        for c in self._mem.values():
            if book_id is not None and c.book_id != book_id:
                continue
            overlap = len(qterms & _terms(c.text))
            score = float(overlap)
            if overlap == 0 and query.lower() in c.text.lower():
                score = 0.5  # 정확 부분 문자열 보너스
            if score:
                scored.append((score, c))
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
