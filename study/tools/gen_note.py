#!/usr/bin/env python3
"""Generate a single study note with RAG grounding (see study/README.md).

Examples:
    python tools/gen_note.py "Normal distribution" --book prob --section 3.5
    python tools/gen_note.py "Central limit theorem" --book prob --section 3.7 --effort high
"""
from __future__ import annotations

import argparse
import logging
import sys

from rag import generate
from rag.config import settings
from rag.retrieve import primary_sections, retrieve
from rag.store import Store
from rag.topics import load_topics, mark_topic

log = logging.getLogger("rag")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("topic", help="topic name, as listed in TOPICS.md")
    ap.add_argument("--book", default=None, help="book_id (defaults to TOPICS.md row)")
    ap.add_argument("--section", default=None, help="primary section(s), e.g. '3.5' or '3.5, 3.6'")
    ap.add_argument("--effort", default=None, help="override REASONING_EFFORT (low/medium/high)")
    ap.add_argument("--out", default=None, help="output path relative to study/")
    ap.add_argument("--crossref", type=int, default=None, help="number of cross-reference chunks")
    ap.add_argument("--problems", action="store_true",
                    help="generate a 10+10 problem set with a separate solutions file")
    ap.add_argument("--update-topics", action="store_true", help="set the row status to draft")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings.ensure_dirs()
    if args.effort:
        settings.reasoning_effort = args.effort

    # Prefer the TOPICS.md row, allow CLI overrides.
    row = next((r for r in load_topics() if r.topic == args.topic), None)
    book = args.book or (row.book if row else None)
    section = args.section or (row.section if row else "") or ""
    out = args.out or (row.note if row and row.note else None)

    store = Store(settings.index_dir / "rag.db")
    refs = [s.strip() for s in section.split(",") if s.strip()] if section else []
    primary = primary_sections(store, args.topic, refs, book)
    cross = retrieve(store, args.topic, book_id=book, k=args.crossref)
    ids = {h.chunk_id for h in primary}
    hits = primary + [h for h in cross if h.chunk_id not in ids]

    if not hits:
        print("No matching textbook chunks found. Is the book indexed (see README)?")
        sys.exit(1)

    title = store.book_title(book) if book else "(all books)"
    if args.problems:
        messages = generate.build_problem_messages(args.topic, title, book or "-", section, hits)
        print(f"Retrieved {len(hits)} chunks. Generating problem set (call 1/2: problems) ...")
        problems_text = generate.call_llama(messages)
        messages = generate.build_solution_messages(
            args.topic, title, book or "-", section, hits, problems_text
        )
        print("Generating solutions (call 2/2) ...")
        solutions_text = generate.call_llama(messages)
        p_out, s_out = generate.save_problem_set(
            args.topic, book or "-", section, problems_text, solutions_text, out_base=out
        )
        if args.update_topics and row:
            mark_topic(args.topic, "draft")
        print(f"Saved: {p_out}\nSaved: {s_out}")
        return

    messages = generate.build_messages(args.topic, title, book or "-", section, hits)
    print(
        f"Retrieved {len(hits)} chunks ({len(primary)} primary + {len(cross)} cross-reference).\n"
        f"Calling llama-server (effort={settings.reasoning_effort}, max_tokens={settings.max_tokens}) ..."
    )
    content = generate.call_llama(messages)
    path = generate.save_note(args.topic, book or "-", section, content, out_path=out)
    if args.update_topics and row:
        mark_topic(args.topic, "draft")
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
