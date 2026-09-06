"""파싱 계약 — 클라이언트 관점 테스트.

소스 파일(경로+프로필)을 주면 ParsedBook(마크다운)이 나온다. 프로필은 명시하거나
확장자로 추론하고, 무거운 파서(pypdf/docling) 의존성이 없으면
**설치 방법을 알려주는** 명확한 오류가 난다.
"""
import importlib.util

import pytest

from study_lib import parse


def test_text_profile_parses_a_txt_file(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("# Chapter 1\n\nHello.\n", encoding="utf-8")
    book = parse.parse_source(p, profile="text", book_id="calc")
    assert book.markdown == "# Chapter 1\n\nHello.\n"
    assert book.parser == "text"
    assert book.book_id == "calc"


def test_profile_is_inferred_from_extension():
    assert parse.resolve_profile("x.pdf") == "fast"
    assert parse.resolve_profile("x.txt") == "text"
    with pytest.raises(ValueError, match="cannot infer"):
        parse.resolve_profile("x.epub")


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="unknown parser profile"):
        parse.get_parser("no-such-parser")


@pytest.mark.skipif(importlib.util.find_spec("pypdf") is not None,
                    reason="pypdf installed")
def test_fast_parser_without_pypdf_gives_actionable_error(tmp_path):
    p = tmp_path / "book.pdf"
    p.write_bytes(b"%PDF-1.4 placeholder")
    with pytest.raises(RuntimeError, match="pypdf"):
        parse.parse_source(p, profile="fast")


@pytest.mark.skipif(importlib.util.find_spec("docling") is not None,
                    reason="docling installed")
def test_docling_parser_without_dependency_gives_actionable_error(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4 placeholder")
    with pytest.raises(RuntimeError, match="docling"):
        parse.parse_source(p, profile="docling")
