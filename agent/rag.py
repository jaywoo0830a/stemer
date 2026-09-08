"""rag — 근거 검색 (text RAG + code-index stub). NEW-METHOD §4, §환각차단.

각에이전트가 답을 만들 때 로컬 저장소에서 근거 청크만 주입해 모델이 "없는 지식"을
지어내지 못하게 한다. 두 계층:

1. TextRAG (이공계 문서/교재):
   - KeywordRetriever     — 결정적(토큰 매치). 테스트/오프라인 기본.
   - StudyStoreRetriever  — study_lib.IndexStore(+embedder) 재사용, 밀집+어휘 검색.
     lazy import 라서 study_lib 없으면 ImportError 대신 동작 불가로 안내.

2. CodeIndex (§4.2)  — AST 분할+임베딩+호출그래프 저장 · 검색의 "뼈대".
   실제 인덱싱(문서→store)은 큰 작업이라 여기선 구조/계약만 잡고, 검색은
   text-index 병행 또는 외부 AST 인덱스를 붙일 슬롯으로 제공한다.

공통 인터페이스: `retrieve(query, k) -> list[Chunk]`
Chunk: (source/badge, text) 근거 인용.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, Sequence

from .gateway import GatewayError


@dataclass(frozen=True)
class Chunk:
    source: str   # book_id 또는 파일 경로
    section: str  # 책 섹션 / 파일 심볼
    text: str
    score: float = 0.0

    @property
    def badge(self) -> str:
        return f"[{self.source} · {self.section}]" if self.section else f"[{self.source}]"


class Retriever(Protocol):
    def retrieve(self, query: str, k: int = 5) -> list[Chunk]: ...


def _terms(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9A-Za-z가-힣]+", text.lower()) if t}


class KeywordRetriever:
    """결정적 근거 검색 — 저장소가 없어도 오케스트레이터/테스트가 돌도록.

    corpus 는 (source, section, text) 튜플 or dict 목록. BM25 대신 단순
    공통 토큰 가중치(제목·본문) 로 순위. RAG '없음 환경 폴백' 및 계약 테스트용.
    """

    def __init__(self, docs: Optional[Sequence[dict]] = None) -> None:
        self._docs: list[dict] = list(docs) if docs else []
        for d in self._docs:
            d.setdefault("section", "")
            d.setdefault("score", 0.0)

    def add(self, source: str, text: str, *, section: str = "") -> None:
        self._docs.append({"source": source, "section": section, "text": text})

    @property
    def count(self) -> int:
        return len(self._docs)

    def retrieve(self, query: str, k: int = 5) -> list[Chunk]:
        q = _terms(query)
        if not q:
            return []
        scored = []
        for d in self._docs:
            title = _terms(d.get("section", ""))
            body = _terms(d.get("text", ""))
            s = len(title & q) * 3 + len(body & q)
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda t: (-t[0], t[1]["source"], t[1]["section"]))
        return [Chunk(source=d["source"], section=d.get("section", ""),
                      text=d["text"], score=s)
                for s, d in scored[:k]]


class StudyStoreRetriever:
    """study_lib.IndexStore 재사용 — 로컬 교재 RAG (책 청크) 검색.

    스토어 경로는 env STEMER_STORE (기본 <repo>/study/data/store). 서버 배치 시
    /data/store 로 보낸다. 임베더는 주입받는 Gateway/OllamaEmbed 로 실제 모델.
    study_lib 의 store/search 를 그대로 쓴다(섹션 매칭 이상은 retrieve 에 위임).
    """

    def __init__(self, store: object, embedder: object, *, dim: int = 3072) -> None:
        """store: study_lib.store.IndexStore, embedder: study_lib.embed.Embedder."""
        self._store = store
        self._embedder = embedder
        self._dim = dim

    @classmethod
    def load(cls, store_dir: Optional[str] = None,
             embedder: Optional[object] = None, *, dim: int = 3072,
             embed_base: str = "http://127.0.0.1:11434",
             embed_model: str = "qwen2.5:3b") -> "StudyStoreRetriever":
        """study_lib 패키지 + 스토어를 lazily 로드해 실제 인스턴스를 만든다."""
        from agent.gateway import OllamaEmbed  # noqa
        try:
            _import_study_lib()
            from study_lib.store import IndexStore, JsonDurableSink
            from study_lib.embed import Embedder  # noqa: F401  (규약만)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "StudyStoreRetriever needs study_lib on path (run deploy on server "
                "where study/study_lib is importable); inner: " + str(exc)) from None
        if embedder is None:
            embedder = OllamaEmbed(base_url=embed_base, model=embed_model,
                                   timeout=120.0, dim=dim)
        sink_dir = store_dir or cls._default_store_dir()
        sink = JsonDurableSink(sink_dir)
        store = IndexStore(sink)
        store.load_all()
        return cls(store=store, embedder=embedder, dim=dim)

    @staticmethod
    def _default_store_dir() -> str:
        repo = Path(__file__).resolve().parents[1]
        return os.environ.get("STEMER_STORE") or str(repo / "study" / "data" / "store")

    def retrieve(self, query: str, k: int = 5) -> list[Chunk]:
        from study_lib.store import SearchHit  # noqa
        vec = self._embedder.embed_texts([query], batch_size=1)[0]
        hits = self._store.search_dense(tuple(vec), k=k)
        if len(hits) < k:  # 밀집이 부족하면 어휘(BM25) 보완
            extra = {h.chunk_id for h in hits}
            for h in self._store.search_text(query, k=k):
                if h.chunk_id not in extra:
                    hits.append(h)
                    if len(hits) >= k:
                        break
        return [Chunk(source=h.book_id, section=h.section, text=h.text, score=h.score)
                for h in hits]


def _import_study_lib() -> None:
    """workspace/서버 배치 구조에서 study_lib 를 import 경로에 올린다."""
    if "study_lib" in sys.modules:
        return
    here = Path(__file__).resolve().parents[1]
    for cand in (here / "study", here.parent / "study"):
        if (cand / "study_lib").is_dir():
            sys.path.insert(0, str(cand))
            return
    # 이미 sys.path 에 없으면 경고 없이 StudyStoreRetriever 가 런타임에 잡아준다.


# --------------------------------------------------------------------------- #
# 코드 인덱스 뼈대 (NEW-METHOD §4.2) — 실제 인덱싱/그래프는 후속 작업.
# --------------------------------------------------------------------------- #
class CodeOutcome:
    pass


@dataclass
class CodeSymbol:
    path: str
    name: str
    kind: str            # function/class/method
    signature: str = ""
    doc: str = ""        # LLM이 만든 자연어 설명(임베딩/텍스트검색용)
    start_line: int = 0


class CodeIndex:
    """함수/클래스 → docstring(으)로 텍스트 검색 · 호출 관계 그래프 저장 슬롯.

    뼈대 버전: 신호(symbols)만 메모리에 두고 retrieve 는 doc/이름 부분매치로 근사.
    AST 인덱서/호출그래프(§4.2)는 add_file/relation 구현으로 확장하면 된다.
    """

    def __init__(self) -> None:
        self._symbols: list[CodeSymbol] = []
        self._calls: dict[tuple[str, str], str] = {}   # (path,sym)->doc

    def index_symbol(self, sym: CodeSymbol) -> None:
        self._symbols.append(sym)

    def index_file(self, path: str, symbols: Sequence[CodeSymbol]) -> int:
        for s in symbols:
            self._symbols.append(CodeSymbol(
                path=path, name=s.name, kind=s.kind, signature=s.signature,
                doc=s.doc, start_line=s.start_line))
        return len(symbols)

    def relation(self, caller: str, callee: str) -> None:
        """호출 그래프 엣지 — 영향 분석용 (SQLite 백엔드 교체 지점)."""
        self._calls[(caller, callee)] = "calls"

    @property
    def size(self) -> int:
        return len(self._symbols)

    def retrieve(self, query: str, k: int = 5) -> list[Chunk]:
        q = _terms(query)
        if not q:
            return []
        scored: list[tuple[int, CodeSymbol]] = []
        for s in self._symbols:
            n = len(_terms(s.name) & q) * 4 + len(_terms(s.doc) & q) * 2
            if n:
                scored.append((n, s))
        scored.sort(key=lambda t: (-t[0], t[1].path, t[1].name))
        return [Chunk(source=s.path, section=f"{s.kind} {s.name}",
                      text=(s.signature + "\n" + s.doc).strip(), score=n)
                for n, s in scored[:k]]
