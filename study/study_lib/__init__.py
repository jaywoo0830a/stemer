"""study_lib — 교재 RAG 공장 공개 라이브러리.

계약 우선 TDD: `tests/test_*_contract.py`가 이 패키지의 공개 동작을
클라이언트 관점에서 먼저 고정한다.
"""
from . import chunk, discover, embed, factory, figures, ingest, lint, llm, parse, profiles, protocol, registry, render, retrieve, store, subjects, tokens
from .chunk import Chunk, ChunkProfile, chunk_markdown
from .discover import DiscoverReport, discover_topics, topic_title
from .embed import StubEmbedder, TransformerEmbedder, embed_text
from .factory import GenerateResult, generate_one, build_system, build_user
from .figures import Figure, FigureRegistry, attach_figures, register_parsed_figures
from .ingest import BatchReport, IngestReport, ingest_dir, ingest_one
from .lint import LintIssue, lint_katex
from .llm import LLMClient, LLMError, LLMResult, Usage, FlashClient, extract_usage, parse_json_content
from .parse import ParsedBook, get_parser, parse_source
from .profiles import load_profile, profile_names
from .registry import Book, Library, Topic
from .render import render_guide
from .retrieve import (CrossEncoderReranker, Reranker, RetrievedChunk,
                       RetrievedContext, rerank_top, retrieve)
from .store import (IndexStore, IndexedChunk, JsonDurableSink, PgDurableSink,
                    pg_create_sql, pg_delete_book_sql, pg_read_sql, pg_upsert_sql)

__all__ = [
    "chunk", "discover", "embed", "factory", "figures", "ingest", "lint",
    "llm", "parse", "profiles", "protocol", "registry", "render",
    "retrieve", "store", "subjects", "tokens",
    "Chunk", "ChunkProfile", "chunk_markdown",
    "DiscoverReport", "discover_topics", "topic_title",
    "StubEmbedder", "TransformerEmbedder", "embed_text",
    "GenerateResult", "generate_one", "build_system", "build_user",
    "Figure", "FigureRegistry", "attach_figures", "register_parsed_figures",
    "BatchReport", "IngestReport", "ingest_dir", "ingest_one",
    "LintIssue", "lint_katex",
    "LLMClient", "LLMError", "LLMResult", "Usage", "FlashClient",
    "extract_usage", "parse_json_content",
    "ParsedBook", "get_parser", "parse_source",
    "load_profile", "profile_names",
    "Book", "Library", "Topic",
    "render_guide",
    "Reranker", "CrossEncoderReranker", "RetrievedChunk", "RetrievedContext",
    "rerank_top", "retrieve",
    "IndexStore", "IndexedChunk", "JsonDurableSink",
    "PgDurableSink", "pg_create_sql", "pg_delete_book_sql", "pg_read_sql",
    "pg_upsert_sql",
]
