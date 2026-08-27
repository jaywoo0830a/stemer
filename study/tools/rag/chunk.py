"""Hierarchical (TOC-aware) chunking of exported textbook markdown.

Strategy:
- level 1-2 headings open a new chapter
- level 3 headings open a new section (the default chunk unit)
- level >= 4 headings stay inside the current section
- chunks smaller than chunk_min_chars are merged into the following one
- chunks larger than chunk_max_chars are split on paragraph boundaries
  with a char-level overlap
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import settings

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


@dataclass
class Chunk:
    chunk_id: str
    book_id: str
    chapter: str
    section: str
    text: str
    seq: int = 0

    @property
    def metadata(self) -> dict:
        return {"book_id": self.book_id, "chapter": self.chapter, "section": self.section}


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split an oversized chunk on paragraph boundaries with char overlap."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text)]
    paras = [p for p in paras if p]
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for para in paras:
        if buf and size + len(para) > max_chars:
            pieces.append("\n\n".join(buf))
            tail = buf[-1][-overlap:] if overlap else ""
            buf = [tail] if tail else []
            size = len(tail)
        buf.append(para)
        size += len(para)
    if buf:
        pieces.append("\n\n".join(buf))
    return pieces or [text]


def split_markdown(
    md_text: str,
    book_id: str,
    min_chars: int | None = None,
    max_chars: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    min_chars = min_chars or settings.chunk_min_chars
    max_chars = max_chars or settings.chunk_max_chars
    if overlap is None:
        overlap = settings.chunk_overlap

    raw: list[tuple[str, str, str]] = []  # (chapter, section, text)
    chapter = ""
    section = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        if text:
            raw.append((chapter, section, text))
        buf = []

    for line in md_text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if level <= 2:
                flush()
                buf = [line]
                chapter, section = title, ""
            elif level <= 3:
                flush()
                buf = [line]
                section = title
            else:
                buf.append(line)  # sub-subsection: keep inside current chunk
        else:
            buf.append(line)
    flush()

    # Merge tiny chunks into the following one so retrieval never sees
    # heading-only fragments.
    merged: list[tuple[str, str, str]] = []
    for chap, sec, text in raw:
        if merged and len(text) < min_chars:
            c0, s0, t0 = merged[-1]
            merged[-1] = (c0 or chap, s0 or sec, t0 + "\n\n" + text)
        else:
            merged.append((chap, sec, text))

    chunks: list[Chunk] = []
    seq = 0
    for chap, sec, text in merged:
        pieces = _split_long(text, max_chars, overlap) if len(text) > max_chars else [text]
        for piece in pieces:
            chunks.append(
                Chunk(
                    chunk_id=f"{book_id}:{seq:05d}",
                    book_id=book_id,
                    chapter=chap,
                    section=sec,
                    text=piece,
                    seq=seq,
                )
            )
            seq += 1
    return chunks
