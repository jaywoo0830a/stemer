"""Hierarchical (TOC-aware) chunking of exported textbook markdown.

The heuristics (noise headings, section/chapter shapes, back-matter, sizes)
are configurable per book via YAML profiles — see rag/chunking_profile.py and
study/config/chunking.yaml. Edit the YAML (or add a per-book
chunking.<book_id>.yaml), not this file, to adapt a new textbook.

Docling marks figure captions, "SOLUTION", "EXAMPLE" blocks and front-matter
lines as headings too, which would fragment the real outline. Strategy:

- numbered sections in any common shape open a section:
  "... 1.1" (number at end) / "12.3 Title" (at start) / "Section 3.5 ..." / "3.5"
  — including numbered exercise/review/quiz blocks ("1.1 Exercises"), which
  are first-class retrievable units for problem generation
- numbered chapters ("1 Functions and Models", "CHAPTER 1") open a chapter
- noise headings (FIGURE/SOLUTION/EXAMPLE/PROOF/DEFINITION/Table/■/learning
  objectives/publisher furniture/single letters/very short titles/...) stay
  inside the current section as body text
- chunks before the first numbered section (cover, TOC, preface, diagnostics)
  are dropped from the index
- chunks smaller than min_chars are merged into the following one
- chunks larger than max_chars are split on paragraph boundaries with overlap
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .chunking_profile import ChunkProfile, load_profile

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
    """Split an oversized chunk into <= max_chars pieces.

    Paragraph boundaries are respected; a single paragraph longer than
    max_chars is hard-split with a sliding char window that keeps `overlap`
    chars of continuity between consecutive pieces.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for para in paras:
        subparts = [para]
        if len(para) > max_chars:
            step = max(max_chars - overlap, 1)
            subparts = [para[i : i + max_chars] for i in range(0, len(para), step)]
            # drop a trailing piece that is almost entirely overlap
            if len(subparts) >= 2 and len(subparts[-1]) <= overlap:
                subparts = subparts[:-1]
        for sp in subparts:
            if buf and size + len(sp) > max_chars:
                pieces.append("\n\n".join(buf))
                tail = buf[-1][-overlap:] if overlap else ""
                buf = [tail] if tail else []
                size = len(tail)
            buf.append(sp)
            size += len(sp)
    if buf:
        pieces.append("\n\n".join(buf))
    return pieces or [text]


def split_markdown(
    md_text: str,
    book_id: str,
    min_chars: int | None = None,
    max_chars: int | None = None,
    overlap: int | None = None,
    profile: ChunkProfile | None = None,
) -> list[Chunk]:
    profile = profile or load_profile(book_id)
    min_chars = min_chars or profile.min_chars
    max_chars = max_chars or profile.max_chars
    if overlap is None:
        overlap = profile.overlap

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

            # 0) back-matter answer/solution sections become their own
            #    retrievable chapter (never noise, never last chapter's tail)
            if profile.is_backmatter(title):
                flush()
                buf = [line]
                chapter, section = title, ""
                continue

            # 1) noise headings: keep as body text, never split the outline
            #    (numbered exercise/review/quiz blocks are NOT noise — they
            #     become their own sections, e.g. "1.1 Exercises")
            if profile.is_noise(title):
                buf.append(line)
                continue

            # 2) numbered section in any of the configured shapes:
            #    "... 1.1" | "12.3 Title" | "Section 3.5 ..." | "3.5"
            sec_match = profile.match_section(title)
            if sec_match is not None:
                major, minor, rest = sec_match
                flush()
                buf = [line]
                section = f"{major}.{minor} {rest}".strip()
                if profile.auto_chapter:
                    cm = profile.chapter_major(chapter)
                    if not (cm and cm == major):
                        chapter = f"Chapter {major}"
                continue

            # 3) numbered chapter ("1 Functions ..." / "CHAPTER 1")
            if profile.is_chapter(title):
                flush()
                buf = [line]
                chapter, section = title, ""
                continue

            # 4) very short unnumbered titles are usually noise (D, ;, ...)
            if len(title) < profile.short_title_max_chars:
                buf.append(line)
                continue

            # 5) fall back to the markdown heading level
            if level <= profile.level_chapter_max:
                flush()
                buf = [line]
                chapter, section = title, ""
            elif level <= profile.level_section_max:
                flush()
                buf = [line]
                section = title
            else:
                buf.append(line)
        else:
            buf.append(line)
    flush()

    # Drop front matter (cover, TOC, preface, diagnostics): everything before
    # the first chunk that belongs to a numbered section.
    if profile.drop_front_matter:
        first_body = next(
            (
                i
                for i, (_c, sec, _t) in enumerate(raw)
                if re.search(r"\d{1,3}\.\d{1,3}", sec)
            ),
            None,
        )
        if first_body:
            raw = raw[first_body:]

    # Merge tiny chunks into the following one so retrieval never sees
    # heading-only fragments. Back-matter chapters (answer keys, index) are
    # kept as their own chunks even when short — they are meaningful units.
    merged: list[tuple[str, str, str]] = []
    for chap, sec, text in raw:
        if (
            profile.merge_small_chunks
            and merged
            and len(text) < min_chars
            and not profile.is_backmatter(chap)
        ):
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
