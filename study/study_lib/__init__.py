"""study_lib — 교재 RAG 공장 공개 라이브러리.

계약 우선 TDD: `tests/test_*_contract.py`가 이 패키지의 공개 동작을
클라이언트 관점에서 먼저 고정한다.
"""
from . import chunk, embed, factory, figures, llm, parse, protocol, registry, render, retrieve, store, subjects, tokens
from .chunk import Chunk, ChunkProfile, chunk_markdown
from .embed import StubEmbedder, TransformerEmbedder, embed_text
from .factory import GenerateResult, generate_one, build_system, build_user
from .figures import Figure, FigureRegistry, attach_figures
from .llm import LLMClient, LLMError, LLMResult, Usage, FlashClient, extract_usage, parse_json_content
from .parse import ParsedBook, get_parser, parse_source
from .registry import Book, Library, Topic
from .render import render_guide
from .retrieve import Reranker, RetrievedChunk, RetrievedContext, rerank_top, retrieve
from .store import IndexStore, IndexedChunk, JsonDurableSink

__all__ = [
    "chunk", "embed", "factory", "figures", "llm", "parse", "protocol",
    "registry", "render", "retrieve", "store", "subjects", "tokens",
    "Chunk", "ChunkProfile", "chunk_markdown",
    "StubEmbedder", "TransformerEmbedder", "embed_text",
    "GenerateResult", "generate_one", "build_system", "build_user",
    "Figure", "FigureRegistry", "attach_figures",
    "LLMClient", "LLMError", "LLMResult", "Usage", "FlashClient",
    "extract_usage", "parse_json_content",
    "ParsedBook", "get_parser", "parse_source",
    "Book", "Library", "Topic",
    "render_guide",
    "Reranker", "RetrievedChunk", "RetrievedContext", "rerank_top", "retrieve",
    "IndexStore", "IndexedChunk", "JsonDurableSink",
]
