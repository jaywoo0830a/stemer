"""rag 계약 — 근거 검색 결정성 (keyword store + study store plus code-index stub)."""
import pytest

from agent.rag import Chunk, CodeIndex, CodeSymbol, KeywordRetriever


def test_keyword_retriever_orders_by_overlap():
    r = KeywordRetriever()
    r.add("heat", "explicit euler diverges by cfl condition", section="3.1")
    r.add("calc", "sin integral zero over symmetric interval", section="orth")
    hits = r.retrieve("sin symmetric interval integral", k=1)
    assert hits[0].source == "calc"
    assert hits[0].badge == "[calc · orth]"


def test_keyword_retriever_k_cuts():
    r = KeywordRetriever()
    for i in range(5):
        r.add("b", f"doc about derivative index {i}", section="s")
    assert len(r.retrieve("derivative index", k=3)) <= 3


def test_keyword_empty_query_returns_none():
    r = KeywordRetriever()
    r.add("b", "some text", section="s")
    assert r.retrieve("   ") == []


def test_keyword_no_match():
    r = KeywordRetriever()
    r.add("b", "zeros of zeta", section="s")
    assert KeywordRetriever([]) and r.retrieve("quantum chromodynamics", k=5) == []


def test_keyword_needs_no_network():
    # config 없는 완결성: 그냥 리스트만으로 문서를 만들 수 있어야 한다.
    from agent.rag import Chunk as _  # noqa:F401
    r = KeywordRetriever([{"source": "x", "section": "y", "text": "shared token here"}])
    assert r.count == 1


def test_code_index_symbol_search():
    ci = CodeIndex()
    ci.index_symbol(CodeSymbol(path="heat_solver.py", name="explicit_euler_step",
                               kind="function", signature="def explicit_euler_step(u):",
                               doc="advance by one explicit euler timestep for heat"))
    ci.index_symbol(CodeSymbol(path="utils.py", name="read_data",
                               kind="function", doc="read csv rows"))
    hits = ci.retrieve("explicit euler heat timestep", k=1)
    assert hits and hits[0].source == "heat_solver.py"


def test_code_index_relations():
    ci = CodeIndex()
    ci.relation("a", "b")
    ci.index_symbol(CodeSymbol(path="p", name="f", kind="function"))
    assert ci.size == 1
