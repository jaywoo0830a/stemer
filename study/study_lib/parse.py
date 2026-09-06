"""파싱 — PDF/TXT → 마크다운 (파서 프로필 선택).

클라이언트 관점: 소스 파일 경로(+프로필)를 주면 `ParsedBook.markdown`이 나온다.
- `profile="text"`    : 순수 텍스트(.txt) — 의존성 없음
- `profile="fast"`    : 텍스트 레이어 PDF(pypdf) — 빠름, 스캔본 불가
- `profile="docling"` : OCR/VLM 풀 파싱(docling) — 스캔본·수식 지원, 느림

의존성이 없으면 **어떤 패키지를 설치해야 하는지** 알려주는 `RuntimeError`.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class ParsedBook:
    book_id: str
    title: str
    markdown: str
    parser: str
    pages: int | None = None
    source: str = ""
    figures: tuple = ()   # docling 등에서 추출된 Figure 목록 (없으면 빈 튜플)


class Parser(Protocol):
    name: str

    def parse(self, path: str | Path, *, book_id: str = "") -> ParsedBook: ...


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


class TextParser:
    name = "text"

    def parse(self, path: str | Path, *, book_id: str = "") -> ParsedBook:
        p = Path(path)
        markdown = p.read_text(encoding="utf-8")
        return ParsedBook(book_id=book_id or p.stem, title=p.stem, markdown=markdown,
                          parser=self.name, source=str(p))


class FastPdfParser:
    """텍스트 레이어 PDF 전용 — 빠르지만 스캔본은 내용이 안 나온다."""
    name = "fast"

    def parse(self, path: str | Path, *, book_id: str = "") -> ParsedBook:
        if not _has_module("pypdf"):
            raise RuntimeError(
                "fast PDF parser needs 'pypdf' (pip install pypdf). "
                "스캔본이면 profile='docling' 을 쓰세요."
            )
        import pypdf  # type: ignore

        p = Path(path)
        reader = pypdf.PdfReader(str(p))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return ParsedBook(book_id=book_id or p.stem, title=p.stem,
                          markdown="\n\n".join(pages), parser=self.name,
                          pages=len(pages), source=str(p))


class DoclingParser:
    """OCR/VLM 풀 파싱 — 스캔본·수식 지원, 느림(무거운 선택 의존성)."""
    name = "docling"

    def parse(self, path: str | Path, *, book_id: str = "") -> ParsedBook:
        if not _has_module("docling"):
            raise RuntimeError(
                "docling parser needs optional 'docling' (pip install 'docling[vlm]'). "
                "텍스트 PDF면 profile='fast' 가 훨씬 빠릅니다."
            )
        from docling.document_converter import DocumentConverter  # type: ignore

        p = Path(path)
        result = DocumentConverter().convert(str(p))
        markdown = result.document.export_to_markdown()
        return ParsedBook(book_id=book_id or p.stem, title=p.stem, markdown=markdown,
                          parser=self.name, pages=None, source=str(p))


PARSER_PROFILES: dict[str, type[Parser]] = {
    "text": TextParser,
    "fast": FastPdfParser,
    "docling": DoclingParser,
}

_EXT_DEFAULT = {".txt": "text", ".md": "text", ".pdf": "fast"}

MIN_CHARS_PER_PAGE = 150


def detect_scanned(parsed: ParsedBook, min_chars_per_page: int = MIN_CHARS_PER_PAGE) -> bool:
    """페이지 수 대비 추출 텍스트가 너무 적으면 스캔본 의심 (품질 게이트)."""
    if parsed.pages is None or parsed.pages <= 0:
        return False
    return len(parsed.markdown.strip()) < min_chars_per_page * parsed.pages


def get_parser(name: str) -> Parser:
    if name not in PARSER_PROFILES:
        raise ValueError(f"unknown parser profile {name!r}; known: {sorted(PARSER_PROFILES)}")
    return PARSER_PROFILES[name]()


def resolve_profile(path: str | Path | None, profile: str | None = None) -> str:
    """명시 프로필 우선, 없으면 확장자로 추론(.txt→text, .pdf→fast)."""
    if profile is not None:
        if profile not in PARSER_PROFILES:
            raise ValueError(f"unknown parser profile {profile!r}; known: {sorted(PARSER_PROFILES)}")
        return profile
    if path is not None:
        ext = Path(path).suffix.lower()
        if ext in _EXT_DEFAULT:
            return _EXT_DEFAULT[ext]
        raise ValueError(f"cannot infer parser for {path!r}; pass profile= explicitly")
    raise ValueError("profile is required when path is not given")


def parse_source(path: str | Path, *, profile: str | None = None,
                 book_id: str = "") -> ParsedBook:
    """클라이언트가 쓰는 진입점 — 파일 → ParsedBook."""
    name = resolve_profile(path, profile)
    return get_parser(name).parse(path, book_id=book_id)
