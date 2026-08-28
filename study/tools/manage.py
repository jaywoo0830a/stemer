#!/usr/bin/env python3
"""Manage books, topics and pipeline docs in the registry DB.

The registry (study/registry.db) is the source of truth.
TOPICS.md / AGENTS.md / templates/warmup.md are exported snapshots — every
mutation re-exports the markdown files automatically.

Examples:
    python tools/manage.py init
    python tools/manage.py status                  # registry + index + progress
    python tools/manage.py reindex-all             # apply new chunking rules to every book
    python tools/manage.py import                  # load markdown -> DB
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


def cmd_status(_args) -> None:
    """Show registry DB + search index + pipeline progress in one view."""
    from rag.store import Store

    reg_books = registry.list_books()
    reg_topics = registry.list_topics()
    counts = {s: sum(1 for t in reg_topics if t.status == s) for s in registry.STATUSES}

    print("=== registry.db (books / topics) ===")
    print(f"books: {len(reg_books)}")
    print("topics:", ", ".join(f"{s}={counts[s]}" for s in registry.STATUSES))

    print()
    print("=== index (rag.db + chroma) ===")
    idx_path = settings.index_dir / "rag.db"
    if idx_path.exists():
        store = Store(idx_path)
        st = store.stats()
        print(f"books: {len(st['books'])} | chunks: {st['chunks']}")
        for row in st["per_book"]:
            print(f"  {row['book_id']}: {row['chunks']} chunks")
    else:
        print("아직 없음 — PDF가 인덱싱되지 않았습니다.")

    try:
        from rag import embed_index

        print(f"vectors: {embed_index.get_collection().count()}")
    except Exception:
        print("vectors: (chromadb 미설치 환경 — 도커 컨테이너에서 확인하세요)")

    inbox = sorted(settings.books_inbox.glob("*.pdf"))
    notes = sorted(settings.notes_dir.glob("*.md")) if settings.notes_dir.exists() else []
    print()
    print("=== 진행 ===")
    print(f"inbox 대기 PDF: {len(inbox)}" + ("  " + ", ".join(p.name for p in inbox) if inbox else ""))
    print(f"notes: {len(notes)}")


def _reindex_one(store, book_id: str) -> int | None:
    """Re-chunk one book from cached markdown and rebuild its index.

    Returns the new chunk count, or None if the book is unknown.
    """
    from rag import chunk, embed_index

    row = store.conn.execute(
        "SELECT title, source_pdf FROM books WHERE book_id = ?", (book_id,)
    ).fetchone()
    if row is None:
        return None
    md_path = settings.books_markdown / f"{row['title']}.md"
    if not md_path.exists():
        print(f"  markdown cache not found: {md_path}")
        return None

    md = md_path.read_text(encoding="utf-8")
    chunks = chunk.split_markdown(md, book_id)
    store.delete_book(book_id)
    store.add_book(book_id, row["title"], row["source_pdf"] or "")
    store.add_chunks(chunks)
    embed_index.index_book(book_id, chunks, force=True)
    return len(chunks)


def cmd_reindex(args) -> None:
    """Re-chunk one book from its cached markdown (no PDF re-parsing)."""
    from rag.store import Store

    store = Store(settings.index_dir / "rag.db")
    n = _reindex_one(store, args.book_id)
    if n is None:
        print(f"book_id '{args.book_id}' not found in the index (indexed yet?)")
    else:
        print(f"Reindexed '{args.book_id}': {n} chunks.")


def cmd_reindex_all(_args) -> None:
    """Reindex every indexed book — use after chunking rules change.

    No PDF re-parsing (uses books/markdown/ cache); re-embedding is the
    slow part, so expect roughly 1 hour per book on this CPU.
    """
    from rag.store import Store

    store = Store(settings.index_dir / "rag.db")
    rows = store.conn.execute(
        "SELECT book_id, title FROM books ORDER BY book_id"
    ).fetchall()
    if not rows:
        print("No indexed books yet.")
        return
    for r in rows:
        print(f"Reindexing '{r['book_id']}' ({r['title']}) ...")
        n = _reindex_one(store, r["book_id"])
        print(f"  -> {n} chunks" if n is not None else "  -> skipped")
    print("Done. Run 'manage.py status' to verify.")


def cmd_reset_all(args) -> None:
    """Reset the whole study pipeline to a fresh-install state.

    Deletes: registry tables, search index (rag.db + chroma), uploaded PDFs
    (inbox/processed), markdown cache, generated notes and logs.
    Keeps: AGENTS.md / templates/warmup.md files (re-imported into the DB on
    first use), the HuggingFace model cache.
    """
    import shutil

    if not args.yes:
        print("This deletes EVERYTHING except AGENTS.md/template/model cache:")
        print("  - registry.db tables (books/topics/docs)")
        print("  - index/ (rag.db + chroma)")
        print("  - books/inbox/*.pdf, books/processed/*.pdf, books/markdown/*.md")
        print("  - notes/*.md")
        print("  - logs/*.log")
        print("Tip: stop the workers first: docker compose stop pipeline api")
        print("Run again with --yes to confirm.")
        return

    # 1) registry: drop tables via SQL (safer than unlinking the file while
    #    another container may hold a connection to it)
    conn = registry.connect()
    conn.executescript("DROP TABLE IF EXISTS books; DROP TABLE IF EXISTS topics; DROP TABLE IF EXISTS docs;")
    conn.commit()
    conn.close()
    registry.init_schema()
    # Write the skeleton snapshot DIRECTLY — export_all() would call
    # ensure_ready() and re-import the old TOPICS.md into the empty DB.
    registry.export_topics_md()

    # 2) everything else: delete and recreate empty dirs
    for p in (settings.index_dir, settings.books_inbox, settings.books_processed,
              settings.books_markdown, settings.notes_dir, settings.logs_dir):
        if p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    settings.ensure_dirs()

    print("Reset complete — fresh-install state.")
    print("Kept: AGENTS.md, templates/warmup.md, HuggingFace model cache.")
    print("Next: upload PDFs again (or run 'manage.py status').")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create schema + import markdown if the DB is empty")
    sub.add_parser("import", help="load TOPICS.md / AGENTS.md / template into the DB (overwrites)")
    sub.add_parser("export", help="rewrite markdown files from the DB")
    sub.add_parser("status", help="show registry + index + notes state")
    ri = sub.add_parser("reindex", help="re-chunk one book from cached markdown")
    ri.add_argument("book_id", help="index book_id (see: manage.py status)")
    sub.add_parser("reindex-all", help="reindex every indexed book (after chunking rules change)")
    ra = sub.add_parser("reset-all", help="reset DB + index + books + notes to a clean state")
    ra.add_argument("--yes", action="store_true", help="confirm the destructive reset")

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
        "status": cmd_status,
        "reindex": cmd_reindex,
        "reindex-all": cmd_reindex_all,
        "reset-all": cmd_reset_all,
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
