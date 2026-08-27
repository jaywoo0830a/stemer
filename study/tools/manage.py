#!/usr/bin/env python3
"""Manage books, topics and pipeline docs in the registry DB.

The registry (study/registry.db) is the source of truth.
TOPICS.md / AGENTS.md / templates/warmup.md are exported snapshots — every
mutation re-exports the markdown files automatically.

Examples:
    python tools/manage.py init
    python tools/manage.py import                     # load markdown -> DB
    python tools/manage.py export                     # write DB -> markdown
    python tools/manage.py books add prob --title "Introduction to Probability" --author "Blitzstein"
    python tools/manage.py books list
    python tools/manage.py topics add "Normal distribution" --book prob --section 3.5
    python tools/manage.py topics list --status todo
    python tools/manage.py topics set "Normal distribution" --status done
    python tools/manage.py docs set agents --file AGENTS.md
    python tools/manage.py docs get agents
"""
from __future__ import annotations

import argparse
import logging
import sys

from rag import registry
from rag.config import settings


def _print_topics(rows) -> None:
    print(f"{'topic':<32} {'book':<10} {'section':<12} {'status':<8} note")
    print("-" * 80)
    for r in rows:
        print(f"{r.topic:<32} {r.book:<10} {r.section:<12} {r.status:<8} {r.note}")


def _print_books(rows) -> None:
    print(f"{'book_id':<12} {'title':<48} author")
    print("-" * 80)
    for r in rows:
        print(f"{r.book_id:<12} {r.title:<48} {r.author}")


def _counts() -> str:
    n_topics = len(registry.list_topics())
    n_books = len(registry.list_books())
    return f"{n_books} book(s), {n_topics} topic(s)"


def cmd_init(_args) -> None:
    registry.ensure_ready()
    print(f"Registry ready at {settings.registry_file} — {_counts()}")


def cmd_import(_args) -> None:
    registry.init_schema()
    registry.import_topics_md()
    registry.import_docs()
    print(f"Imported markdown files into {settings.registry_file} — {_counts()}")


def cmd_export(_args) -> None:
    registry.export_all()
    print("Exported registry to TOPICS.md / AGENTS.md / templates/warmup.md")


def cmd_books(args) -> None:
    if args.sub == "add":
        registry.add_book(args.book_id, title=args.title, author=args.author)
        print(f"Book '{args.book_id}' saved (TOPICS.md re-exported).")
    elif args.sub == "list":
        _print_books(registry.list_books())


def cmd_topics(args) -> None:
    if args.sub == "add":
        registry.add_topic(args.topic, book=args.book, section=args.section, note=args.note)
        print(f"Topic '{args.topic}' added as todo (TOPICS.md re-exported).")
    elif args.sub == "list":
        _print_topics(registry.list_topics(status=args.status, book=args.book))
    elif args.sub == "set":
        fields = {}
        for key in ("status", "book", "section", "note"):
            value = getattr(args, key)
            if value is not None:
                fields[key] = value
        if not fields:
            print("Nothing to set (use --status / --book / --section / --note).")
            return
        if registry.update_topic(args.topic, **fields):
            print(f"Topic '{args.topic}' updated (TOPICS.md re-exported).")
        else:
            print(f"Topic '{args.topic}' not found.")


def cmd_docs(args) -> None:
    if args.sub == "set":
        registry.set_doc_from_file(args.key, args.file)
        print(f"Doc '{args.key}' saved from {args.file} (markdown re-exported).")
    elif args.sub == "get":
        content = registry.get_doc(args.key)
        if content is None:
            print(f"Doc '{args.key}' not found.")
        else:
            print(content)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create schema + import markdown if the DB is empty")
    sub.add_parser("import", help="load TOPICS.md / AGENTS.md / template into the DB (overwrites)")
    sub.add_parser("export", help="rewrite markdown files from the DB")

    pb = sub.add_parser("books", help="manage the book register")
    bsub = pb.add_subparsers(dest="sub", required=True)
    ba = bsub.add_parser("add", help="add or update a book")
    ba.add_argument("book_id")
    ba.add_argument("--title", default="")
    ba.add_argument("--author", default="")
    bsub.add_parser("list", help="list books")

    pt = sub.add_parser("topics", help="manage topics")
    tsub = pt.add_subparsers(dest="sub", required=True)
    ta = tsub.add_parser("add", help="add a topic (status=todo)")
    ta.add_argument("topic")
    ta.add_argument("--book", default="")
    ta.add_argument("--section", default="")
    ta.add_argument("--note", default="")
    tl = tsub.add_parser("list", help="list topics")
    tl.add_argument("--status", default=None, choices=registry.STATUSES)
    tl.add_argument("--book", default=None)
    ts = tsub.add_parser("set", help="update fields of one topic")
    ts.add_argument("topic")
    ts.add_argument("--status", default=None, choices=registry.STATUSES)
    ts.add_argument("--book", default=None)
    ts.add_argument("--section", default=None)
    ts.add_argument("--note", default=None)

    pd = sub.add_parser("docs", help="manage prompt docs (agents, template)")
    dsub = pd.add_subparsers(dest="sub", required=True)
    ds = dsub.add_parser("set", help="store a doc from a file")
    ds.add_argument("key", choices=("agents", "template"))
    ds.add_argument("--file", required=True)
    dg = dsub.add_parser("get", help="print a doc")
    dg.add_argument("key", choices=("agents", "template"))

    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings.ensure_dirs()

    dispatch = {
        "init": cmd_init,
        "import": cmd_import,
        "export": cmd_export,
        "books": cmd_books,
        "topics": cmd_topics,
        "docs": cmd_docs,
    }
    try:
        dispatch[args.cmd](args)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
