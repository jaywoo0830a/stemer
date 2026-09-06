"""파싱 — PDF/TXT → 마크다운 (파서 프로필 선택).

클라이언트 관점: 소스 파일 경로(+프로필)를 주면 `ParsedBook.markdown`이 나온다.
- `profile="text"`    : 순수 텍스트(.txt) — 의존성 없음
- `profile="fast"`    : 텍스트 레이어 PDF(pypdf) — 빠름, 스캔본 불가
- `profile="docling"` : OCR/VLM 풀 파싱(docling) — 스캔본·수식 지원, 느림

pypdf 는 글자만 뽑을 뿐 **헤딩 구조를 만들지 않으므로**, fast 프로필은
`_reconstruct_heads` 로 `1.1 제목` 형태 줄을 `## 헤딩`으로 승격한다.
(목차 페이지는 줄이 짧고 헤딩 후보가 몰려 있어 제외 — 가짜 섹션 방지.)

의존성이 없으면 **어떤 패키지를 설치해야 하는지** 알려주는 `RuntimeError`.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


_NUM_HEAD_RE = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+[A-Z]")
_NUM_HEAD_ONLY_RE = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+[A-Z].{0,80}$")
_TOC_MIN = 6       # 이 개수 이상 헤딩 후보면 목차 페이지로 간주
_MAX_HEAD_LEN = 90
# 목차 항목은 줄 끝에 페이지 번호(1~4자리)가 붙는다 → 본문 헤딩과 구분
_PAGE_NUM_TAIL = re.compile(r"\s+\d{1,4}\s*$")
# pypdf 가 ToUnicode 깨진 폰트에서 뱉는 글리프 코드(/H11005 등) → 제거
_GLYPH_CODE = re.compile(r"/H\d+")
_WORDY = re.compile(r"[A-Za-z0-9]")


def _clean_glyph_noise(text: str) -> str:
    """글리프 코드(/H\d+)와 순수 쓰레기 줄 제거 — 폰트 매핑 깨진 PDF 정화."""
    out: list[str] = []
    for ln in text.splitlines():
        s = re.sub(r"\s+", " ", _GLYPH_CODE.sub(" ", ln)).strip()
        # 대문자 '#'-시작 줄은 pypdf 가 '#' 로 매핑한 깨진 글리프 잔재 → 제거
        if not s or s == "#" or (s.startswith("#") and not _WORDY.search(s[1:])):
            continue
        out.append(s)
    return "\n".join(out)


def _looks_like_prose(title: str) -> bool:
    """번호 뒤 제목이 본문 문장처럼 보이는가? (오탐 필터)

    진짜 제목은 Title Case(첫 단어 제외 대문자 비율 높음). 본문 문장은
    소문자 단어가 연속된다: "US quart) is vibrating up and down under the".
    """
    words = title.split()
    if len(words) < 3:
        return False
    rest = words[1:]
    caps = sum(1 for w in rest if w[:1].isupper())
    return caps / len(rest) < 0.5


def _looks_like_toc(lines: list[str]) -> bool:
    """페이지의 대부분 줄이 짧은 점-번호 헤딩이면 목차(TOC)로 판단."""
    if len(lines) < _TOC_MIN:
        return False
    hits = sum(1 for ln in lines if _NUM_HEAD_ONLY_RE.match(ln))
    # 헤딩 후보가 절반 이상 & 줄이 전반적으로 짧으면 목차
    short = [ln for ln in lines if ln.strip() and len(ln.strip()) < 100]
    if not short:
        return False
    return hits >= max(_TOC_MIN, len(short) // 2)


def _reconstruct_heads(page_text: str) -> str:
    """페이지 텍스트에서 `1.1 제목` 형태 줄을 `## 헤딩`으로 승격.

    목차 페이지는 승격하지 않는다(가짜 섹션 방지). 또한
    - 줄 끝에 페이지 번호가 붙은 목차 항목
    - 번호 뒤가 본문 문장(소문자 연속)인 줄
    은 승격하지 않는다. 먼저 pypdf 글리프 노이즈(/H 숫자, #-쓰레기)를 제거한다.
    """
    page_text = _clean_glyph_noise(page_text)
    lines = page_text.splitlines()
    if _looks_like_toc(lines):
        return page_text
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if (s and len(s) <= _MAX_HEAD_LEN and _NUM_HEAD_RE.match(s)
                and not _PAGE_NUM_TAIL.search(s)
                and not _looks_like_prose(s)):
            out.append("## " + s)
        else:
            out.append(ln)
    return "\n".join(out)


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
        pages = [_reconstruct_heads(page.extract_text() or "") for page in reader.pages]
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
