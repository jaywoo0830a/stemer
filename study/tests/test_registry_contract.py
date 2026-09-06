"""레지스트리 계약 — 클라이언트 관점 테스트.

사용자가 하는 일: 책 등록 → 그 책의 섹션을 토픽으로 등록 →
생성 상태를 `todo → draft → review → done` 으로 전이한다.
"""
import pytest

from study_lib.registry import (
    DONE,
    DRAFT,
    InMemoryStore,
    JsonFileStore,
    Library,
    TODO,
    slugify,
)


def test_register_a_book_and_add_topics_from_its_sections():
    # given: 빈 라이브러리
    lib = Library(InMemoryStore())
    # when: 수학 교재를 등록하고 그 책의 섹션들을 토픽으로 추가
    lib.add_book("prob", "Introduction to Probability", subject="math",
                 source="blitzstein.pdf")
    lib.add_topic(book_id="prob", title="Normal distribution", section="3.5")
    lib.add_topic(book_id="prob", title="Law of large numbers", section="5.1")
    # then: 책·토픽이 조회되고, 과목은 책에서 상속되며, 상태는 todo
    assert lib.book("prob").subject == "math"
    assert [t.topic_id for t in lib.topics(book_id="prob")] == [
        "normal-distribution",
        "law-of-large-numbers",
    ]
    assert all(t.status == TODO for t in lib.topics())


def test_adding_a_book_twice_or_topic_for_unknown_book_is_rejected():
    lib = Library(InMemoryStore())
    lib.add_book("a", "Book A", "math")
    with pytest.raises(ValueError, match="already exists"):
        lib.add_book("a", "Book A again", "math")
    with pytest.raises(KeyError, match="unknown book"):
        lib.add_topic(book_id="missing", title="Anything")


def test_unknown_subject_and_kind_are_rejected():
    lib = Library(InMemoryStore())
    lib.add_book("a", "Book A", "math")
    with pytest.raises(ValueError, match="unknown subject"):
        lib.add_book("b", "Bad", "astronomy")
    with pytest.raises(ValueError, match="invalid kind"):
        lib.add_topic(book_id="a", title="X", kind="essay")


def test_topic_lifecycle_todo_to_done_and_note_path():
    lib = Library(InMemoryStore())
    lib.add_book("a", "Book A", "chem")
    lib.add_topic(book_id="a", title="Redox reactions", kind="exam")
    tid = "redox-reactions"
    lib.set_status(tid, DRAFT, note_path="notes/redox-reactions.md")
    lib.set_status(tid, "review")
    lib.set_status(tid, DONE)
    topic = lib.topic(tid)
    assert topic.status == DONE
    assert topic.note_path == "notes/redox-reactions.md"
    # done 이 된 토픽은 pending 에 남아 있지 않아야 한다
    assert lib.pending_topics() == []


def test_invalid_status_is_rejected():
    lib = Library(InMemoryStore())
    lib.add_book("a", "Book A", "math")
    lib.add_topic(book_id="a", title="Series")
    with pytest.raises(ValueError, match="invalid status"):
        lib.set_status("series", "published")


def test_json_file_store_roundtrips_state(tmp_path):
    # given: 파일 저장소 위에서 책·토픽을 만들고 draft 로 진행
    path = tmp_path / "registry.json"
    lib = Library(JsonFileStore(path))
    lib.add_book("calc", "Calculus", subject="math")
    lib.add_topic(book_id="calc", title="Limit of a sequence", kind="exam")
    lib.set_status("limit-of-a-sequence", DRAFT)
    lib.save()
    # when: 같은 파일로 새 Library 를 연다 (재시작 시나리오)
    reloaded = Library(JsonFileStore(path))
    # then: 상태가 그대로 복원된다
    assert reloaded.book("calc").title == "Calculus"
    assert reloaded.topic("limit-of-a-sequence").status == DRAFT


def test_slugify_makes_safe_topic_ids():
    assert slugify("Normal distribution") == "normal-distribution"
    assert slugify("  Multi   Word  Title  ") == "multi-word-title"


def test_book_parser_field_and_setters_roundtrip(tmp_path):
    # given: 파서·청크 프로필을 명시해 책 등록
    lib = Library(JsonFileStore(tmp_path / "registry.json"))
    lib.add_book("scan", "Scanned Physics", subject="phys", parser="docling",
                 chunk_profile="compact")
    # then: 저장/로드 후에도 책별 파서·청크 프로필이 보존된다
    lib.save()
    reloaded = Library(JsonFileStore(tmp_path / "registry.json"))
    assert reloaded.book("scan").parser == "docling"
    assert reloaded.book("scan").chunk_profile == "compact"
    # when: 책별 파서/청크 프로필을 변경
    reloaded.set_book_parser("scan", "fast")
    reloaded.set_book_chunk_profile("scan", "long")
    reloaded.save()
    reloaded2 = Library(JsonFileStore(tmp_path / "registry.json"))
    assert reloaded2.book("scan").parser == "fast"
    assert reloaded2.book("scan").chunk_profile == "long"


def test_clear_and_delete_topics():
    lib = Library(InMemoryStore())
    lib.add_book("a", "Book A", "math")
    lib.add_topic(book_id="a", title="X", section="1.1")
    lib.add_topic(book_id="a", title="Y", section="1.2")
    lib.add_book("b", "Book B", "math")
    lib.add_topic(book_id="b", title="Z", section="2.1")
    # delete 1건
    lib.delete_topic("x")
    assert len(lib.topics(book_id="a")) == 1
    # clear 특정 책만
    n = lib.clear_topics("a")
    assert n == 1
    assert lib.topics(book_id="a") == []
    assert len(lib.topics(book_id="b")) == 1  # 다른 책은 유지
    # 없는 책은 KeyError
    with pytest.raises(KeyError, match="unknown book"):
        lib.clear_topics("missing")


def test_delete_book_removes_book_and_its_topics():
    lib = Library(InMemoryStore())
    lib.add_book("a", "Book A", "math")
    lib.add_topic(book_id="a", title="X", section="1.1")
    lib.add_topic(book_id="a", title="Y", section="1.2")
    lib.add_book("b", "Book B", "math")
    lib.add_topic(book_id="b", title="Z", section="2.1")
    removed = lib.delete_book("a")
    assert removed == 2                     # a의 토픽 2개
    with pytest.raises(KeyError, match="unknown book"):
        lib.book("a")
    assert lib.topics(book_id="b")           # b는 그대로
    with pytest.raises(KeyError, match="unknown book"):
        lib.delete_book("missing")
