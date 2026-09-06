"""인덱스 저장소 계약 — 클라이언트 관점 테스트.

청크(+벡터)를 RAM 에 스테이징(`add`) → `flush()` 로 영속화 → `search_*` 로 검색.
재시작(새 IndexStore + 같은 sink) 후 `load_all()` 하면 복원된다.
"""
import pytest

from study_lib.store import IndexStore, IndexedChunk, JsonDurableSink


def _chunk(cid: str, book: str = "calc", section: str = "1.1", text: str = "") -> IndexedChunk:
    return IndexedChunk(chunk_id=cid, book_id=book, section=section, text=text or cid)


def test_dense_search_returns_nearest_chunk():
    store = IndexStore()
    store.add(_chunk("a", text="cosine to [1,0]"), vector=(1.0, 0.0))
    store.add(_chunk("b", text="cosine to [0,1]"), vector=(0.0, 1.0))
    hits = store.search_dense((1.0, 0.0), k=1)
    assert hits[0].chunk_id == "a"
    assert hits[0].score == pytest.approx(1.0)


def test_text_search_finds_term_and_respects_book_filter():
    store = IndexStore()
    store.add(_chunk("d1", book="calc", text="The derivative of f is f prime."))
    store.add(_chunk("d2", book="stat", text="Derivatives are unrelated here."))
    hits = store.search_text("derivative", k=5, book_id="calc")
    assert [h.chunk_id for h in hits] == ["d1"]
    hits_all = store.search_text("derivative", k=5)
    assert {h.book_id for h in hits_all} == {"calc", "stat"}


def test_flush_then_reload_restores_index(tmp_path):
    # given: sink 로 flush 한 저장소
    sink = JsonDurableSink(tmp_path / "store")
    store = IndexStore(sink=sink)
    store.add(_chunk("k1", book="calc", text="limit definition"), vector=(1.0, 0.0))
    store.flush()
    # when: 재시작 — 같은 sink 로 새 저장소를 열어 복원
    reloaded = IndexStore(sink=JsonDurableSink(tmp_path / "store"))
    assert reloaded.load_all() == 1
    # then: 검색이 다시 동작한다
    assert reloaded.search_dense((1.0, 0.0), k=1)[0].chunk_id == "k1"
    assert reloaded.search_text("limit", k=5)[0].chunk_id == "k1"


def test_flush_without_sink_gives_actionable_error():
    store = IndexStore()
    store.add(_chunk("x", book="calc", text="any"))
    with pytest.raises(ValueError, match="sink"):
        store.flush()


def test_delete_book_updates_stats_and_search():
    store = IndexStore()
    store.add(_chunk("c1", book="a"), vector=(1.0, 0.0))
    store.add(_chunk("c2", book="b"), vector=(0.0, 1.0))
    assert store.stats().books == 2
    store.delete_book("a")
    stats = store.stats()
    assert stats.books == 1
    assert stats.by_book == {"b": 1}
    assert store.search_dense((1.0, 0.0), k=5) == []


def test_chunks_accessor_filters_and_keeps_book_order():
    store = IndexStore()
    store.add(IndexedChunk("a0", "b", "1.1 Limits", "one", seq=0))
    store.add(IndexedChunk("a1", "b", "1.2 Continuity", "two", seq=1))
    store.add(IndexedChunk("o0", "other", "1.1 Limits", "three", seq=0))
    assert [c.chunk_id for c in store.chunks(book_id="b", section="1.1 Limits")] == ["a0"]
    assert [c.chunk_id for c in store.chunks(book_id="b")] == ["a0", "a1"]
    assert len(store.chunks()) == 3
