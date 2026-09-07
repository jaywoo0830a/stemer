"""CLI 계약 — 클라이언트 관점 테스트.

`python -m study_lib.cli` 의 얇은 명령들: books/topics/status 는 registry JSON 위에서
결정적으로 동작하고, index 는 파서→청크→임베딩(stub)→store 영속, generate 는 API
설정이 없으면 명확한 오류를 돌려준다.
"""
from study_lib.cli import main


def _call(capsys, argv):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_books_lifecycle(tmp_path, capsys):
    reg = tmp_path / "registry.json"
    code, out, err = _call(capsys, ["books", "add", "--id", "calc", "--title", "Calculus",
                                    "--subject", "math", "--registry", str(reg)])
    assert code == 0 and "added book calc" in out
    code, out, err = _call(capsys, ["books", "list", "--registry", str(reg)])
    assert code == 0 and "calc" in out and "Calculus" in out and "math" in out
    code, out, err = _call(capsys, ["status", "--registry", str(reg)])
    assert "books=1" in out and "pending_topics=0" in out


def test_topics_lifecycle(tmp_path, capsys):
    reg = tmp_path / "registry.json"
    main(["books", "add", "--id", "prob", "--title", "Probability", "--subject", "math",
          "--registry", str(reg)])
    code, out, err = _call(capsys, ["topics", "add", "--book", "prob",
                                    "--title", "Normal distribution", "--section", "3.5",
                                    "--registry", str(reg)])
    assert code == 0 and "normal-distribution" in out
    code, out, err = _call(capsys, ["topics", "set", "normal-distribution", "--status",
                                    "draft", "--registry", str(reg)])
    assert code == 0
    code, out, err = _call(capsys, ["status", "--registry", str(reg)])
    assert "topics=1" in out and "pending_topics=0" in out


def test_invalid_inputs_return_nonzero_with_message(tmp_path, capsys):
    reg = tmp_path / "registry.json"
    code, out, err = _call(capsys, ["topics", "add", "--book", "nope", "--title", "X",
                                    "--registry", str(reg)])
    assert code != 0 and "unknown book" in err
    main(["books", "add", "--id", "a", "--title", "A", "--subject", "math",
          "--registry", str(reg)])
    code, out, err = _call(capsys, ["books", "add", "--id", "a", "--title", "B",
                                    "--subject", "math", "--registry", str(reg)])
    assert code != 0 and "already exists" in err


def test_index_text_file_then_status(tmp_path, capsys):
    reg = tmp_path / "registry.json"
    src = tmp_path / "book.md"
    src.write_text("# 3.5 The Limit of a Sequence\n\nA sequence converges to L.\n",
                   encoding="utf-8")
    main(["books", "add", "--id", "calc", "--title", "Calculus", "--subject", "math",
          "--registry", str(reg)])
    code, out, err = _call(capsys, ["index", str(src), "--book", "calc", "--profile",
                                    "text", "--embedder", "stub", "--registry", str(reg),
                                    "--store", str(tmp_path / "store")])
    assert code == 0 and "indexed calc: 1 chunks" in out
    assert (tmp_path / "store" / "calc.jsonl").exists()
    code, out, err = _call(capsys, ["status", "--registry", str(reg)])
    assert "books=1" in out


def test_generate_without_api_key_gives_actionable_error(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")  # 모델은 있어도 키가 없어야 함
    reg = tmp_path / "registry.json"
    main(["books", "add", "--id", "calc", "--title", "Calculus", "--subject", "math",
          "--registry", str(reg)])
    main(["topics", "add", "--book", "calc", "--title", "Limits", "--section", "3.5",
          "--registry", str(reg)])
    code, out, err = _call(capsys, ["generate", "--book", "calc",
                                    "--registry", str(reg),
                                    "--store", str(tmp_path / "store"),
                                    "--notes", str(tmp_path / "notes")])
    assert code != 0
    assert "DEEPSEEK_API_KEY" in err


def test_chapter_focus_reindexes_given_range(tmp_path, capsys):
    """사용자가 직접 준 챕터 페이지 범위로만 재인제스트 (책 parser 우선)."""
    reg = tmp_path / "registry.json"
    store_dir = tmp_path / "store"
    src = tmp_path / "chap.md"
    src.write_text("# 1.1 Functions\n\nf(x)=x^2\n\n# 1.2 Limits\n\nlimit text\n",
                   encoding="utf-8")
    # 책: parser=text(실행 가벼움), source 는 무시하되 메타만 존재
    main(["books", "add", "--id", "calc", "--title", "Calculus", "--subject", "math",
          "--parser", "text", "--registry", str(reg)])

    # --range 를 사용자가 직접 전달 → 성공 + page_range 반영
    code, out, err = _call(capsys, [
        "chapter", "focus", "--book", "calc", "--range", "42-90",
        "--source", str(src), "--embedder", "stub",
        "--registry", str(reg), "--store", str(store_dir)])
    assert code == 0, err
    assert "focusing calc pages=42-90" in out
    assert "ingested calc: 2 chunks" in out
    # store jsonl 갱신 + book.page_range 반영 확인
    assert (store_dir / "calc.jsonl").exists()
    code, out, err = _call(capsys, ["books", "list", "--registry", str(reg)])
    assert "pages=42-90" in out


def test_chapter_focus_requires_known_book(tmp_path, capsys):
    reg = tmp_path / "registry.json"
    code, out, err = _call(capsys, [
        "chapter", "focus", "--book", "nope", "--range", "42-90",
        "--registry", str(reg)])
    assert code != 0 and "unknown book" in err

