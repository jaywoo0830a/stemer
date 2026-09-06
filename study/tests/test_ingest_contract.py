"""인제스트(사전 준비) 계약 — 클라이언트 관점 테스트.

폴더의 책 파일을: 등록 → 파싱 → 청킹 → 임베딩(stub) → 저장 → `indexed`.
- 재개: 이미 `indexed` 는 스킵(`force` 시 재인덱스)
- 실패: `failed` + 사유 기록, 다음 실행에서 재시도
- 병렬: `jobs>1` 결과가 직렬과 같아야 한다
"""
from pathlib import Path

from study_lib.ingest import ingest_dir
from study_lib.registry import FAILED, INDEXED, InMemoryStore, Library
from study_lib.store import IndexStore, JsonDurableSink

SPEC = ("stub",)


def _write(dirpath: Path, name: str, body: str) -> Path:
    p = dirpath / name
    p.write_text(body, encoding="utf-8")
    return p


def _good_body(section: str, text: str) -> str:
    return f"# 1.1 Intro\n\n## {section}\n\n{text}\n"


def _env(tmp_path: Path):
    lib = Library(InMemoryStore())
    store = IndexStore(JsonDurableSink(tmp_path / "store"))
    return lib, store


def test_ingest_dir_indexes_new_books(tmp_path):
    lib, store = _env(tmp_path)
    src = tmp_path / "books"
    src.mkdir()
    _write(src, "calc-01.md", _good_body("3.5 Limits", "The limit of a sequence is L."))
    _write(src, "calc-02.md", _good_body("4.1 Derivative", "The derivative is a rate."))

    report = ingest_dir(src, library=lib, store=store, subject="math",
                        embedder_spec=SPEC, jobs=1)
    assert report.summary() == "ingested=2 skipped=0 failed=0"
    assert lib.book("calc-01").status == INDEXED
    assert lib.book("calc-02").status == INDEXED
    assert (tmp_path / "store" / "calc-01.jsonl").exists()
    assert store.stats().chunks >= 2


def test_ingest_dir_skips_indexed_and_force_reindexes(tmp_path):
    lib, store = _env(tmp_path)
    src = tmp_path / "books"
    src.mkdir()
    _write(src, "a.md", _good_body("1.1 A", "Alpha content."))
    first = ingest_dir(src, library=lib, store=store, embedder_spec=SPEC)
    assert first.summary() == "ingested=1 skipped=0 failed=0"

    second = ingest_dir(src, library=lib, store=store, embedder_spec=SPEC)
    assert second.summary() == "ingested=0 skipped=1 failed=0"

    third = ingest_dir(src, library=lib, store=store, embedder_spec=SPEC, force=True)
    assert third.summary() == "ingested=1 skipped=0 failed=0"


def test_failed_book_is_marked_and_retried_after_fix(tmp_path):
    lib, store = _env(tmp_path)
    src = tmp_path / "books"
    src.mkdir()
    bad = _write(src, "scan.md", "# Empty\n\n\n")          # 청크 없음 → 실패
    good = _write(src, "ok.md", _good_body("2.1 Good", "Fine content."))

    first = ingest_dir(src, library=lib, store=store, embedder_spec=SPEC)
    assert first.summary() == "ingested=1 skipped=0 failed=1"
    assert lib.book("scan").status == FAILED
    assert "no chunks" in lib.book("scan").error

    # 스캔본이 실제로 텍스트가 생긴 파일로 교체 → 재시도 성공 (ok.md 는 이미 indexed → skip)
    bad.write_text(_good_body("2.1 Scan", "Now it has a text layer."), encoding="utf-8")
    second = ingest_dir(src, library=lib, store=store, embedder_spec=SPEC)
    assert second.summary() == "ingested=1 skipped=1 failed=0"
    assert lib.book("scan").status == INDEXED


def test_parallel_jobs_matches_sequential(tmp_path):
    lib, store = _env(tmp_path)
    src = tmp_path / "books"
    src.mkdir()
    _write(src, "p1.md", _good_body("1.1 One", "First paragraph here."))
    _write(src, "p2.md", _good_body("1.2 Two", "Second paragraph here."))
    _write(src, "p3.md", _good_body("1.3 Three", "Third paragraph here."))

    report = ingest_dir(src, library=lib, store=store, embedder_spec=SPEC, jobs=2)
    assert report.summary() == "ingested=3 skipped=0 failed=0"
    assert {b.book_id for b in lib.books()} == {"p1", "p2", "p3"}
    assert all(b.status == INDEXED for b in lib.books())
    assert store.stats().books == 3


def test_ingest_dir_rejects_missing_directory(tmp_path):
    lib, store = _env(tmp_path)
    try:
        ingest_dir(tmp_path / "nope", library=lib, store=store, embedder_spec=SPEC)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_ingest_respects_per_book_parser_precedence(tmp_path, monkeypatch):
    """책.parser(사용자 지정) > CLI 기본(폴더) — 책마다 다른 파서 적용."""
    import study_lib.ingest as ingest_mod
    from study_lib.registry import InMemoryStore

    lib = Library(InMemoryStore())
    lib.add_book("scanned", "Scanned", subject="math", parser="docling")
    lib.add_book("plain", "Plain", subject="math", parser=None)  # 기본값 사용
    lib.save()
    store = IndexStore(JsonDurableSink(tmp_path / "store"))

    src = tmp_path / "books"
    src.mkdir()
    _write(src, "scanned.md", _good_body("1.1 A", "Scanned content."))
    _write(src, "plain.md", _good_body("1.2 B", "Plain content."))

    seen: dict[str, str | None] = {}

    def fake_parse_source(path, *, profile=None, book_id=""):
        from study_lib.parse import ParsedBook
        seen[book_id] = profile
        return ParsedBook(book_id=book_id or Path(path).stem,
                          title=Path(path).stem,
                          markdown=Path(path).read_text(encoding="utf-8"),
                          parser=profile or "text", pages=None)

    monkeypatch.setattr(ingest_mod, "parse_source", fake_parse_source)
    report = ingest_dir(src, library=lib, store=store, embedder_spec=SPEC,
                        profile="fast", jobs=1)
    assert report.summary() == "ingested=2 skipped=0 failed=0"
    # 책별 파서가 CLI 기본(fast)보다 우선
    assert seen["scanned"] == "docling"
    assert seen["plain"] == "fast"
