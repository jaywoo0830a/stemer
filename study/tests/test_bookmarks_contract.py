"""북마크 기반 토픽 discover 계약 — docling 이 번호를 버리는 PDF용.

클라이언트 관점: PDF 북마크(완전한 번호 목차)에서 번호 섹션을 읽어
todo 토픽을 만든다. 재실행 멱등. 단일 번호/무번호 항목은 제외.
"""
import pytest

from study_lib.bookmarks import (
    discover_topics_from_bookmarks,
    extract_sections_from_titles,
)
from study_lib.registry import InMemoryStore, Library, TODO

_TITLES = [
    "Cover",
    "Contents",
    "Chapter 1: Functions and Models",
    "1.1 Four Ways to Represent a Function",
    "1.2 Mathematical Models",
    "1 Review",
    "Problems Plus",
    "Chapter 2: Limits and Derivatives",
    "2.1 The Tangent and Velocity Problems",
    "2.2 The Limit of a Function",
    "2 Review",
    "Appendix A: Numbers",
    "Index",
]


def test_extract_sections_keeps_only_dotted_numbers():
    sections = extract_sections_from_titles(_TITLES)
    assert sections == [
        ("1.1", "Four Ways to Represent a Function"),
        ("1.2", "Mathematical Models"),
        ("2.1", "The Tangent and Velocity Problems"),
        ("2.2", "The Limit of a Function"),
    ]


def test_discover_creates_todo_topics_idempotently():
    lib = Library(InMemoryStore())
    lib.add_book("calc", "Calculus", subject="math", parser="docling",
                 page_range="42-1249")

    sections = extract_sections_from_titles(_TITLES)
    report = discover_topics_from_bookmarks("calc", library=lib, sections=sections)
    assert report.summary() == "added=4 existing_skipped=0"
    topics = lib.topics(book_id="calc")
    assert len(topics) == 4
    assert all(t.status == TODO for t in topics)
    assert {t.section for t in topics} == {"1.1", "1.2", "2.1", "2.2"}
    # 제목에 번호가 붙지 않아야 한다
    t11 = lib.topic("calc-1-1")
    assert t11.title == "Four Ways to Represent a Function"

    # 재실행 → 모두 스킵 (멱등)
    report2 = discover_topics_from_bookmarks("calc", library=lib, sections=sections)
    assert report2.summary() == "added=0 existing_skipped=4"


def test_discover_requires_source_or_sections():
    lib = Library(InMemoryStore())
    lib.add_book("calc", "Calculus", subject="math")
    with pytest.raises(ValueError, match="source"):
        discover_topics_from_bookmarks("calc", library=lib)


def test_discover_requires_registered_book():
    lib = Library(InMemoryStore())
    with pytest.raises(KeyError, match="unknown book"):
        discover_topics_from_bookmarks("missing", library=lib, sections=[])
