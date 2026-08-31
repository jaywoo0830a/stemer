#!/usr/bin/env python3
"""Unified CLI for the study RAG pipeline (single entry point).

Replaces the scattered tools/pipeline.py, tools/manage.py and tools/gen_note.py
with one coherent command surface. Run it inside the pipeline container
(`docker compose run --rm pipeline python -u tools/study.py <command>`) or, for
the LLM-managed phases, through the host orchestrator `study/pipeline.sh`
(which starts/stops llama-server around `generate`).

Commands:

  pipeline phases (LLM off unless noted):
    index        Phase A+B: parse inbox PDFs -> chunk -> hybrid index
    generate     Phase C:  retrieve -> rerank -> llama-server -> notes (LLM on)
    all          index then generate (once)
    prefetch     download embedding/rerank models and exit
    note         generate ONE topic immediately (LLM must be up)

  registry & state:
    init         create schema + import markdown files if the DB is empty
    import       load TOPICS.md / AGENTS.md / template into the DB
    export       rewrite markdown files from the DB
    status       registry + index + pending + notes in one view
    reset-all    drop DB tables, index, PDFs, notes, logs [--yes] [--keep-pdfs]

  books / topics / docs:
    books add <id> --title --author | books list
    topics add|list|set
    docs set|get

  reindex:
    reindex <book_id> | reindex --all   re-chunk from cached markdown (no PDF re-parse)

Examples:
    python tools/study.py init
    python tools/study.py status
    python tools/study.py books add prob --title "Introduction to Probability"
    python tools/study.py topics add "Normal distribution" --book prob --section 3.5
    python tools/study.py note "Normal distribution" --book prob --section 3.5
    python tools/study.py index --force
    python tools/study.py generate --max-topics 5
"""
from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from rag import chunk as chunking
from rag import embed_index
from rag import generate
from rag import llm
from rag.config import settings
from rag.parse import parse_pdf
from rag.retrieve import primary_sections, retrieve
from rag.store import Store, open_store
from rag.topics import load_topics, mark_topic

log = logging.getLogger("rag")

# Topics that failed in this process run: do not retry them on a later pass.
_failed_topics: set[str] = set()


def setup_logging() -> None:
    settings.ensure_dirs()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    handlers.append(logging.FileHandler(settings.logs_dir / "pipeline.log", encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    for noisy in ("docling", "chromadb", "sentence_transformers", "httpx", "tqdm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _slug(pdf_path: Path) -> str:
    # Keep Hangul so Korean filenames don't collapse (물리학 파트 2 -> 물리학-파트-2).
    s = re.sub(r"[^a-z0-9\uac00-\ud7a3]+", "-", pdf_path.stem.lower()).strip("-")
    return s or pdf_path.stem


# ---------------------------------------------------------------------------
# shared reads
# ---------------------------------------------------------------------------

def count_pending() -> tuple[int, int]:
    """Return (inbox PDFs left to index, todo topics left to generate)."""
    inbox = len(list(settings.books_inbox.glob("*.pdf")))
    topics = len(load_topics(status="todo"))
    return inbox, topics


def _print_topics(rows) -> None:
    print(f"{'topic':<32} {'book':<10} {'section':<12} {'kind':<9} {'status':<8} note")
    print("-" * 84)
    for r in rows:
        print(f"{r.topic:<32} {r.book:<10} {r.section:<12} {r.kind:<9} {r.status:<8} {r.note}")


def _print_books(rows) -> None:
    print(f"{'book_id':<12} {'title':<48} author")
    print("-" * 80)
    for r in rows:
        print(f"{r.book_id:<12} {r.title:<48} {r.author}")


# ---------------------------------------------------------------------------
# registry: init / import / export / status / reset
# ---------------------------------------------------------------------------

def cmd_init(_args) -> int:
    from rag import registry

    registry.ensure_ready()
    n_books = len(registry.list_books())
    n_topics = len(registry.list_topics())
    print(f"Registry ready at {settings.registry_file} — {n_books} book(s), {n_topics} topic(s)")
    return 0


def cmd_import(_args) -> int:
    from rag import registry

    registry.init_schema()
    registry.import_topics_md()
    registry.import_docs()
    n_books = len(registry.list_books())
    n_topics = len(registry.list_topics())
    print(f"Imported markdown files into {settings.registry_file} — {n_books} book(s), {n_topics} topic(s)")
    return 0


def cmd_export(_args) -> int:
    from rag import registry

    registry.export_all()
    print("Exported registry to TOPICS.md / AGENTS.md / templates/warmup.md")
    return 0


def cmd_status(_args) -> int:
    """One-view status: registry + search index + pending + notes.

    Ends with a parseable `pending_index=N pending_generate=M` line so
    study/pipeline.sh can decide whether to enter the generate phase.
    """
    from rag import registry

    reg_books = registry.list_books()
    reg_topics = registry.list_topics()
    counts = {s: sum(1 for t in reg_topics if t.status == s) for s in registry.STATUSES}

    print("=== registry.db (books / topics) ===")
    print(f"books: {len(reg_books)}")
    print("topics:", ", ".join(f"{s}={counts[s]}" for s in registry.STATUSES))

    print()
    print("=== index (DB) ===")
    if settings.use_pg:
        try:
            st = open_store().stats()
            print(f"postgres | books: {len(st['books'])} | chunks: {st['chunks']}")
            for row in st["per_book"]:
                print(f"  {row['book_id']}: {row['chunks']} chunks")
        except Exception as exc:
            print(f"(postgres unavailable: {exc} — db 서비스 기동 필요: ./worker-up.sh)")
    else:
        idx_path = settings.index_dir / "rag.db"
        if idx_path.exists():
            st = Store(idx_path).stats()
            print(f"sqlite   | books: {len(st['books'])} | chunks: {st['chunks']}")
            for row in st["per_book"]:
                print(f"  {row['book_id']}: {row['chunks']} chunks")
        else:
            print("(empty) — run `study.py index` after dropping PDFs into books/inbox/")
        try:
            print(f"vectors (chroma): {embed_index.get_collection().count()}")
        except Exception:
            print("vectors (chroma): unavailable in this environment")

    inbox = sorted(settings.books_inbox.glob("*.pdf"))
    notes = sorted(settings.notes_dir.glob("*.md")) if settings.notes_dir.exists() else []
    problems = sorted(settings.problems_dir.glob("*.md")) if settings.problems_dir.exists() else []
    print()
    print("=== progress ===")
    print(f"inbox PDFs: {len(inbox)}" + ("  " + ", ".join(p.name for p in inbox) if inbox else ""))
    print(f"notes: {len(notes)} | problems: {len(problems)}")

    pending_index, pending_generate = count_pending()
    print(f"pending_index={pending_index} pending_generate={pending_generate}")
    return 0


def cmd_reset_all(args) -> int:
    """Drop DB tables, index, PDFs, notes and logs — back to a fresh install.

    With --keep-pdfs the uploaded PDFs survive: processed/ ones are moved back
    to inbox/ so a subsequent `study.py index` re-parses and re-indexes every
    book from scratch.
    """
    from rag import registry

    if not args.yes:
        print("This deletes EVERYTHING except AGENTS.md/template/model cache:")
        print("  - registry.db tables (books/topics/docs)")
        print("  - index/ (rag.db + chroma), books/markdown/ (parse cache), books/figures/")
        if args.keep_pdfs:
            print("  - notes/*.md, problems/*.md")
            print("  - logs/*.log")
            print("KEPT: books/inbox/*.pdf + books/processed/*.pdf (moved back to inbox).")
        else:
            print("  - books/inbox/*.pdf, books/processed/*.pdf")
            print("  - notes/*.md, problems/*.md")
            print("  - logs/*.log")
        print("Run again with --yes to confirm.")
        return 1

    # registry: drop tables via SQL (safer than unlinking the file while
    # another container may hold a connection to it)
    conn = registry.connect()
    conn.executescript("DROP TABLE IF EXISTS books; DROP TABLE IF EXISTS topics; DROP TABLE IF EXISTS docs;")
    conn.commit()
    conn.close()
    registry.init_schema()
    # Write the skeleton snapshot DIRECTLY — export_all() would call
    # ensure_ready() and re-import the old TOPICS.md into the empty DB.
    registry.export_topics_md()

    # Wipe derived state: parse cache, index, figures, notes, problems, logs.
    for p in (settings.index_dir, settings.books_markdown, settings.figures_dir,
              settings.notes_dir, settings.problems_dir, settings.logs_dir):
        if p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()

    if args.keep_pdfs:
        # Keep the uploaded PDFs: return processed/ ones to the inbox so a
        # fresh `study.py index` picks every book up again.
        settings.books_processed.mkdir(parents=True, exist_ok=True)
        moved = sorted(settings.books_processed.glob("*.pdf"))
        for pdf in moved:
            shutil.move(str(pdf), str(settings.books_inbox / pdf.name))
        print(f"Kept {len(moved)} processed PDF(s) — moved back to inbox/ for re-indexing.")
    else:
        for p in (settings.books_inbox, settings.books_processed):
            if p.exists():
                shutil.rmtree(p) if p.is_dir() else p.unlink()

    settings.ensure_dirs()

    print("Reset complete — fresh-install state.")
    kept = "AGENTS.md, templates/warmup.md, HuggingFace model cache"
    if args.keep_pdfs:
        kept += ", uploaded PDFs (books/inbox/)"
    print(f"Kept: {kept}.")
    print("Next: run `study.py index` (or `bash study/pipeline.sh index`) — the watcher will pick the inbox up.")
    return 0


# ---------------------------------------------------------------------------
# books / topics / docs
# ---------------------------------------------------------------------------

def cmd_books(args) -> int:
    from rag import registry

    if args.sub == "add":
        registry.add_book(args.book_id, title=args.title, author=args.author)
        print(f"Book '{args.book_id}' saved (TOPICS.md re-exported).")
    elif args.sub == "list":
        _print_books(registry.list_books())
    return 0


def cmd_topics(args) -> int:
    from rag import registry

    if args.sub == "add":
        registry.add_topic(
            args.topic, book=args.book, section=args.section, note=args.note, kind=args.kind
        )
        print(f"Topic '{args.topic}' added as {args.kind}/todo (TOPICS.md re-exported).")
    elif args.sub == "list":
        _print_topics(registry.list_topics(status=args.status, book=args.book))
    elif args.sub == "set":
        fields = {}
        for key in ("status", "kind", "book", "section", "note"):
            value = getattr(args, key, None)
            if value is not None:
                fields[key] = value
        if not fields:
            print("Nothing to set (use --status / --kind / --book / --section / --note).")
            return 1
        if registry.update_topic(args.topic, **fields):
            print(f"Topic '{args.topic}' updated (TOPICS.md re-exported).")
        else:
            print(f"Topic '{args.topic}' not found.")
            return 1
    return 0


def cmd_docs(args) -> int:
    from rag import registry

    if args.sub == "set":
        registry.set_doc_from_file(args.key, args.file)
        print(f"Doc '{args.key}' saved from {args.file} (markdown re-exported).")
    elif args.sub == "get":
        content = registry.get_doc(args.key)
        if content is None:
            print(f"Doc '{args.key}' not found.")
            return 1
        print(content)
    return 0


# ---------------------------------------------------------------------------
# Phase A+B: index (LLM OFF — enforced by the host scheduler)
# ---------------------------------------------------------------------------

def _parse_and_chunk(pdf: Path, force: bool = False) -> tuple[str, Path, list] | None:
    """Parse + chunk + figure-enrich one PDF.

    This is the CPU-heavy stage (Docling + OCR + formula VLM) and is safe to
    run in a worker process for `index --jobs N`. Returns (book_id, pdf, chunks)
    or None on failure (PDF stays in inbox).
    """
    book_id = _slug(pdf)
    try:
        log.info("Ingesting %s (book_id=%s) ...", pdf.name, book_id)
        md = parse_pdf(pdf, force=force)  # cached markdown in books/markdown/
        chunks = chunking.split_markdown(md, book_id)
        if settings.figures_enabled.lower() != "off":
            from rag import figures

            # Custom local-VLM figure descriptions are skipped in native mode —
            # docling already wrote descriptions.json during parse.
            if not settings.native_picture_description:
                figures.caption_figures(pdf.stem)
            attached = figures.attach_descriptions(chunks, pdf.stem)
            if attached:
                log.info("Attached %d figure description(s) for %s.", attached, book_id)
        return book_id, pdf, chunks
    except Exception:
        log.exception("Failed to parse %s — leaving it in inbox.", pdf.name)
        return None


def _store_and_embed(store, book_id: str, pdf: Path, chunks: list, force: bool) -> bool:
    """Write chunks + build vectors + move the PDF to processed/.

    Single-writer stage — runs in the parent process only (SQLite/Postgres and
    Chroma/pgvector are not fork-safe to share across workers).
    """
    if not chunks:
        log.warning("No chunks produced for %s — skipping.", pdf.name)
        return False
    store.delete_book(book_id)
    store.add_book(book_id, pdf.stem, pdf.name)
    store.add_chunks(chunks)
    embed_index.index_book(book_id, chunks, force=force)
    shutil.move(str(pdf), str(settings.books_processed / pdf.name))
    log.info("Indexed %s: %d chunks.", book_id, len(chunks))
    return True


def index_one_book(store, pdf: Path, force: bool = False) -> bool:
    """Parse + chunk + enrich + index a single PDF. Returns success."""
    r = _parse_and_chunk(pdf, force=force)
    if r is None:
        return False
    return _store_and_embed(store, r[0], r[1], r[2], force)


def cmd_index(args) -> int:
    jobs = max(1, args.jobs or 1)
    pdfs = [
        p
        for p in sorted(settings.books_inbox.glob("*.pdf"))
        if not args.book or args.book == _slug(p)
    ]
    indexed = 0

    if jobs <= 1:
        store = open_store()
        for pdf in pdfs:
            try:
                if index_one_book(store, pdf, force=args.force):
                    indexed += 1
            except Exception:
                log.exception("Failed to ingest %s — leaving it in inbox.", pdf.name)
    else:
        # Multi-core: parse N PDFs concurrently (each worker loads its own
        # docling models, ~4-6GB RAM each — so keep N low on 64GB). The store
        # is opened AFTER the pool exits to avoid fork()+SQLite/pg pitfalls.
        import multiprocessing as mp
        from functools import partial

        log.info("index: parsing %d PDF(s) with %d worker(s) in parallel ...", len(pdfs), jobs)
        ctx = mp.get_context("fork")
        parsed: list = []
        with ctx.Pool(jobs) as pool:
            for r in pool.imap_unordered(partial(_parse_and_chunk, force=args.force), pdfs):
                if r is not None:
                    parsed.append(r)
        store = open_store()
        for book_id, pdf, chunks in parsed:
            try:
                if _store_and_embed(store, book_id, pdf, chunks, args.force):
                    indexed += 1
            except Exception:
                log.exception("Failed to ingest %s — leaving it in inbox.", pdf.name)

    pending_index, pending_generate = count_pending()
    log.info(
        "index finished: %d indexed (pending index=%d, generate=%d).",
        indexed, pending_index, pending_generate,
    )
    return 0


# ---------------------------------------------------------------------------
# Phase C: generate (LLM ON — host starts/stops llama-server)
# ---------------------------------------------------------------------------

def generate_pending(store: Store, only_book: str | None = None, max_topics: int | None = None) -> int:
    """Generate notes/problems for all todo topics (llama-server must be up)."""
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
            # problems kind pulls a larger primary set so the theory section AND
            # its exercise blocks ("1.5 Exercises") both reach the prompt.
            primary_limit = 12 if row.kind == "problems" else 8
            primary = primary_sections(store, row.topic, refs, row.book, limit=primary_limit)
            cross = retrieve(store, row.topic, book_id=row.book)
            ids = {h.chunk_id for h in primary}
            hits = primary + [h for h in cross if h.chunk_id not in ids]
            if not hits:
                # Book not indexed yet — retry on a later pass instead of failing.
                log.warning(
                    "No retrieved chunks for '%s' (book=%s) — not indexed yet? Will retry.",
                    row.topic, row.book,
                )
                continue
            title = store.book_title(row.book) or row.book
            if row.kind == "problems":
                messages = generate.build_problem_messages(row.topic, title, row.book, row.section, hits)
                problems_text = generate.call_llama(messages)
                messages = generate.build_solution_messages(
                    row.topic, title, row.book, row.section, hits, problems_text
                )
                solutions_text = generate.call_llama(messages)
                p_out, s_out = generate.save_problem_set(
                    row.topic, row.book, row.section, problems_text, solutions_text,
                    out_base=row.note or None,
                )
                mark_topic(row.topic, "draft")
                log.info("Saved %s and %s; TOPICS.md status -> draft.", p_out, s_out)
            else:
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


def cmd_generate(args) -> int:
    """Phase C. Guards '선 RAG → 후 생성': skip while indexing is pending."""
    pending_index, pending_generate = count_pending()
    if pending_index > 0:
        log.warning(
            "선 RAG → 후 생성: %d PDF(s) 아직 인덱싱 안 됨 — generate 단계 스킵.", pending_index
        )
        return 0

    llm.require_llm()  # host가 pipeline.sh로 llama-server를 켜줘야 함
    store = open_store()
    generated = generate_pending(store, only_book=args.book, max_topics=args.max_topics)
    _, pending_generate = count_pending()
    log.info("generate finished: %d generated (pending generate=%d).", generated, pending_generate)
    return 0


def cmd_all(args) -> int:
    cmd_index(args)
    cmd_generate(args)
    return 0


def cmd_prefetch(_args) -> int:
    log.info("Prefetching embedding model %s ...", settings.embed_model)
    embed_index.get_embed_model()
    log.info("Prefetching reranker %s ...", settings.rerank_model)
    from rag.retrieve import get_reranker

    get_reranker()
    log.info("Models ready.")
    return 0


# ---------------------------------------------------------------------------
# reindex (re-chunk from cached markdown, no PDF re-parse)
# ---------------------------------------------------------------------------

def _reindex_one(store, book_id: str) -> int | None:
    from rag import chunk, embed_index as _embed

    row = store.book_row(book_id)
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
    _embed.index_book(book_id, chunks, force=True)
    return len(chunks)


def cmd_reindex(args) -> int:
    store = open_store()
    if args.all:
        rows = store.list_book_rows()
        if not rows:
            print("No indexed books yet.")
            return 0
        for r in rows:
            print(f"Reindexing '{r['book_id']}' ({r['title']}) ...")
            n = _reindex_one(store, r["book_id"])
            print(f"  -> {n} chunks" if n is not None else "  -> skipped")
        print("Done. Run `study.py status` to verify.")
        return 0

    n = _reindex_one(store, args.book_id)
    if n is None:
        print(f"book_id '{args.book_id}' not found in the index (indexed yet?)")
        return 1
    print(f"Reindexed '{args.book_id}': {n} chunks.")
    return 0


# ---------------------------------------------------------------------------
# note — generate ONE topic immediately (was tools/gen_note.py)
# ---------------------------------------------------------------------------

def cmd_note(args) -> int:
    """Generate a single note (or problem set) for one topic, immediately."""
    # Prefer the TOPICS.md row, allow CLI overrides.
    row = next((r for r in load_topics() if r.topic == args.topic), None)
    book = args.book or (row.book if row else None)
    section = args.section or (row.section if row else "") or ""
    out = args.out or (row.note if row and row.note else None)

    store = open_store()
    refs = [s.strip() for s in section.split(",") if s.strip()] if section else []
    primary = primary_sections(store, args.topic, refs, book)
    cross = retrieve(store, args.topic, book_id=book, k=args.crossref)
    ids = {h.chunk_id for h in primary}
    hits = primary + [h for h in cross if h.chunk_id not in ids]

    if not hits:
        print("No matching textbook chunks found. Is the book indexed (see README)?")
        return 1

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
        return 0

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
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_phase_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--force", action="store_true", help="re-parse PDFs even if markdown cached")
    p.add_argument("--book", default=None, help="restrict to one book_id")
    p.add_argument("--max-topics", type=int, default=None, help="cap topics per pass")
    p.add_argument("--jobs", type=int, default=None,
                   help="parse N PDFs concurrently (index only; ~4-6GB RAM per worker)")


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="study.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # --- pipeline phases ---
    p = sub.add_parser("index", help="Phase A+B: parse + index inbox PDFs (LLM off)")
    p.add_argument("--force", action="store_true", help="re-parse PDFs even if markdown cached")
    p.add_argument("--book", default=None, help="restrict to one book_id")
    p.add_argument("--jobs", type=int, default=None,
                   help="parse N PDFs concurrently (default 1; ~4-6GB RAM per worker)")

    p = sub.add_parser("generate", help="Phase C: generate pending notes (LLM must be up)")
    p.add_argument("--book", default=None, help="restrict to one book_id")
    p.add_argument("--max-topics", type=int, default=None, help="cap topics per pass")

    p = sub.add_parser("all", help="index then generate (once)")
    _add_phase_args(p)

    sub.add_parser("prefetch", help="download embedding/rerank models and exit")

    p = sub.add_parser("note", help="generate ONE topic immediately (LLM must be up)")
    p.add_argument("topic", help="topic name, as listed in TOPICS.md")
    p.add_argument("--book", default=None, help="book_id (defaults to TOPICS.md row)")
    p.add_argument("--section", default=None, help="primary section(s), e.g. '3.5' or '3.5, 3.6'")
    p.add_argument("--effort", default=None, help="override REASONING_EFFORT (low/medium/high)")
    p.add_argument("--out", default=None, help="output path relative to study/")
    p.add_argument("--crossref", type=int, default=None, help="number of cross-reference chunks")
    p.add_argument("--problems", action="store_true",
                   help="generate a 10+10 problem set with a separate solutions file")
    p.add_argument("--update-topics", action="store_true", help="set the row status to draft")

    # --- registry & state ---
    sub.add_parser("init", help="create schema + import markdown if the DB is empty")
    sub.add_parser("import", help="load TOPICS.md / AGENTS.md / template into the DB (overwrites)")
    sub.add_parser("export", help="rewrite markdown files from the DB")
    sub.add_parser("status", help="show registry + index + pending + notes in one view")
    ra = sub.add_parser("reset-all", help="reset DB + index + books + notes to a clean state")
    ra.add_argument("--yes", action="store_true", help="confirm the destructive reset")
    ra.add_argument("--keep-pdfs", action="store_true",
                    help="keep uploaded PDFs (processed/ -> inbox/) and wipe everything else")

    # --- books / topics / docs ---
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
    from rag import registry as _reg
    ta.add_argument("--kind", default="note", choices=_reg.KINDS,
                    help="note = concept note, problems = 10+10 problem set with solutions")
    tl = tsub.add_parser("list", help="list topics")
    tl.add_argument("--status", default=None, choices=_reg.STATUSES)
    tl.add_argument("--book", default=None)
    ts = tsub.add_parser("set", help="update fields of one topic")
    ts.add_argument("topic")
    ts.add_argument("--status", default=None, choices=_reg.STATUSES)
    ts.add_argument("--kind", default=None, choices=_reg.KINDS)
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

    # --- reindex ---
    rp = sub.add_parser("reindex", help="re-chunk from cached markdown (no PDF re-parse)")
    rp.add_argument("book_id", nargs="?", help="index book_id (see: study.py status)")
    rp.add_argument("--all", action="store_true", help="reindex every indexed book")

    args = ap.parse_args()
    setup_logging()

    # `note` needs llama-server up; fail fast with a clear message.
    if args.cmd == "note":
        llm.require_llm()
        if args.effort:
            settings.reasoning_effort = args.effort

    dispatch = {
        "index": cmd_index,
        "generate": cmd_generate,
        "all": cmd_all,
        "prefetch": cmd_prefetch,
        "note": cmd_note,
        "init": cmd_init,
        "import": cmd_import,
        "export": cmd_export,
        "status": cmd_status,
        "reset-all": cmd_reset_all,
        "books": cmd_books,
        "topics": cmd_topics,
        "docs": cmd_docs,
        "reindex": cmd_reindex,
    }
    try:
        sys.exit(dispatch[args.cmd](args) or 0)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
