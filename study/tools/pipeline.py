#!/usr/bin/env python3
"""Overnight pipeline: inbox PDFs -> Docling -> chunks -> hybrid index -> notes.

Usage:
    python tools/pipeline.py --once              # one pass, then exit
    python tools/pipeline.py --watch             # poll the inbox forever (Docker default)
    python tools/pipeline.py --prefetch          # download embedding/rerank models, exit
    python tools/pipeline.py --once --index-only     # only ingest + index
    python tools/pipeline.py --once --generate-only  # only generate pending notes

Notes resume automatically: topics already marked `draft` (or beyond) in
TOPICS.md are skipped, so re-runs continue where they stopped.
"""
from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
import time
from pathlib import Path

import httpx

from rag import chunk as chunking
from rag import embed_index
from rag import generate
from rag.config import settings
from rag.parse import parse_pdf
from rag.retrieve import primary_sections, retrieve
from rag.store import Store
from rag.topics import load_topics, mark_topic

log = logging.getLogger("rag")

# Topics that failed in this process run: do not retry them every watch pass.
_failed_topics: set[str] = set()


def _setup_logging() -> None:
    settings.ensure_dirs()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    handlers.append(logging.FileHandler(settings.logs_dir / "pipeline.log", encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # docling/chromadb are chatty; keep them at WARNING
    for noisy in ("docling", "chromadb", "sentence_transformers", "httpx", "tqdm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _slug(pdf_path: Path) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", pdf_path.stem.lower()).strip("-")
    return s or pdf_path.stem


def ingest_pdfs(store: Store, force: bool = False) -> int:
    """Parse and index every PDF in the inbox, then move it to processed/.

    Returns the number of successfully indexed books (not just found).
    """
    pdfs = sorted(settings.books_inbox.glob("*.pdf"))
    if not pdfs:
        return 0
    indexed = 0
    for pdf in pdfs:
        book_id = _slug(pdf)
        log.info("Ingesting %s (book_id=%s) ...", pdf.name, book_id)
        try:
            md = parse_pdf(pdf, force=force)  # cached markdown in books/markdown/
            chunks = chunking.split_markdown(md, book_id)
            if not chunks:
                log.warning("No chunks produced for %s — skipping.", pdf.name)
                continue
            store.delete_book(book_id)
            store.add_book(book_id, pdf.stem, pdf.name)
            store.add_chunks(chunks)
            embed_index.index_book(book_id, chunks)
            shutil.move(str(pdf), str(settings.books_processed / pdf.name))
            indexed += 1
            log.info("Indexed %s: %d chunks.", book_id, len(chunks))
        except Exception:
            log.exception("Failed to ingest %s — leaving it in inbox.", pdf.name)
    return indexed


def llama_healthy() -> bool:
    try:
        resp = httpx.get(settings.llama_base_url.rstrip("/") + "/health", timeout=10.0)
        return resp.status_code == 200
    except Exception:
        return False


def generate_pending(store: Store, only_book: str | None = None, max_topics: int | None = None) -> int:
    if not llama_healthy():
        log.error(
            "llama-server is not reachable at %s — skipping note generation."
            " (Indexing is unaffected.)",
            settings.llama_base_url,
        )
        return 0
    rows = load_topics(status="todo")
    if only_book:
        rows = [r for r in rows if r.book == only_book]
    if max_topics:
        rows = rows[:max_topics]

    done = 0
    for row in rows:
        if row.topic in _failed_topics:
            log.info("Skipping previously failed topic '%s'.", row.topic)
            continue
        log.info("Generating note for '%s' (book=%s, section=%s) ...", row.topic, row.book, row.section)
        try:
            refs = [s.strip() for s in re.split(r"[,;]", row.section) if s.strip()]
            primary = primary_sections(store, row.topic, refs, row.book)
            cross = retrieve(store, row.topic, book_id=row.book)
            ids = {h.chunk_id for h in primary}
            hits = primary + [h for h in cross if h.chunk_id not in ids]
            if not hits:
                log.warning("No retrieved chunks for '%s' — is the book indexed?", row.topic)
                _failed_topics.add(row.topic)
                continue
            title = store.book_title(row.book) or row.book
            messages = generate.build_messages(row.topic, title, row.book, row.section, hits)
            content = generate.call_llama(messages)
            out = generate.save_note(
                row.topic, row.book, row.section, content, out_path=row.note or None
            )
            mark_topic(row.topic, "draft")
            log.info("Saved %s; TOPICS.md status -> draft.", out)
            done += 1
        except Exception:
            log.exception("Failed for topic '%s' — continuing with the next one.", row.topic)
            _failed_topics.add(row.topic)
    return done


def prefetch_models() -> None:
    log.info("Prefetching embedding model %s ...", settings.embed_model)
    embed_index.get_embed_model()
    log.info("Prefetching reranker %s ...", settings.rerank_model)
    from rag.retrieve import get_reranker

    get_reranker()
    log.info("Models ready.")


def run_once(
    do_index: bool = True,
    do_generate: bool = True,
    only_book: str | None = None,
    max_topics: int | None = None,
    force: bool = False,
) -> None:
    store = Store(settings.index_dir / "rag.db")
    n = ingest_pdfs(store, force) if do_index else 0
    g = generate_pending(store, only_book=only_book, max_topics=max_topics) if do_generate else 0
    log.info("Pass finished: %d PDF(s) ingested, %d note(s) generated.", n, g)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--once", action="store_true", help="run one pass and exit")
    ap.add_argument("--watch", action="store_true", help="poll the inbox forever")
    ap.add_argument("--prefetch", action="store_true", help="download models and exit")
    ap.add_argument("--index-only", action="store_true", help="skip note generation")
    ap.add_argument("--generate-only", action="store_true", help="skip PDF ingestion")
    ap.add_argument("--book", default=None, help="restrict to one book_id")
    ap.add_argument("--max-topics", type=int, default=None, help="cap topics per pass")
    ap.add_argument("--force", action="store_true", help="re-parse PDFs even if markdown cached")
    args = ap.parse_args()

    _setup_logging()
    if args.prefetch:
        prefetch_models()
        return

    if args.watch:
        log.info(
            "Watcher started. Watching %s every %ds (Ctrl-C to stop) ...",
            settings.books_inbox, settings.watch_interval_s,
        )
        while True:
            try:
                run_once(
                    do_index=True,
                    do_generate=True,
                    only_book=args.book,
                    max_topics=args.max_topics,
                    force=args.force,
                )
            except Exception:
                log.exception("Watch pass failed — will retry on the next interval.")
            time.sleep(settings.watch_interval_s)

    run_once(
        do_index=not args.generate_only,
        do_generate=not args.index_only,
        only_book=args.book,
        max_topics=args.max_topics,
        force=args.force,
    )


if __name__ == "__main__":
    main()
