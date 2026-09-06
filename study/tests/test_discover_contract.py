"""topics discover 계약 — 클라이언트 관점 테스트.

인덱스된 책의 번호 섹션(3.1, 3.5)마다 `todo` 토픽을 자동 생성한다.
Chapter(단일 번호)·무번호(Review)·중복 헤딩은 올바르게 처리되어야 한다.
"""
from study_lib.chunk import chunk_markdown
from study_lib.discover import discover_topics, topic_title
from study_lib.registry import InMemoryStore, Library, TODO
from study_lib.store import IndexStore

MD = """# Chapter 3 Limits
## 3.1 The Limit of a Function
First paragraph about limits.
## 3.1 The Limit of a Function
Second paragraph — same section, extra chunk.
## 3.5 The Limit of a Sequence
A sequence converges to L when its terms approach L.
## Review
Chapter review (no number → no topic).
"""


def _env():
    lib = Library(InMemoryStore())
    lib.add_book("calc", "Calculus", "math")
    store = IndexStore()
    chunks = chunk_markdown(MD, book_id="calc")
    store.add_many(chunks, vectors=[() for _ in chunks])
    return lib, store


def test_discover_creates_one_topic_per_numbered_section():
    lib, store = _env()
    report = discover_topics("calc", library=lib, store=store)
    assert report.summary() == "added=2 existing_skipped=0"
    topics = lib.topics(book_id="calc", status=TODO)
    assert {t.section for t in topics} == {"3.1", "3.5"}
    # 제목은 번호를 뺀 설명
    by_section = {t.section: t for t in topics}
    assert by_section["3.5"].title == "The Limit of a Sequence"


def test_discover_is_idempotent_on_rerun():
    lib, store = _env()
    first = discover_topics("calc", library=lib, store=store)
    assert first.summary() == "added=2 existing_skipped=0"
    second = discover_topics("calc", library=lib, store=store)
    assert second.summary() == "added=0 existing_skipped=2"


def test_discover_respects_kind_and_excludes_unnumbered():
    lib, store = _env()
    report = discover_topics("calc", library=lib, store=store, kind="note")
    assert report.summary() == "added=2 existing_skipped=0"
    assert all(t.kind == "note" for t in lib.topics(book_id="calc"))
    # Review(무번호) 토픽은 없어야 한다
    assert not any("review" in t.title.lower() for t in lib.topics(book_id="calc"))


def test_discover_raises_for_book_without_indexed_chunks():
    lib = Library(InMemoryStore())
    lib.add_book("empty", "Empty", "math")
    store = IndexStore()
    try:
        discover_topics("empty", library=lib, store=store)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_topic_title_strips_the_leading_number():
    assert topic_title("3.5 The Limit of a Sequence") == "The Limit of a Sequence"
    assert topic_title("1.1 Exercises") == "Exercises"
