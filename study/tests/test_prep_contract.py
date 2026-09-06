"""사전 준비 강화(Batch A) 계약 테스트 — 품질 게이트·청크 프로필·KaTeX 린트."""
import importlib.util

import pytest

from study_lib.chunk import chunk_markdown
from study_lib.embed import StubEmbedder
from study_lib.factory import generate_one
from study_lib.ingest import ingest_one
from study_lib.lint import lint_katex
from study_lib.llm import LLMResult, Usage
from study_lib.parse import ParsedBook, detect_scanned
from study_lib.profiles import load_profile, profile_names
from study_lib.registry import FAILED, InMemoryStore, Library
from study_lib.store import IndexStore, JsonDurableSink


# ---- ① 품질 게이트(스캔 감지) ----
def test_detect_scanned_flags_short_text_over_many_pages():
    assert detect_scanned(ParsedBook("b", "B", "tiny text", "fast", pages=200))
    assert not detect_scanned(ParsedBook("b", "B", "tiny text", "text", pages=None))
    long_text = "x" * 2000
    assert not detect_scanned(ParsedBook("b", "B", long_text, "fast", pages=10))


def test_ingest_rejects_scanned_like_parse_with_docling_hint(tmp_path, monkeypatch):
    import study_lib.ingest as ingest_mod

    lib = Library(InMemoryStore())
    lib.add_book("scan", "Scan", "math")
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"%PDF placeholder")
    monkeypatch.setattr(
        ingest_mod, "parse_source",
        lambda path, profile=None, book_id="": ParsedBook(
            book_id="scan", title="Scan", markdown="tiny text",
            parser="fast", pages=200, source=str(path)),
    )
    store = IndexStore(JsonDurableSink(tmp_path / "store"))
    report = ingest_one(src, library=lib, store=store, embedder=StubEmbedder(),
                        profile="fast", book_id="scan")
    assert not report.ok
    assert "docling" in report.error
    assert lib.book("scan").status == FAILED


# ---- ② 청크 프로필 책별 연결 ----
def test_profiles_lookup_and_unknown_error():
    assert "compact" in profile_names()
    assert load_profile("compact").max_tokens == 350
    with pytest.raises(ValueError, match="unknown chunk profile"):
        load_profile("nope")


def test_ingest_uses_books_chunk_profile(tmp_path):
    body = "# 1.1 Long paragraph\n\n" + "가" * 380 + "\n"
    lib = Library(InMemoryStore())
    lib.add_book("defbook", "Default", "math")
    lib.add_book("compbook", "Compact", "math", chunk_profile="compact")
    store = IndexStore(JsonDurableSink(tmp_path / "store"))

    d = tmp_path / "d.md"
    d.write_text(body, encoding="utf-8")
    r_default = ingest_one(d, library=lib, store=store, embedder=StubEmbedder(),
                           book_id="defbook")
    c = tmp_path / "c.md"
    c.write_text(body, encoding="utf-8")
    r_compact = ingest_one(c, library=lib, store=store, embedder=StubEmbedder(),
                           book_id="compbook")
    assert r_default.ok and r_compact.ok
    # 유니코드 380토큰 문단: default(≤500)→1청크, compact(≤350)→분할 2청크
    assert store.stats().by_book == {"defbook": 1, "compbook": 2}


# ---- ⑤ KaTeX 린트 ----
def test_lint_katex_finds_forbidden_env_and_macro():
    md = "Clean text with $x$.\n\n\\begin{align} x &= 1 \\end{align}\n$\\bm y$"
    issues = lint_katex(md)
    assert any("align" in i.message for i in issues)
    assert any("\\bm" in i.message for i in issues)


def test_lint_katex_passes_clean_output():
    md = "# T\n\n**Formula.** lim a_n = L ⇔ ∀ε>0 ∃N: n>N ⇒ |a_n−L|<ε\n"
    assert lint_katex(md) == []


# ---- ⑤-b KaTeX MATH-PROTOCOL (출력 최소 + 오류 방지) ----
def test_lint_passes_unicode_first_with_minimal_macros():
    md = ("**Formula.** $y' = f(x,y)$, $\\frac{dy}{dx}$, "
          "$\\sum_{i=1}^n x_i$, $\\int_0^1 x\\,dx$\n")
    assert lint_katex(md) == []


def test_lint_flags_unbalanced_dollars():
    md = "**Formula.** $y' = f(x,y)\n"   # 닫는 $ 없음
    issues = lint_katex(md)
    assert any("$" in i.message for i in issues)


def test_lint_flags_macro_outside_allowed_set():
    md = "**Formula.** $\\dfrac{d}{dx}$ is fine but $\\notarealmacro{x}$ breaks\n"
    issues = lint_katex(md)
    assert any("notarealmacro" in i.message for i in issues)
    # 허용된 \\dfrac 는 보고 안 됨
    assert not any("dfrac" in i.message for i in issues)


def test_lint_passes_plain_unicode_without_dollars():
    # 수식이 없는 일반 문장/유니코드 기호는 $ 없이도 통과 (balance 0)
    md = "The limit is L ⇔ ∀ε>0.  Not math: hello world.\n"
    assert lint_katex(md) == []


def test_factory_reports_lint_warnings_without_failing(tmp_path, schema):
    lib = Library(InMemoryStore())
    lib.add_book("calc", "Calculus", "math")
    topic = lib.add_topic(book_id="calc", title="Limits", section="3.1")
    store = IndexStore()
    payload = {
        "cs": [{"c": "Limit", "d": "Behavior near a point.",
                "f": "lim f = L", "k": "Near, not at.",
                "m": "Direct plug-in."}],
        "r": "Factor and cancel.",
        "as": ["Rate of change."],
        "ex": [{"p": "Compute the limit.",
                "s": "\\begin{align} x &= 1 \\end{align}"}],
        "pr": ["a_n = 1/n"],
    }

    class FakeLLM:
        def complete(self, *, system, user, max_tokens=1000, json_object=True):
            return LLMResult(content=payload, usage=Usage(completion_tokens=10))

    res = generate_one(topic, library=lib, store=store,
                       embedder=StubEmbedder(dim=8), llm=FakeLLM(),
                       schema=schema, guide="G", notes_dir=tmp_path / "notes")
    assert res.status == "draft"          # 린트는 실패가 아니라 경고
    assert res.lint_warnings
    assert any("align" in w for w in res.lint_warnings)


# lint_katex 가 chunk_markdown 과 무관하게 독립 동작하는지 확인용 (import sanity)
def test_chunk_and_lint_coexist():
    from study_lib.tokens import default_tokenize
    chunks = chunk_markdown("# 1.1 A\n\nhello world\n", book_id="b")
    assert chunks and lint_katex(chunks[0].text) == []
