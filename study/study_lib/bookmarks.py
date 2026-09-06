"""북마크 기반 토픽 discover — docling 이 번호를 버리는 PDF용.

docling 은 헤딩 텍스트에서 섹션 번호("1.1")를 분리해 버려서, 청크 기반
`discover_topics` 가 번호 섹션을 못 찾는 책(Cengage/Stewart 등)이 있다.
이런 책들은 PDF 자체의 북마크(outline)에 완전한 번호 목차가 있으므로,
북마크에서 토픽을 만든다.

클라이언트 관점:
    report = discover_topics_from_bookmarks("미적분", library=lib,
                                            source="/books/math/미적분.pdf")
    report.summary()   # added=120 existing_skipped=0

규칙:
- 북마크 제목에서 점 번호(`1.1`, `12.3`)가 있는 항목만 토픽으로.
- 단일 번호(Chapter 1), 무번호(Review, Problems Plus)는 제외.
- topic_id = `<book_id>-<section>` 슬러그 (기존 discover 와 동일 규칙).
- kind 기본 exam.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

from .discover import topic_title
from .registry import Library, slugify

_NUM_RE = re.compile(r"\d+(?:\.\d+)+")


@dataclass(frozen=True)
class BookmarkDiscoverReport:
    book_id: str
    added: tuple[str, ...] = ()
    existing_skipped: int = 0

    def summary(self) -> str:
        return f"added={len(self.added)} existing_skipped={self.existing_skipped}"


def _iter_outline(items):
    """pypdf outline 트리를 평탄화 — title 을 순서대로 산출."""
    for it in items:
        if isinstance(it, list):
            yield from _iter_outline(it)
        else:
            yield (it.title or "").strip()


def extract_sections_from_titles(titles: list[str]) -> list[tuple[str, str]]:
    """북마크 제목 목록에서 (number, title) 추출 — 점 번호 섹션만.

    예: '1.1 Four Ways to Represent a Function' → ('1.1', 'Four Ways...')
    단일 번호(Chapter 1, 1 Review), 무번호(Preface)는 제외.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for title in titles:
        m = _NUM_RE.search(title)
        if not m:
            continue
        num = m.group(0)
        if num in seen:
            continue
        seen.add(num)
        out.append((num, topic_title(title)))
    return out


def extract_sections_from_pdf(path: str | Path) -> list[tuple[str, str]]:
    """PDF 북마크에서 (number, title) 목록 추출."""
    if not importlib.util.find_spec("pypdf"):
        raise RuntimeError("PDF 북마크 추출은 'pypdf' 가 필요합니다 (pip install pypdf)")
    import pypdf  # type: ignore

    reader = pypdf.PdfReader(str(path))
    return extract_sections_from_titles(list(_iter_outline(reader.outline)))


def discover_topics_from_bookmarks(book_id: str, *, library: Library,
                                   source: str | Path | None = None,
                                   sections: list[tuple[str, str]] | None = None,
                                   kind: str = "exam") -> BookmarkDiscoverReport:
    """북마크 번호 섹션마다 todo 토픽 생성 (재실행 멱등).

    sections 를 직접 주면 PDF 를 읽지 않는다(테스트/사전 추출용).
    """
    library.book(book_id)  # 미등록 책이면 KeyError
    if sections is None:
        if source is None:
            raise ValueError("source(경로) 또는 sections(목록) 중 하나는 필요합니다")
        sections = extract_sections_from_pdf(source)

    existing = library.topics(book_id=book_id)
    existing_ids = {t.topic_id for t in existing}
    existing_sections = {t.section for t in existing if t.section}

    added: list[str] = []
    skipped = 0
    seen: set[str] = set()
    for num, title in sections:
        if num in seen:
            continue
        seen.add(num)
        topic_id = slugify(f"{book_id}-{num}")
        if topic_id in existing_ids or num in existing_sections:
            skipped += 1
            continue
        library.add_topic(book_id=book_id, title=title, kind=kind,
                          section=num, topic_id=topic_id)
        added.append(topic_id)
    library.save()
    return BookmarkDiscoverReport(book_id=book_id, added=tuple(added),
                                  existing_skipped=skipped)
