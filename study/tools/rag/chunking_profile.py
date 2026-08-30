"""Chunking profiles — YAML-configurable TOC-aware chunking heuristics.

The heuristics that used to be hard-coded in rag/chunk.py (noise headings,
section/chapter shapes, back-matter, sizes) now live in YAML profiles so a
new textbook can be indexed by editing a YAML file only.

Lookup (deep-merged, later wins):
    built-in defaults (+ CHUNK_* env vars as base)
    -> study/config/chunking.yaml                 (default, applies to all books)
    -> study/config/chunking.<book_id>.yaml       (per-book override)

All regexes are compiled with re.IGNORECASE and matched with re.match
(anchored at the start of the heading title). Write them as SINGLE-QUOTED
YAML strings so backslashes (\\s, \\d, ...) stay literal.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Pattern

from .config import settings

try:
    import yaml
except ImportError:  # pragma: no cover - host without PyYAML
    yaml = None

# Built-in defaults (mirror study/config/chunking.yaml; used when the file or
# PyYAML is missing, e.g. running test_chunk.py on a bare host).
DEFAULT_DATA: dict = {
    "chunk": {
        "min_chars": 400,
        "max_chars": 1500,
        "overlap": 150,
        "drop_front_matter": True,
        "merge_small_chunks": True,
        "auto_chapter": True,
    },
    "sections": {
        "number_at_end": r"^(?P<title>.+?)\s+(?P<major>\d{1,3})[.\-](?P<minor>\d{1,3})\s*$",
        "number_at_start": r"^(?P<major>\d{1,3})[.\-](?P<minor>\d{1,3})\s+(?P<title>.+)$",
        "bare_number": r"^(?P<major>\d{1,3})[.\-](?P<minor>\d{1,3})$",
        "section_word": r"^section\s+(?P<major>\d{1,3})[.\-](?P<minor>\d{1,3})\s*(?P<title>.*)$",
    },
    "chapters": {
        "word": r"^\s*(?:chapter|part)\s+(?P<major>\d{1,3})\s*$",
        "number_title": r"^\s*(?P<major>\d{1,3})\s+[A-Za-z\uac00-\ud7a3]",
    },
    "backmatter": {
        "enabled": True,
        "patterns": [
            r"^answers?\s+to",
            r"^solutions?\s+to",
            r"^solutions?\s+manual",
            r"^answer\s+key",
            r"^appendix\b",
            r"^index\b",
            r"^glossary\b",
        ],
    },
    "noise": {
        "patterns": [
            "figure", "solution", "example", "table", "problem", "proof",
            "theorem", "lemma", "corollary", "axiom", "definition",
            "definitions", "property", "properties", "rule", "rules",
            r"answers?\s+to", r"answer\s+key", "checkpoint", "practice",
            "quiz", r"test\s+yourself", r"self[- ]test",
            r"check\s+your\s+understanding", "vocabulary", r"key\s+terms?",
            r"key\s+concepts?", "checklist", r"objectives?",
            r"learning\s+targets?", r"essential\s+question", r"big\s+idea",
            r"big\s+question", r"why\s+you\s+should", r"what\s+you\s+should",
            r"chapter\s+opener", r"chapter\s+summary", "lesson", r"how\s+to",
            r"getting\s+started", r"quick\s+check", r"try\s+it", "explore",
            "investigat", "activity", "activities", r"applied\s+project",
            r"discovery\s+project", r"writing\s+project", "project",
            "laboratory", r"lab\b", r"case\s+study", "animation", "video",
            r"focus\s+on", "connection", "extension", "extend",
            "diagnostic", "webassign", "cengage", "stewart", "ancillar",
            "acknowledg", r"about\s+the\s+authors", r"a\s+tribute",
            r"to\s+the\s+student", "preface", "alternate", r"what'?s\s+new",
            "features", "content", "instructor", r"test\s+bank",
            r"complete\s+solutions", r"single\s+variable", "multivariable",
            r"problems\s+plus", "appendix", r"reference\s+page", "reviewers",
            "technology", r"graphing\s+icon", r"technology\s+icon",
            r"table\s+of\s+contents", "contents", "index", "glossary",
            "bibliography", "references", "credits", r"photo\s+credits",
            "copyright", "isbn", "cataloging", r"library\s+of\s+congress",
            r"all\s+rights\s+reserved", r"printed\s+in", "edition", "mylab",
            "mastering", "mindtap", "online", "homework", "calcchat",
            "calcview", r"new\s+from", r"real[-\s]?world", "summary",
            "review", r"n\s+", "■",
        ],
        "short_title_max_chars": 20,
        "drop_single_letters": True,
        "drop_symbols_only": True,
    },
    "headings": {"level_chapter_max": 2, "level_section_max": 3},
}

# Section regexes are tried in this order.
_SECTION_ORDER = ("number_at_end", "number_at_start", "bare_number", "section_word")


@dataclass(frozen=True)
class ChunkProfile:
    min_chars: int
    max_chars: int
    overlap: int
    drop_front_matter: bool
    merge_small_chunks: bool
    auto_chapter: bool
    section_regexes: tuple[Pattern, ...]
    chapter_word: Pattern
    chapter_number_title: Pattern
    backmatter_enabled: bool
    backmatter_patterns: tuple[Pattern, ...]
    noise_patterns: tuple[Pattern, ...]
    short_title_max_chars: int
    drop_single_letters: bool
    drop_symbols_only: bool
    level_chapter_max: int
    level_section_max: int

    # --- helpers ----------------------------------------------------------

    def is_noise(self, title: str) -> bool:
        """True if the heading must stay as body text (never splits the outline)."""
        if any(p.match(title) for p in self.noise_patterns):
            return True
        if self.drop_single_letters and re.fullmatch(r"[a-z]", title, re.I):
            return True
        if self.drop_symbols_only and re.fullmatch(r"[^\w\uac00-\ud7a3]{1,3}", title):
            return True
        return False

    def match_section(self, title: str) -> tuple[str, str, str] | None:
        """Return (major, minor, rest) for a numbered-section heading, else None."""
        for rx in self.section_regexes:
            m = rx.match(title)
            if m:
                rest = (m.groupdict().get("title") or "").strip()
                return m.group("major"), m.group("minor"), rest
        return None

    def is_chapter(self, title: str) -> bool:
        """True if the heading opens a chapter (\"1 Functions\", \"CHAPTER 1\")."""
        return bool(self.chapter_word.match(title) or self.chapter_number_title.match(title))

    def chapter_major(self, text: str) -> str | None:
        """Major number of a numbered chapter title (\"2 Limits ...\" -> \"2\")."""
        m = self.chapter_number_title.match(text)
        return m.group("major") if m else None

    def is_backmatter(self, title: str) -> bool:
        """True if this heading is an answer/solution/appendix/index section."""
        if not self.backmatter_enabled:
            return False
        return any(p.match(title) for p in self.backmatter_patterns)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict:
    if yaml is None or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _compile(data: dict) -> ChunkProfile:
    ci = re.compile  # always compiled case-insensitively below
    sections = data.get("sections", {})
    chapters = data.get("chapters", {})
    back = data.get("backmatter", {})
    noise = data.get("noise", {})
    chunk = data.get("chunk", {})
    heads = data.get("headings", {})

    def rx(raw: str) -> Pattern:
        return ci(raw, re.IGNORECASE)

    return ChunkProfile(
        min_chars=int(chunk.get("min_chars", settings.chunk_min_chars)),
        max_chars=int(chunk.get("max_chars", settings.chunk_max_chars)),
        overlap=int(chunk.get("overlap", settings.chunk_overlap)),
        drop_front_matter=bool(chunk.get("drop_front_matter", True)),
        merge_small_chunks=bool(chunk.get("merge_small_chunks", True)),
        auto_chapter=bool(chunk.get("auto_chapter", True)),
        section_regexes=tuple(rx(sections[n]) for n in _SECTION_ORDER if n in sections),
        chapter_word=rx(chapters.get("word", DEFAULT_DATA["chapters"]["word"])),
        chapter_number_title=rx(chapters.get("number_title", DEFAULT_DATA["chapters"]["number_title"])),
        backmatter_enabled=bool(back.get("enabled", True)),
        backmatter_patterns=tuple(rx(p) for p in back.get("patterns", DEFAULT_DATA["backmatter"]["patterns"])),
        noise_patterns=tuple(rx(p) for p in noise.get("patterns", DEFAULT_DATA["noise"]["patterns"])),
        short_title_max_chars=int(noise.get("short_title_max_chars", 20)),
        drop_single_letters=bool(noise.get("drop_single_letters", True)),
        drop_symbols_only=bool(noise.get("drop_symbols_only", True)),
        level_chapter_max=int(heads.get("level_chapter_max", 2)),
        level_section_max=int(heads.get("level_section_max", 3)),
    )


def load_profile(book_id: str | None = None) -> ChunkProfile:
    """Load the chunking profile for a book (default + per-book override).

    Precedence: per-book YAML > default YAML > CHUNK_* env vars > built-ins.
    """
    base = _deep_merge(DEFAULT_DATA, {})
    chunk_sizes = base["chunk"]
    for key, env in (("min_chars", "CHUNK_MIN_CHARS"),
                     ("max_chars", "CHUNK_MAX_CHARS"),
                     ("overlap", "CHUNK_OVERLAP")):
        if os.environ.get(env):
            try:
                chunk_sizes[key] = int(os.environ[env])
            except ValueError:
                pass

    data = _deep_merge(base, _load_yaml(settings.chunking_dir / "chunking.yaml"))
    if book_id:
        data = _deep_merge(data, _load_yaml(settings.chunking_dir / f"chunking.{book_id}.yaml"))
    return _compile(data)
