"""Parse and update TOPICS.md (textbook register + topic -> section map)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import settings

# Matches the five-column topic table rows.
_ROW_RE = re.compile(
    r"^\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$"
)


@dataclass
class TopicRow:
    topic: str
    book: str
    section: str
    status: str
    note: str


def _is_table_row(cells: list[str]) -> bool:
    first = cells[0].strip().lower()
    if first in ("topic",):
        return False
    if set(first) <= {"-"}:
        return False
    return True


def load_topics(status: str | None = None, book: str | None = None) -> list[TopicRow]:
    rows: list[TopicRow] = []
    try:
        text = settings.topics_file.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        cells = [g.strip() for g in m.groups()]
        if not _is_table_row(cells):
            continue
        row = TopicRow(*cells)
        if status and row.status != status:
            continue
        if book and row.book != book:
            continue
        rows.append(row)
    return rows


def mark_topic(topic: str, new_status: str) -> bool:
    """Flip the status cell of one topic row in place."""
    try:
        text = settings.topics_file.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _ROW_RE.match(line)
        if not m:
            continue
        cells = [g.strip() for g in m.groups()]
        if not _is_table_row(cells):
            continue
        if cells[0] == topic:
            cells[3] = new_status
            lines[i] = "| " + " | ".join(cells) + " |"
            settings.topics_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False
