"""교재 라이브러리 & 주제 레지스트리 — 공개 API (클라이언트 관점).

- `Book`: 책 등록(과목·파서 프로필), 인덱싱 상태 추적.
- `Topic`: 섹션 단위 생성 작업, 상태 수명주기 `todo → draft → review → done`.
- 저장소는 `RegistryStore` 인터페이스로 주입한다 (`InMemoryStore` / `JsonFileStore`).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .subjects import require_subject

# --- 토픽 상태 ---
TODO = "todo"
DRAFT = "draft"
REVIEW = "review"
DONE = "done"
VALID_STATUS = (TODO, DRAFT, REVIEW, DONE)

# --- 책(인덱싱) 상태 ---
PENDING = "pending"
INDEXING = "indexing"
INDEXED = "indexed"
FAILED = "failed"
VALID_BOOK_STATUS = (PENDING, INDEXING, INDEXED, FAILED)

VALID_KINDS = ("exam", "note", "problems")


def slugify(text: str) -> str:
    """제목/이름을 안전한 id로. 'Normal distribution' → 'normal-distribution'."""
    text = re.sub(r"[^a-z0-9가-힣]+", "-", text.strip().lower())
    return text.strip("-") or "untitled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Book:
    book_id: str
    title: str
    subject: str
    source: str = ""
    parser: str | None = None      # 파서 프로필: "fast"/"docling"/"text" (None=추론)
    chunk_profile: str | None = None
    # 페이지 범위 (1-based inclusive, "42-1249") — None 이면 전체.
    # 초입부/부록 제외하고 핵심 본문만 파싱할 때 사용 (docling page_range).
    page_range: str | None = None
    status: str = PENDING
    error: str = ""
    added_at: str = field(default_factory=_now)


@dataclass
class Topic:
    topic_id: str
    book_id: str
    subject: str
    title: str = ""
    kind: str = "exam"
    section: str | None = None
    status: str = TODO
    note_path: str | None = None
    retries: int = 0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class LibraryState:
    books: dict[str, Book] = field(default_factory=dict)
    topics: dict[str, Topic] = field(default_factory=dict)


class RegistryStore(Protocol):
    def load(self) -> LibraryState: ...
    def save(self, state: LibraryState) -> None: ...


class InMemoryStore:
    """아무것도 영속화하지 않는 저장소 (테스트/기본)."""

    def __init__(self, initial: LibraryState | None = None) -> None:
        self._state = initial or LibraryState()

    def load(self) -> LibraryState:
        return self._state

    def save(self, state: LibraryState) -> None:
        self._state = state


class JsonFileStore:
    """registry.json 한 파일에 영속화 (클라이언트는 파일 경로만 주면 된다)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> LibraryState:
        if not self._path.exists():
            return LibraryState()
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return LibraryState(
            books={bid: Book(**b) for bid, b in data.get("books", {}).items()},
            topics={tid: Topic(**t) for tid, t in data.get("topics", {}).items()},
        )

    def save(self, state: LibraryState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "books": {bid: asdict(b) for bid, b in state.books.items()},
            "topics": {tid: asdict(t) for tid, t in state.topics.items()},
        }
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class Library:
    """클라이언트 진입점: 책 등록 → 토픽 등록 → 상태 전이."""

    def __init__(self, store: RegistryStore) -> None:
        self._store = store
        self._state = store.load()

    def save(self) -> None:
        self._store.save(self._state)

    # ---- 책 ----
    def add_book(self, book_id: str, title: str, subject: str, *, source: str = "",
                 parser: str | None = None,
                 chunk_profile: str | None = None,
                 page_range: str | None = None) -> Book:
        if book_id in self._state.books:
            raise ValueError(f"book {book_id!r} already exists")
        require_subject(subject)
        book = Book(book_id=book_id, title=title, subject=subject, source=source,
                    parser=parser, chunk_profile=chunk_profile, page_range=page_range)
        self._state.books[book_id] = book
        return book

    def set_book_parser(self, book_id: str, parser: str | None) -> Book:
        """책별 파서 프로필 지정/변경 (None 이면 확장자 추론)."""
        book = self.book(book_id)
        book.parser = parser
        return book

    def set_book_page_range(self, book_id: str, page_range: str | None) -> Book:
        """책별 페이지 범위 지정/변경. '42-1249' (1-based inclusive) 또는 None=전체."""
        book = self.book(book_id)
        book.page_range = page_range
        return book

    def set_book_chunk_profile(self, book_id: str, profile: str | None) -> Book:
        """책별 청크 프로필 지정/변경 (None 이면 기본 프로필)."""
        book = self.book(book_id)
        book.chunk_profile = profile
        return book

    def book(self, book_id: str) -> Book:
        try:
            return self._state.books[book_id]
        except KeyError:
            raise KeyError(f"unknown book {book_id!r}") from None

    def books(self) -> list[Book]:
        return list(self._state.books.values())

    def set_book_status(self, book_id: str, status: str) -> Book:
        if status not in VALID_BOOK_STATUS:
            raise ValueError(f"invalid book status {status!r}; expected {VALID_BOOK_STATUS}")
        book = self.book(book_id)
        book.status = status
        return book

    def set_book_error(self, book_id: str, message: str) -> Book:
        """인제스트 실패 마킹 — 상태 failed + 사유 기록 (재시도 가능)."""
        book = self.book(book_id)
        book.status = FAILED
        book.error = message
        return book

    # ---- 토픽 ----
    def add_topic(self, *, book_id: str, title: str = "", kind: str = "exam",
                  section: str | None = None, topic_id: str | None = None) -> Topic:
        book = self.book(book_id)  # 미등록 책이면 KeyError
        if kind not in VALID_KINDS:
            raise ValueError(f"invalid kind {kind!r}; expected {VALID_KINDS}")
        tid = topic_id or slugify(title)
        if not tid:
            raise ValueError("topic_id or a non-empty title is required")
        if tid in self._state.topics:
            raise ValueError(f"topic {tid!r} already exists")
        topic = Topic(topic_id=tid, book_id=book_id, subject=book.subject,
                      title=title or tid, kind=kind, section=section)
        self._state.topics[tid] = topic
        return topic

    def topic(self, topic_id: str) -> Topic:
        try:
            return self._state.topics[topic_id]
        except KeyError:
            raise KeyError(f"unknown topic {topic_id!r}") from None

    def topics(self, *, book_id: str | None = None, status: str | None = None,
               kind: str | None = None) -> list[Topic]:
        out = [
            t for t in self._state.topics.values()
            if (book_id is None or t.book_id == book_id)
            and (status is None or t.status == status)
            and (kind is None or t.kind == kind)
        ]
        out.sort(key=lambda t: t.created_at)
        return out

    def pending_topics(self, *, kind: str | None = None) -> list[Topic]:
        return self.topics(status=TODO, kind=kind)

    def set_status(self, topic_id: str, status: str, *, note_path: str | None = None) -> Topic:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status {status!r}; expected {VALID_STATUS}")
        topic = self.topic(topic_id)
        topic.status = status
        if note_path is not None:
            topic.note_path = note_path
        topic.updated_at = _now()
        return topic
