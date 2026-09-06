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
import os
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
    """글리프 코드(/H + 숫자)와 순수 쓰레기 줄 제거 — 폰트 매핑 깨진 PDF 정화."""
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

    def parse(self, path: str | Path, *, book_id: str = "",
              page_range: tuple[int, int] | None = None) -> ParsedBook: ...


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def parse_page_range(spec: str | None) -> tuple[int, int] | None:
    """'42-1249' (1-based inclusive) → (42, 1249). None/빈 값 → None(전체)."""
    if not spec:
        return None
    s = str(spec).strip()
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", s)
    if not m:
        raise ValueError(f"invalid page_range {spec!r}; expected 'start-end' (1-based)")
    start, end = int(m.group(1)), int(m.group(2))
    if start < 1 or end < start:
        raise ValueError(f"invalid page_range {spec!r}; need 1 <= start <= end")
    return (start, end)


class TextParser:
    name = "text"

    def parse(self, path: str | Path, *, book_id: str = "",
              page_range: tuple[int, int] | None = None) -> ParsedBook:
        p = Path(path)
        markdown = p.read_text(encoding="utf-8")
        return ParsedBook(book_id=book_id or p.stem, title=p.stem, markdown=markdown,
                          parser=self.name, source=str(p))


class FastPdfParser:
    """텍스트 레이어 PDF 전용 — 빠르지만 스캔본은 내용이 안 나온다."""
    name = "fast"

    def parse(self, path: str | Path, *, book_id: str = "",
              page_range: tuple[int, int] | None = None) -> ParsedBook:
        if not _has_module("pypdf"):
            raise RuntimeError(
                "fast PDF parser needs 'pypdf' (pip install pypdf). "
                "스캔본이면 profile='docling' 을 쓰세요."
            )
        import pypdf  # type: ignore

        p = Path(path)
        reader = pypdf.PdfReader(str(p))
        raw_pages = [page.extract_text() or "" for page in reader.pages]
        total = len(raw_pages)
        if page_range is not None:
            start, end = page_range
            if end > total:
                end = total
            if start > total:
                start, end = total, total
            raw_pages = raw_pages[start - 1:end]
        pages = [_reconstruct_heads(t) for t in raw_pages]
        return ParsedBook(book_id=book_id or p.stem, title=p.stem,
                          markdown="\n\n".join(pages), parser=self.name,
                          pages=len(pages), source=str(p))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _docling_threads() -> int:
    """docling 추론 스레드 수.

    우선순위: DOCLING_THREADS 명시 > OMP_NUM_THREADS(워커가 설정한 캡) >
    (전체 코어 - 1). jobs 병렬로 워커가 OMP 캡을 걸면 이를 존중해
    워커×docling 전체코어 oversubscription 을 막는다.
    """
    explicit = _env_int("DOCLING_THREADS", 0)
    if explicit > 0:
        return explicit
    omp = _env_int("OMP_NUM_THREADS", 0)
    if omp > 0:
        return omp
    return max(1, (os.cpu_count() or 4) - 1)


def _build_docling_converter():
    """docling 2.126.0 최적화 DocumentConverter 구성.

    성능 전략 (CPU):
    - AcceleratorOptions: 전체 코어 사용 (기본 4스레드 한계 제거)
    - heading_hierarchy: PDF 북마크/번호로 헤딩 레벨 추론 → `## 1.1 제목` 구조
    - TableFormerMode.FAST: 테이블 속도 우선
    - OCR/이미지/수식 기본 OFF (텍스트 레이어 PDF는 불필요) — 환경변수로 ON
    """
    from docling.datamodel.accelerator_options import (  # type: ignore
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.pipeline_options import (  # type: ignore
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption  # type: ignore

    accel = AcceleratorOptions(num_threads=_docling_threads(),
                               device=AcceleratorDevice.CPU)
    opts = PdfPipelineOptions()
    opts.accelerator_options = accel
    # 기본값: 텍스트 레이어 PDF 기준 (스캔본은 DOCLING_OCR=1)
    opts.do_ocr = os.environ.get("DOCLING_OCR", "0") == "1"
    opts.do_table_structure = os.environ.get("DOCLING_TABLES", "1") != "0"
    opts.do_formula_enrichment = False
    opts.do_code_enrichment = False
    opts.images_scale = 1.0
    opts.generate_page_images = False
    opts.generate_picture_images = False
    # 테이블: 정확도보다 속도 (복잡 테이블은 DOCLING_TABLES_ACCURATE=1)
    try:
        if os.environ.get("DOCLING_TABLES_ACCURATE", "0") != "1":
            opts.table_structure_options.mode = TableFormerMode.FAST
    except Exception:
        pass  # 버전 차이 시 기본값 유지
    # 헤딩 레벨 추론 — discover 가 `## N.N 제목` 구조를 얻는 핵심
    try:
        opts.heading_hierarchy_options.enabled = True
    except Exception:
        pass  # 버전 차이 시 기본값 유지
    fmt = PdfFormatOption(pipeline_options=opts)
    return DocumentConverter(format_options={"pdf": fmt})


class DoclingParser:
    """docling 풀 파싱 — 복잡 레이아웃/스캔본용 (2.126.0 성능 최적화).

    텍스트 레이어가 온전한 PDF는 profile='fast'(pypdf) 가 훨씬 빠르므로,
    Cengage/Stewart 처럼 pypdf 가 헤딩 구조를 못 살리는 책에만 사용한다.

    환경변수 (성능/품질 트레이드오프):
      DOCLING_THREADS          추론 스레드 수 (기본: 전체코어-1)
      DOCLING_OCR=1            스캔본 OCR 활성화 (기본 off)
      DOCLING_TABLES=0         테이블 구조 추출 비활성 (속도)
      DOCLING_TABLES_ACCURATE=1  TableFormer 정확도 모드 (기본 fast)
    """
    name = "docling"

    def parse(self, path: str | Path, *, book_id: str = "",
              page_range: tuple[int, int] | None = None) -> ParsedBook:
        if not _has_module("docling"):
            raise RuntimeError(
                "docling parser needs optional 'docling' (pip install docling). "
                "텍스트 PDF면 profile='fast' 가 훨씬 빠릅니다."
            )
        p = Path(path)
        converter = _build_docling_converter()
        kwargs = {}
        if page_range is not None:
            kwargs["page_range"] = page_range   # (start, end) 1-based inclusive
        result = converter.convert(str(p), **kwargs)
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


def parser_names() -> tuple[str, ...]:
    """등록된 파서 프로필 이름 목록 (CLI choices/검증용)."""
    return tuple(PARSER_PROFILES)


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
                 book_id: str = "",
                 page_range: str | tuple[int, int] | None = None) -> ParsedBook:
    """클라이언트가 쓰는 진입점 — 파일 → ParsedBook.

    page_range: '42-1249'(1-based inclusive) 문자열 또는 (start, end) 튜플.
    """
    name = resolve_profile(path, profile)
    if isinstance(page_range, str):
        page_range = parse_page_range(page_range)
    return get_parser(name).parse(path, book_id=book_id, page_range=page_range)
