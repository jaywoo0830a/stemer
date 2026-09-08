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


# --- StudyStoreRetriever 어휘 저하(lexical fallback)/ 임베더 501 회귀 가드 -----
class _Hit:
    def __init__(self, book_id, section, text, cid="c"):
        self.book_id, self.section, self.text, self.chunk_id, self.score = \
            book_id, section, text, cid, 0.5


class _StoreStub:
    """study_lib IndexStore 의 search_* 만 흉내 (실 서버용 임베더 장애 시나리오)."""
    def __init__(self, hits):
        self._hits = hits

    def search_dense(self, vector, k=5, *, book_id=None):
        raise RuntimeError("no dense index (vectors absent)")

    def search_text(self, query, k=5, *, book_id=None):
        return self._hits[:k]


class _EmbedRaises:
    """임베딩 서버(--embeddings 없음 501) 대역."""
    def embed_texts(self, texts, *, batch_size=32):
        from agent.gateway import GatewayError
        raise GatewayError("HTTP 501: server does not support embeddings")


def test_store_retriever_lexical_fallback_when_embed_off():
    """임베더가 501 을 내도 BM25 어휘로 저하시켜 결과·mode 를 준다(회귀 가드)."""
    from agent.rag import StudyStoreRetriever
    hit = _Hit("calc", "11.3", "Series test: remainder bound integral tail")
    r = StudyStoreRetriever(store=_StoreStub([hit]),
                            embedder=_EmbedRaises())
    out = r.retrieve("integral test remainder", k=3)
    assert len(out) == 1 and out[0].source == "calc"
    assert "lexical" in r.last_mode   # 임베더 저하 모드임을 기록

