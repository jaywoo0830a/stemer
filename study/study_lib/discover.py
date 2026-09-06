"""discover — 인덱스된 책의 번호 섹션에서 자동으로 todo 토픽 생성.

클라이언트 관점:
    report = discover_topics("calc", library=lib, store=store)   # kind 기본 exam
    report.summary()   # added=2 existing_skipped=0

규칙:
- 대상: 청크 `section` 헤딩에 **점이 붙은 번호**(`3.5`, `1.1.2`)가 있는 섹션.
  단일 번호(Chapter 3)나 무번호(Review)는 제외.
- 한 섹션 번호당 토픽 1개(중복 헤딩/긴 섹션 여러 청크 → dedupe).
- 제목 = 헤딩에서 번호를 뺀 설명(`3.5 The Limit of a Sequence` → `The Limit of a Sequence`).
- topic_id = `<book_id>-<section>` 슬러그 → 재실행 멱등(기존 섹션/동일 id 는 스킵).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .registry import Library, slugify
from .store import IndexStore

_NUM_RE = re.compile(r"\d+(?:\.\d+)+")
_LEADING_NUM = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s*")


@dataclass(frozen=True)
class DiscoverReport:
    book_id: str
    added: tuple[str, ...] = ()
    existing_skipped: int = 0

    def summary(self) -> str:
        return f"added={len(self.added)} existing_skipped={self.existing_skipped}"


def topic_title(heading: str) -> str:
    """'3.5 The Limit of a Sequence' → 'The Limit of a Sequence'."""
    title = _LEADING_NUM.sub("", heading).strip(" :.-")
    return title or heading.strip()


def discover_topics(book_id: str, *, library: Library, store: IndexStore,
                    kind: str = "exam") -> DiscoverReport:
    """클라이언트 진입점 — 책의 번호 섹션마다 todo 토픽을 만든다."""
    library.book(book_id)  # 미등록 책이면 KeyError
    chunks = sorted(store.chunks(book_id=book_id), key=lambda c: (c.seq, c.chunk_id))
    if not chunks:
        raise ValueError(f"no indexed chunks for book {book_id!r}")

    existing = library.topics(book_id=book_id)
    existing_ids = {t.topic_id for t in existing}
    existing_sections = {t.section for t in existing if t.section}

    added: list[str] = []
    skipped = 0
    seen: set[str] = set()
    for chunk in chunks:
        m = _NUM_RE.search(chunk.section)
        if not m:
            continue
        num = m.group(0)
        if num in seen:
            continue
        seen.add(num)
        topic_id = slugify(f"{book_id}-{num}")
        if topic_id in existing_ids or num in existing_sections:
            skipped += 1
            continue
        library.add_topic(book_id=book_id, title=topic_title(chunk.section),
                          kind=kind, section=num, topic_id=topic_id)
        added.append(topic_id)
    library.save()
    return DiscoverReport(book_id=book_id, added=tuple(added),
                          existing_skipped=skipped)
