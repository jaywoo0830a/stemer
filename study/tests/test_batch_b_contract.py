"""사전 준비 강화(Batch B) 계약 테스트 — pgvector 어댑터·figures 인덱싱 훅."""
import importlib.util

import pytest

from study_lib.embed import StubEmbedder
from study_lib.figures import Figure, FigureRegistry, register_parsed_figures
from study_lib.parse import ParsedBook
from study_lib.registry import InMemoryStore as RegMemStore
from study_lib.registry import Library
from study_lib.store import (
    IndexStore,
    JsonDurableSink,
    PgDurableSink,
    pg_create_sql,
    pg_delete_book_sql,
    pg_read_sql,
    pg_upsert_sql,
)


# ---- ③ pgvector 영속 어댑터 ----
def test_pg_sql_builders_have_expected_shape():
    create = pg_create_sql("chunks")
    assert "CREATE TABLE IF NOT EXISTS chunks" in create
    assert "vector(1024)" in create and "USING hnsw" in create
    assert "ON CONFLICT" in pg_upsert_sql("chunks")
    assert "%s::vector" in pg_upsert_sql("chunks")
    assert "ORDER BY seq" in pg_read_sql("chunks")
    assert "WHERE book_id = %s" in pg_delete_book_sql("chunks")


@pytest.mark.skipif(importlib.util.find_spec("psycopg") is not None,
                    reason="psycopg installed")
def test_pg_sink_without_psycopg_gives_actionable_error():
    with pytest.raises(RuntimeError, match="psycopg"):
        PgDurableSink("postgresql://u:p@localhost/db")


# ---- ④ figures 인덱싱 훅 ----
def test_parsed_book_default_figures_is_empty():
    assert ParsedBook("b", "B", "# x", "text").figures == ()


def test_register_parsed_figures_assigns_book_and_skips_duplicates():
    registry = FigureRegistry()
    figures = (Figure("f1", "", "3.5", "img/a.png", "Cap A", page=3),
               Figure("f2", "", "3.5", "img/b.png", "Cap B"))
    assert register_parsed_figures(registry, figures, "calc") == 2
    assert all(f.book_id == "calc" for f in registry.figures(book_id="calc"))
    # 같은 그림 재등록 → 중복 skip
    assert register_parsed_figures(registry, figures, "calc") == 0


def test_ingest_registers_parsed_figures(tmp_path, monkeypatch):
    import study_lib.ingest as ingest_mod
    from study_lib.ingest import ingest_one

    lib = Library(RegMemStore())
    lib.add_book("calc", "Calculus", "math")
    src = tmp_path / "b.md"
    src.write_text("# 3.5 Limits\n\ntext\n", encoding="utf-8")
    parsed = ParsedBook(
        book_id="calc", title="Calculus", markdown="# 3.5 Limits\n\ntext\n",
        parser="text",
        figures=(Figure("f1", "", "3.5", "img/f1.png", "Sketch"),),
    )
    monkeypatch.setattr(ingest_mod, "parse_source",
                        lambda path, profile=None, book_id="", page_range=None: parsed)
    store = IndexStore(JsonDurableSink(tmp_path / "store"))
    registry = FigureRegistry()
    report = ingest_one(src, library=lib, store=store, embedder=StubEmbedder(),
                        book_id="calc", figures_registry=registry)
    assert report.ok
    figs = registry.figures(book_id="calc")
    assert len(figs) == 1
    assert figs[0].path == "img/f1.png"
    assert figs[0].section == "3.5"
