"""Hierarchical (TOC-aware) chunking of exported textbook markdown.

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
- chunks smaller than chunk_min_chars are merged into the following one
- chunks larger than chunk_max_chars are split on paragraph boundaries
  with a char-level overlap
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import settings

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

# "... 1.1" or "... 1.1, 1.2" style section numbers at the END of a heading.
_SECTION_RE = re.compile(r"^\s*(.+?)\s+(\d{1,3})[.\-](\d{1,3})\s*$")

# "12.3 The Derivative" — section number at the START of a heading.
_SECTION_START_RE = re.compile(r"^(\d{1,3})[.\-](\d{1,3})\s+(.+)$")

# "3.5" alone (number split off the title by the layout model).
_BARE_NUM_RE = re.compile(r"^(\d{1,3})[.\-](\d{1,3})$")

# "Section 3.5 Derivatives" / "SECTION 3.5".
_SECTION_WORD_RE = re.compile(r"(?i)^section\s+(\d{1,3})[.\-](\d{1,3})\s*(.*)$")

# "1 Functions and Models" — numbered chapter heading.
_CHAPTER_RE = re.compile(r"^\s*(\d{1,3})\s+[A-Za-z\uac00-\ud7a3]")

# "CHAPTER 1" / "PART 2".
_CHAPTER_WORD_RE = re.compile(r"^\s*(?:chapter|part)\s+(\d{1,3})\s*$", re.I)

# Back-matter answer/solution sections: keep them as their own retrievable
# chapters (solutions are grounding material for problem generation).
_BACKMATTER_RE = re.compile(
    r"(?i)^\s*(answers?\s+to|solutions?\s+to|solutions?\s+manual|answer\s+key|"
    r"appendix|index|glossary)\b"
)

# Docling noise headings that must never split the outline.
_NOISE_RE = re.compile(
    r"(?i)^\s*("
    # captions / blocks / labeled boxes
    r"figure|solution|example|table|problem|proof|theorem|lemma|corollary|axiom"
    r"|definition|definitions|property|properties|rule|rules"
    r"|answers?\s+to|answer\s+key|checkpoint|practice|quiz|test\s+yourself|self[- ]test"
    r"|check\s+your\s+understanding|vocabulary|key\s+terms?|key\s+concepts?|checklist"
    r"|objectives?|learning\s+targets?|essential\s+question|big\s+idea|big\s+question"
    r"|why\s+you\s+should|what\s+you\s+should|chapter\s+opener|chapter\s+summary"
    r"|lesson|how\s+to|getting\s+started|quick\s+check|try\s+it|explore|investigat"
    r"|activity|activities|applied\s+project|discovery\s+project|writing\s+project"
    r"|project|laboratory|lab\b|case\s+study|animation|video|focus\s+on|connection"
    r"|extension|extend"
    # front/back matter & publisher furniture
    r"|diagnostic|webassign|cengage|stewart|ancillar|acknowledg|about\s+the\s+authors"
    r"|a\s+tribute|to\s+the\s+student|preface|alternate|what'?s\s+new|features|content"
    r"|instructor|test\s+bank|complete\s+solutions|single\s+variable|multivariable"
    r"|problems\s+plus|appendix|reference\s+page|reviewers|technology|graphing\s+icon"
    r"|technology\s+icon|table\s+of\s+contents|contents|index|glossary|bibliography"
    r"|references|credits|photo\s+credits|copyright|isbn|cataloging"
    r"|library\s+of\s+congress|all\s+rights\s+reserved|printed\s+in|edition"
    r"|mylab|mastering|mindtap|online|homework|calcchat|calcview|new\s+from"
    r"|real[-\s]?world|summary|review|n\s+|■"
    r")"
    r"|^\s*[a-z]\s*$"
    r"|^\s*[^\w\uac00-\ud7a3]{1,3}\s*$"
)


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

            # 0) back-matter answer/solution sections become their own
            #    retrievable chapter (never noise, never last chapter's tail)
            if _BACKMATTER_RE.match(title):
                flush()
                buf = [line]
                chapter, section = title, ""
                continue

            # 1) noise headings: keep as body text, never split the outline
            #    (numbered exercise/review/quiz blocks are NOT noise — they
            #     become their own sections, e.g. "1.1 Exercises")
            if _NOISE_RE.match(title):
                buf.append(line)
                continue

            # 2) numbered section in any of three common shapes:
            #    "... 1.1" | "12.3 The Derivative" | "Section 3.5 ..." | "3.5"
            sec_match: tuple[str, str, str] | None = None
            m_end = _SECTION_RE.match(title)
            if m_end:
                sec_match = (m_end.group(2), m_end.group(3), m_end.group(1).strip())
            if sec_match is None:
                m_start = _SECTION_START_RE.match(title) or _BARE_NUM_RE.match(title)
                if m_start:
                    rest = m_start.group(3).strip() if m_start.lastindex and m_start.lastindex >= 3 else ""
                    sec_match = (m_start.group(1), m_start.group(2), rest)
            if sec_match is None:
                m_word = _SECTION_WORD_RE.match(title)
                if m_word:
                    sec_match = (m_word.group(1), m_word.group(2), m_word.group(3).strip())
            if sec_match is not None:
                major, minor, rest = sec_match
                flush()
                buf = [line]
                section = f"{major}.{minor} {rest}".strip()
                cm = _CHAPTER_RE.match(chapter)
                if not (cm and cm.group(1) == major):
                    chapter = f"Chapter {major}"
                continue

            # 3) numbered chapter ("1 Functions ..." / "CHAPTER 1")
            c = _CHAPTER_WORD_RE.match(title) or _CHAPTER_RE.match(title)
            if c:
                flush()
                buf = [line]
                chapter, section = title, ""
                continue

            # 4) very short unnumbered titles are usually noise (D, ;, ...)
            if len(title) < 20:
                buf.append(line)
                continue

            # 5) fall back to the markdown heading level
            if level <= 2:
                flush()
                buf = [line]
                chapter, section = title, ""
            elif level <= 3:
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
        if merged and len(text) < min_chars and not _BACKMATTER_RE.match(chap):
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
