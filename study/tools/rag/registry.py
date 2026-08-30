"""SQLite registry: books, topics and pipeline docs (AGENTS.md, template).

Design:
- The DB (study/registry.db, gitignored) is the source of truth.
- TOPICS.md / AGENTS.md / templates/warmup.md are exported snapshots kept in
  git for human review and diffs.
- First use auto-imports the existing markdown files (migration).
  Afterwards edit through `python tools/study.py books|topics|docs ...`, or edit
  the markdown by hand and run `python tools/study.py import` to load it back.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import settings

log = logging.getLogger("rag")

STATUSES = ("todo", "draft", "review", "done")
KINDS = ("note", "problems")

_ROW_RE = re.compile(
    r"^\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$"
)
_ROW6_RE = re.compile(
    r"^\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*"
    r"([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$"
)
_BOOK_ROW_RE = re.compile(r"^\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$")


@dataclass
class TopicRow:
    topic: str
    book: str
    section: str
    status: str
    note: str
    kind: str = "note"


@dataclass
class BookRow:
    book_id: str
    title: str
    author: str


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(settings.registry_file))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            book_id    TEXT PRIMARY KEY,
            title      TEXT DEFAULT '',
            author     TEXT DEFAULT '',
            source_pdf TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS topics (
            topic      TEXT PRIMARY KEY,
            book       TEXT DEFAULT '',
            section    TEXT DEFAULT '',
            kind       TEXT DEFAULT 'note',
            status     TEXT DEFAULT 'todo',
            note       TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS docs (
            key        TEXT PRIMARY KEY,
            content    TEXT,
            updated_at TEXT
        );
        """
    )
    # Migration: older DBs lack the kind column.
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(topics)")]
    if "kind" not in cols:
        conn.execute("ALTER TABLE topics ADD COLUMN kind TEXT DEFAULT 'note'")
    conn.commit()
    conn.close()


_ready = False


def ensure_ready() -> None:
    """Create the schema and migrate existing markdown files on first use."""
    global _ready
    if _ready:
        return
    init_schema()
    conn = connect()
    n_topics = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    n_books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    n_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    conn.close()
    if n_topics == 0 and n_books == 0:
        log.info("Registry empty — importing TOPICS.md ...")
        import_topics_md()
    if n_docs == 0:
        log.info("Registry empty — importing AGENTS.md / template ...")
        import_docs()
    _ready = True


# --------------------------------------------------------------------------
# import: markdown -> DB
# --------------------------------------------------------------------------

def _skip_row(first_cell: str) -> bool:
    first = first_cell.strip().lower()
    if first in ("topic", "book_id"):
        return True
    return set(first) <= {"-"}


def import_topics_md() -> None:
    try:
        text = settings.topics_file.read_text(encoding="utf-8")
    except OSError:
        return
    conn = connect()
    for line in text.splitlines():
        m = _BOOK_ROW_RE.match(line)
        if m:
            cells = [g.strip() for g in m.groups()]
            if _skip_row(cells[0]):
                continue
            conn.execute(
                "INSERT INTO books(book_id, title, author, created_at, updated_at)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(book_id) DO UPDATE SET title=excluded.title,"
                " author=excluded.author, updated_at=excluded.updated_at",
                (cells[0], cells[1], cells[2], _now(), _now()),
            )
            continue
        # 6-column rows (topic/book/section/kind/status/note) or legacy 5-column.
        m = _ROW6_RE.match(line) or _ROW_RE.match(line)
        if m:
            cells = [g.strip() for g in m.groups()]
            if _skip_row(cells[0]):
                continue
            if len(cells) == 6:
                topic, book, section, kind, status, note = cells
            else:
                topic, book, section, status, note = cells
                kind = "note"
            if status not in STATUSES:
                # e.g. the "Format" example row in TOPICS.md — not a real topic
                continue
            if kind not in KINDS:
                kind = "note"
            conn.execute(
                "INSERT INTO topics(topic, book, section, kind, status, note, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(topic) DO UPDATE SET book=excluded.book,"
                " section=excluded.section, kind=excluded.kind, status=excluded.status,"
                " note=excluded.note, updated_at=excluded.updated_at",
                (topic, book, section, kind, status, note, _now(), _now()),
            )
    conn.commit()
    conn.close()


def import_docs() -> None:
    pairs = (("agents", settings.agents_file), ("template", settings.template_file))
    conn = connect()
    for key, path in pairs:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        conn.execute(
            "INSERT INTO docs(key, content, updated_at) VALUES(?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET content=excluded.content,"
            " updated_at=excluded.updated_at",
            (key, content, _now()),
        )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# export: DB -> markdown
# --------------------------------------------------------------------------

def export_topics_md() -> None:
    conn = connect()
    books = conn.execute("SELECT * FROM books ORDER BY book_id").fetchall()
    topics = conn.execute("SELECT * FROM topics ORDER BY topic").fetchall()
    conn.close()
    lines = [
        "# TOPICS — textbook register & topic -> section map",
        "",
        "> Source of truth: `registry.db`. Manage with `python tools/study.py` —",
        "> this file is an exported snapshot. Hand edits are applied with",
        "> `python tools/study.py import`.",
        "",
        "## Format",
        "",
        "| topic | book | section | kind | status | note |",
        "|---|---|---|---|---|---|",
        "| Topic name (US English) | book_id from the register below |"
        " primary textbook section, e.g. `3.5` or `3.5, 3.6` | note / problems |"
        " todo / draft / review / done | path to the note file |",
        "",
        "## Books",
        "",
        "| book_id | title | author |",
        "|---|---|---|",
    ]
    lines += [f"| {b['book_id']} | {b['title']} | {b['author']} |" for b in books]
    lines += [
        "",
        "## Topics",
        "",
        "| topic | book | section | kind | status | note |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {t['topic']} | {t['book']} | {t['section']} | {t['kind']} | {t['status']} | {t['note']} |"
        for t in topics
    ]
    settings.topics_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_docs() -> None:
    conn = connect()
    for key in ("agents", "template"):
        row = conn.execute("SELECT content FROM docs WHERE key = ?", (key,)).fetchone()
        if row is None or not row["content"]:
            continue
        path = settings.agents_file if key == "agents" else settings.template_file
        path.write_text(row["content"], encoding="utf-8")
    conn.close()


def export_all() -> None:
    ensure_ready()
    export_topics_md()
    export_docs()


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------

def add_book(book_id: str, title: str = "", author: str = "") -> None:
    ensure_ready()
    conn = connect()
    conn.execute(
        "INSERT INTO books(book_id, title, author, created_at, updated_at)"
        " VALUES(?,?,?,?,?)"
        " ON CONFLICT(book_id) DO UPDATE SET title=excluded.title,"
        " author=excluded.author, updated_at=excluded.updated_at",
        (book_id, title, author, _now(), _now()),
    )
    conn.commit()
    conn.close()
    export_topics_md()


def list_books() -> list[BookRow]:
    ensure_ready()
    conn = connect()
    rows = [
        BookRow(r["book_id"], r["title"], r["author"])
        for r in conn.execute("SELECT * FROM books ORDER BY book_id")
    ]
    conn.close()
    return rows


def add_topic(topic: str, book: str = "", section: str = "", note: str = "", kind: str = "note") -> None:
    ensure_ready()
    conn = connect()
    conn.execute(
        "INSERT INTO topics(topic, book, section, kind, status, note, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)"
        " ON CONFLICT(topic) DO UPDATE SET book=excluded.book,"
        " section=excluded.section, kind=excluded.kind, status=excluded.status,"
        " note=excluded.note, updated_at=excluded.updated_at",
        (topic, book, section, kind, "todo", note, _now(), _now()),
    )
    conn.commit()
    conn.close()
    export_topics_md()


def update_topic(topic: str, **fields: str) -> bool:
    ensure_ready()
    allowed = {"book", "section", "kind", "status", "note"}
    for key in list(fields):
        if key not in allowed:
            raise ValueError(f"unknown field: {key}")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"invalid status {fields['status']!r} (one of {STATUSES})")
    if "kind" in fields and fields["kind"] not in KINDS:
        raise ValueError(f"invalid kind {fields['kind']!r} (one of {KINDS})")
    if not fields:
        return False
    conn = connect()
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [_now(), topic]
    cur = conn.execute(f"UPDATE topics SET {sets}, updated_at = ? WHERE topic = ?", params)
    conn.commit()
    conn.close()
    if cur.rowcount:
        export_topics_md()
    return cur.rowcount > 0


def list_topics(status: str | None = None, book: str | None = None) -> list[TopicRow]:
    ensure_ready()
    sql = "SELECT * FROM topics"
    conds: list[str] = []
    params: list[str] = []
    if status:
        conds.append("status = ?")
        params.append(status)
    if book:
        conds.append("book = ?")
        params.append(book)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY topic"
    conn = connect()
    rows = [
        TopicRow(r["topic"], r["book"], r["section"], r["status"], r["note"], r["kind"])
        for r in conn.execute(sql, params)
    ]
    conn.close()
    return rows


def get_doc(key: str) -> str | None:
    ensure_ready()
    conn = connect()
    row = conn.execute("SELECT content FROM docs WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["content"] if row else None


def set_doc(key: str, content: str) -> None:
    ensure_ready()
    conn = connect()
    conn.execute(
        "INSERT INTO docs(key, content, updated_at) VALUES(?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET content=excluded.content,"
        " updated_at=excluded.updated_at",
        (key, content, _now()),
    )
    conn.commit()
    conn.close()
    export_docs()


def set_doc_from_file(key: str, path: Path) -> None:
    set_doc(key, path.read_text(encoding="utf-8"))
