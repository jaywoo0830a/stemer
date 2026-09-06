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


# ---- 헤딩 재구성 (fast 파서가 만드는 구조) ----

def test_reconstruct_promotes_dotted_number_heading():
    text = ("1.1 Basic Concepts. Modeling\n"
            "Some body text here.\n")
    out = parse._reconstruct_heads(text)
    assert out.splitlines()[0] == "## 1.1 Basic Concepts. Modeling"
    assert "Some body text" in out


def test_reconstruct_ignores_toc_page():
    # 목차: 짧은 점-번호 줄이 다수 → 승격하면 안 됨 (가짜 섹션 방지)
    lines = [f"{c}.{s} Title of section number {n}  {n}" for c in range(1, 4)
             for s, n in [(1, 10), (2, 20), (3, 30)]]
    out = parse._reconstruct_heads("\n".join(lines))
    assert "## " not in out


def test_reconstruct_keeps_body_crossrefs_unpromoted():
    # 본문 상호참조 "(Sec. 4.5)" 는 대문자 시작도 아니고 줄 시작 번호도 아님
    text = "see (Sec. 4.5) for details.\n"
    out = parse._reconstruct_heads(text)
    assert "## " not in out
    assert "Sec. 4.5" in out


def test_reconstruct_ignores_long_or_lowercase_lines():
    text = ("1.1 this is lowercase so not a heading\n"
            + "1.2 " + "x" * 200 + "\n")
    out = parse._reconstruct_heads(text)
    assert "## " not in out


def test_reconstruct_ignores_toc_entry_with_trailing_page_number():
    # 목차 조각: 헤딩처럼 보이지만 줄 끝에 페이지 번호가 붙음 → 승격 금지
    text = "25.4 Testing Hypotheses. Decisions 1077\nbody\n"
    out = parse._reconstruct_heads(text)
    assert "## 25.4" not in out


def test_reconstruct_ignores_prose_sentence_fragment():
    # 본문 문장: 번호 뒤 소문자 단어 연속 → 오탐 금지
    text = "1.06 US quart) is vibrating up and down under the\nbody\n"
    out = parse._reconstruct_heads(text)
    assert "## 1.06" not in out


def test_reconstruct_keeps_title_case_headings():
    # 전치사 소문자가 섞여도 Title Case 비율이 높으면 승격
    text = "2.4 Modeling of Free Oscillations of a Mass–Spring System\nbody\n"
    out = parse._reconstruct_heads(text)
    assert out.splitlines()[0].startswith("## 2.4 Modeling")


def test_clean_glyph_noise_removes_glyph_codes_and_hash_garbage():
    text = ("1 inch (in.) /H110052.540000 cm\n"
            "# Á #\n"
            "## Á\n"
            "Real body text here.\n")
    out = parse._clean_glyph_noise(text)
    assert "/H11005" not in out
    assert "# Á #" not in out
    assert "## Á" not in out
    assert "Real body text here." in out


def test_reconstruct_after_glyph_noise_still_promotes_clean_headings():
    text = "# Á #\n## 1.1 Basic Concepts. Modeling\nbody\n"
    out = parse._reconstruct_heads(text)
    assert "# Á #" not in out
    assert out.splitlines()[0].startswith("## 1.1 Basic Concepts")
