"""청킹 — 파싱된 책 마크다운(헤딩 구조)을 300~500토큰 청크로 분할.

클라이언트 관점: 파싱된 책 마크다운을 넣으면 검색/생성에 쓸 청크 목록이 나온다.
- 헤딩(`#`~`######`)은 섹션 경계 → 각 청크는 현재 섹션에 귀속(`section` 메타).
- 어떤 청크도 `max_tokens` 를 넘지 않는다(단일 문단 초과 시 overlap 으로 하드 분할).
- 동일 입력이면 동일 결과(결정적). 청크 id = `f"{book_id}-{seq:04d}"`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .tokens import Tokenize, default_tokenize

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class ChunkProfile:
    name: str = "default"
    min_tokens: int = 200
    target_tokens: int = 400
    max_tokens: int = 500
    overlap_tokens: int = 60


@dataclass
class Chunk:
    chunk_id: str
    book_id: str
    section: str
    seq: int
    text: str
    tokens: int


def _split_sections(markdown: str) -> list[tuple[str, list[str]]]:
    """헤딩 기준 섹션 분리 → [(heading, lines)] (본문 앞부분은 heading='')."""
    sections: list[tuple[str, list[str]]] = []
    heading = ""
    lines: list[str] = []

    def push() -> None:
        if lines:
            sections.append((heading, list(lines)))  # 사본 저장 (clear 대비)
            lines.clear()

    for line in markdown.splitlines():
        m = HEADING_RE.match(line)
        if m:
            push()
            heading = m.group(2).strip()
        else:
            lines.append(line)
    push()
    if not sections:
        sections = [("", [ln for ln in markdown.splitlines()])]
    return sections


def _section_paragraphs(lines: list[str]) -> list[str]:
    """빈 줄을 문단 구분자로 하여 문단 목록을 만든다."""
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in lines:
        s = line.strip()
        if s:
            buf.append(s)
        elif buf:
            paragraphs.append(" ".join(buf))
            buf = []
    if buf:
        paragraphs.append(" ".join(buf))
    return paragraphs


def _token_prefix_len(text: str, budget: int, tok: Tokenize) -> int:
    """tok 가 단조 증가한다는 전제로, 토큰 예산 안의 최대 문자 길이(이분 탐색)."""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if tok(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _overlap_back(text: str, end: int, overlap_tokens: int, tok: Tokenize) -> int:
    """text[:end] 에서 overlap_tokens 만큼의 토큰을 확보하는 최소 시작점."""
    if overlap_tokens <= 0 or end <= 0:
        return end
    lo, hi = 1, end
    while lo < hi:
        mid = (lo + hi) // 2
        if tok(text[end - mid:end]) >= overlap_tokens:
            hi = mid
        else:
            lo = mid + 1
    return end - lo


def _split_text(text: str, budget: int, overlap_tokens: int, tok: Tokenize) -> list[str]:
    """예산을 넘는 단일 문단을 겹침을 유지하며 하드 분할."""
    if tok(text) <= budget:
        return [text]
    pieces: list[str] = []
    start, n = 0, len(text)
    while start < n:
        take = max(1, _token_prefix_len(text[start:], budget, tok))
        end = start + take
        pieces.append(text[start:end])
        if end >= n:
            break
        nxt = _overlap_back(text, end, overlap_tokens, tok)
        if nxt <= start or nxt >= end:
            nxt = end
        start = nxt
    return pieces


def chunk_markdown(markdown: str, *, book_id: str, profile: ChunkProfile | None = None,
                   tokenize: Tokenize | None = None, start_seq: int = 0) -> list[Chunk]:
    """클라이언트가 쓰는 진입점 — 책 마크다운 → 청크 목록."""
    profile = profile or ChunkProfile()
    tok = tokenize or default_tokenize
    chunks: list[Chunk] = []
    seq = start_seq

    def emit(text: str, section: str) -> None:
        nonlocal seq
        chunks.append(Chunk(chunk_id=f"{book_id}-{seq:04d}", book_id=book_id,
                            section=section, seq=seq, text=text, tokens=tok(text)))
        seq += 1

    for heading, lines in _split_sections(markdown):
        current: list[str] = []
        for para in _section_paragraphs(lines):
            if tok(para) > profile.max_tokens:
                # 단일 문단이 예산을 초과 → 겹침 하드 분할
                if current:
                    emit("\n\n".join(current), heading)
                    current = []
                for piece in _split_text(para, profile.max_tokens,
                                         profile.overlap_tokens, tok):
                    emit(piece, heading)
                continue
            if current and tok("\n\n".join(current + [para])) > profile.max_tokens:
                emit("\n\n".join(current), heading)
                current = []
            current.append(para)
            if tok("\n\n".join(current)) >= profile.target_tokens:
                emit("\n\n".join(current), heading)
                current = []
        if current:
            emit("\n\n".join(current), heading)
    return chunks
