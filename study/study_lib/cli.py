"""cli — study_lib 위의 얇은 CLI 래퍼.

사용 예:
    python -m study_lib.cli books add --id calc --title Calculus --subject math
    python -m study_lib.cli books list
    python -m study_lib.cli topics add --book calc --title "Limit of a sequence" --section 3.5
    python -m study_lib.cli topics set <topic> --status draft
    python -m study_lib.cli index book.md --book calc --profile text --embedder stub
    python -m study_lib.cli status
    python -m study_lib.cli generate --book calc

공통 경로: --registry(registry.json) --store(store/) --notes(notes/) — env STUDY_* 로도 지정.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .bookmarks import discover_topics_from_bookmarks
from .chunk import chunk_markdown
from .discover import discover_topics
from .embed import StubEmbedder, TransformerEmbedder
from .factory import generate_one
from .ingest import ingest_dir
from .llm import FlashClient, LLMError
from .parse import parse_source, parser_names
from .profiles import load_profile, profile_names
from .protocol import load_schema
from .registry import INDEXED, JsonFileStore, Library
from .store import IndexStore, JsonDurableSink
from .subjects import SUBJECTS


@dataclass
class Workspace:
    registry: str
    store: str
    notes: str

    def library(self) -> Library:
        return Library(JsonFileStore(self.registry))

    def open_store(self) -> IndexStore:
        return IndexStore(JsonDurableSink(self.store))


def _resolve_embedder(kind: str):
    if kind == "stub":
        return StubEmbedder()
    try:
        return TransformerEmbedder()
    except Exception as exc:  # 무거운 모델 없으면 오프라인/개발용 stub 폴백
        print(f"note: {exc}; falling back to StubEmbedder", file=sys.stderr)
        return StubEmbedder()


# ---- books ----
def _books_add(ws: Workspace, args) -> int:
    lib = ws.library()
    lib.add_book(args.id, args.title, args.subject, source=args.source or "",
                 parser=args.parser, chunk_profile=args.chunk_profile,
                 page_range=args.page_range)
    lib.save()
    print(f"added book {args.id}")
    return 0


def _books_list(ws: Workspace, args) -> int:
    lib = ws.library()
    for b in lib.books():
        p = b.parser or "auto"
        pr = b.page_range or "all"
        print(f"{b.book_id:<16} {b.subject:<6} {b.status:<10} parser={p:<8} "
              f"pages={pr:<10} {b.title}")
    return 0


def _books_set_parser(ws: Workspace, args) -> int:
    lib = ws.library()
    lib.set_book_parser(args.book, args.parser)
    lib.save()
    print(f"set parser for {args.book} -> {args.parser or 'auto'}")
    return 0


def _books_set_page_range(ws: Workspace, args) -> int:
    lib = ws.library()
    lib.set_book_page_range(args.book, args.range_)
    lib.save()
    print(f"set page_range for {args.book} -> {args.range_ or 'all'}")
    return 0


# ---- topics ----
def _topics_add(ws: Workspace, args) -> int:
    lib = ws.library()
    topic = lib.add_topic(book_id=args.book, title=args.title, kind=args.kind,
                          section=args.section)
    lib.save()
    print(f"added topic {topic.topic_id}")
    return 0


def _topics_list(ws: Workspace, args) -> int:
    lib = ws.library()
    for t in lib.topics(book_id=args.book, status=args.status):
        print(f"{t.status:<8} {t.topic_id:<40} book={t.book_id} kind={t.kind}")
    return 0


def _topics_set(ws: Workspace, args) -> int:
    lib = ws.library()
    lib.set_status(args.topic, args.status)
    lib.save()
    print(f"set {args.topic} -> {args.status}")
    return 0


def _topics_rm(ws: Workspace, args) -> int:
    lib = ws.library()
    lib.delete_topic(args.topic)
    lib.save()
    print(f"deleted topic {args.topic}")
    return 0


def _topics_clear(ws: Workspace, args) -> int:
    lib = ws.library()
    n = lib.clear_topics(args.book)
    lib.save()
    print(f"cleared {n} topics for {args.book}")
    return 0


def _topics_discover(ws: Workspace, args) -> int:
    lib = ws.library()
    store = ws.open_store()
    store.load_all()
    report = discover_topics(args.book, library=lib, store=store, kind=args.kind)
    for tid in report.added:
        t = lib.topic(tid)
        print(f"added topic {t.topic_id} ({t.section}) {t.title}")
    print(report.summary())
    return 0


def _topics_discover_bookmarks(ws: Workspace, args) -> int:
    """PDF 북마크에서 번호 섹션 토픽 생성 (docling 이 번호를 버리는 책용)."""
    lib = ws.library()
    book = lib.book(args.book)
    source = args.source or book.source
    if not source:
        print("error: --source 필요 (책 source 가 비어 있음)", file=sys.stderr)
        return 1
    report = discover_topics_from_bookmarks(args.book, library=lib,
                                            source=source, kind=args.kind)
    for tid in report.added:
        t = lib.topic(tid)
        print(f"added topic {t.topic_id} ({t.section}) {t.title}")
    print(report.summary())
    return 0


# ---- status ----
def _status(ws: Workspace, args) -> int:
    lib = ws.library()
    books, topics = lib.books(), lib.topics()
    print(f"books={len(books)} topics={len(topics)} pending_topics={len(lib.pending_topics())}")
    for t in topics:
        print(f"  {t.status:<8} {t.topic_id}  ({t.book_id})")
    return 0


# ---- index ----
def _index(ws: Workspace, args) -> int:
    lib = ws.library()
    lib.book(args.book)  # 미등록 책이면 KeyError → main 이 안내
    parsed = parse_source(args.path, profile=args.profile, book_id=args.book)
    chunks = chunk_markdown(parsed.markdown, book_id=args.book)
    if not chunks:
        raise ValueError(f"no chunks extracted from {args.path}")
    embedder = _resolve_embedder(args.embedder)
    vectors = embedder.embed_texts([c.text for c in chunks])
    store = ws.open_store()
    store.add_many(chunks, vectors=vectors)
    store.flush(args.book)
    lib.set_book_status(args.book, INDEXED)
    lib.save()
    pages = parsed.pages if parsed.pages is not None else "-"
    print(f"indexed {args.book}: {len(chunks)} chunks (parser={parsed.parser}, pages={pages})")
    return 0


# ---- ingest (폴더 배치) ----
def _ingest(ws: Workspace, args) -> int:
    spec = ("stub",) if args.embedder == "stub" else ("auto",)
    chunk_profile = load_profile(args.chunk_profile) if args.chunk_profile else None

    def report_line(report) -> None:
        if report.ok:
            print(f"ingested {report.book_id}: {report.chunks} chunks "
                  f"(parser={report.parser})", flush=True)
        else:
            print(f"failed   {report.book_id}: {report.error}",
                  file=sys.stderr, flush=True)

    report = ingest_dir(args.directory, library=ws.library(), store=ws.open_store(),
                        subject=args.subject, profile=args.profile,
                        embedder_spec=spec, force=args.force, jobs=args.jobs,
                        chunk_profile=chunk_profile, page_range=args.page_range,
                        log=lambda msg: print(msg, flush=True),
                        on_report=report_line)
    for bid in report.skipped:
        print(f"skip     {bid} (already indexed)")
    print(report.summary())
    return 1 if report.failed else 0


# ---- generate ----
def default_guide_path(subject: str) -> Path:
    """과목 가이드(B) 기본 위치: <repo>/guides/<subject>.md (cwd 무관)."""
    return Path(__file__).resolve().parent.parent / "guides" / f"{subject}.md"


def _load_guide(path: str | None, subject: str) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    guide = default_guide_path(subject)
    return guide.read_text(encoding="utf-8") if guide.exists() else ""


def _generate(ws: Workspace, args) -> int:
    lib = ws.library()
    if args.topic:
        topics = [lib.topic(args.topic)]          # 개별 토픽 1건 (상태 무관)
    elif args.book:
        lib.book(args.book)
        topics = lib.topics(book_id=args.book, status="todo")
    else:
        topics = lib.pending_topics()
    if args.limit and args.limit > 0:
        topics = topics[:args.limit]
    if not topics:
        print("no pending topics")
        return 0

    store = ws.open_store()
    store.load_all()
    schema = load_schema()
    embedder = _resolve_embedder(args.embedder)
    llm = FlashClient()  # 설정 누락 시 LLMError → main 이 안내
    guide = _load_guide(args.guide, topics[0].subject)

    done = failed = 0
    total_cost = 0.0
    for topic in topics:
        res = generate_one(topic, library=lib, store=store, embedder=embedder, llm=llm,
                           schema=schema, guide=guide, notes_dir=ws.notes)
        total_cost += res.usage.cost_usd()
        if res.status == "draft":
            done += 1
            print(f"draft  {topic.topic_id} -> {res.note_path}")
        else:
            failed += 1
            print(f"failed {topic.topic_id}: {'; '.join(res.issues[:2])}", file=sys.stderr)
    print(f"done={done} failed={failed} total_cost=${total_cost:.4f}")
    return 1 if failed else 0


# ---- parser ----
def _common():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--registry", default=os.environ.get("STUDY_REGISTRY", "registry.json"))
    p.add_argument("--store", default=os.environ.get("STUDY_STORE", "store"))
    p.add_argument("--notes", default=os.environ.get("STUDY_NOTES", "notes"))
    p.add_argument("--embedder", default="auto", choices=["auto", "stub"])
    return p


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study")
    sub = parser.add_subparsers(dest="command", required=True)
    common = _common()

    def subp(name):
        return sub.add_parser(name, parents=[common])

    p = subp("books")
    bs = p.add_subparsers(dest="action", required=True)
    pa = bs.add_parser("add", parents=[common])
    pa.add_argument("--id", required=True)
    pa.add_argument("--title", required=True)
    pa.add_argument("--subject", required=True)
    pa.add_argument("--source")
    pa.add_argument("--parser", choices=list(parser_names()))
    pa.add_argument("--chunk-profile", choices=list(profile_names()))
    pa.add_argument("--page-range", help="파싱할 페이지 범위 '42-1249' (1-based, 전체=비움)")
    pa.set_defaults(func=_books_add)
    pl = bs.add_parser("list", parents=[common])
    pl.set_defaults(func=_books_list)
    psp = bs.add_parser("set-parser", parents=[common])
    psp.add_argument("--book", required=True)
    psp.add_argument("--parser", choices=list(parser_names()))
    psp.set_defaults(func=_books_set_parser)
    psr = bs.add_parser("set-page-range", parents=[common])
    psr.add_argument("--book", required=True)
    psr.add_argument("--range", dest="range_",
                     help="페이지 범위 '42-1249' (1-based) — 전체로 되돌리려면 빈 값/생략")
    psr.set_defaults(func=_books_set_page_range)

    p = subp("topics")
    ts = p.add_subparsers(dest="action", required=True)
    ta = ts.add_parser("add", parents=[common])
    ta.add_argument("--book", required=True)
    ta.add_argument("--title", required=True)
    ta.add_argument("--kind", default="exam", choices=["exam", "note", "problems"])
    ta.add_argument("--section")
    ta.set_defaults(func=_topics_add)
    tl = ts.add_parser("list", parents=[common])
    tl.add_argument("--book")
    tl.add_argument("--status", choices=["todo", "draft", "review", "done"])
    tl.set_defaults(func=_topics_list)
    tset = ts.add_parser("set", parents=[common])
    tset.add_argument("topic")
    tset.add_argument("--status", required=True,
                      choices=["todo", "draft", "review", "done"])
    tset.set_defaults(func=_topics_set)
    trm = ts.add_parser("rm", parents=[common])
    trm.add_argument("topic")
    trm.set_defaults(func=_topics_rm)
    tclear = ts.add_parser("clear", parents=[common])
    tclear.add_argument("--book", required=True)
    tclear.set_defaults(func=_topics_clear)

    td = ts.add_parser("discover", parents=[common])
    td.add_argument("--book", required=True)
    td.add_argument("--kind", default="exam",
                    choices=["exam", "note", "problems"])
    td.set_defaults(func=_topics_discover)

    tdb = ts.add_parser("discover-bookmarks", parents=[common])
    tdb.add_argument("--book", required=True)
    tdb.add_argument("--source",
                     help="PDF 경로 (기본: 책의 source 메타데이터)")
    tdb.add_argument("--kind", default="exam",
                     choices=["exam", "note", "problems"])
    tdb.set_defaults(func=_topics_discover_bookmarks)

    st = subp("status")
    st.set_defaults(func=_status)

    ix = subp("index")
    ix.add_argument("path")
    ix.add_argument("--book", required=True)
    ix.add_argument("--profile", choices=["text", "fast", "docling"])
    ix.set_defaults(func=_index)

    ing = subp("ingest")
    ing.add_argument("directory")
    ing.add_argument("--subject", default="math", choices=list(SUBJECTS))
    ing.add_argument("--profile", choices=list(parser_names()),
                     help="파서 기본값(folder). 책별 books set-parser 지정이 우선.")
    ing.add_argument("--jobs", type=int, default=1)
    ing.add_argument("--chunk-profile", choices=list(profile_names()))
    ing.add_argument("--page-range",
                     help="신규 등록 책들의 파싱 페이지 범위 '42-1249' (기존 책은 set-page-range)")
    ing.add_argument("--force", action="store_true")
    ing.set_defaults(func=_ingest)

    gen = subp("generate")
    gen.add_argument("--book")
    gen.add_argument("--topic", help="개별 토픽 1건만 생성 (상태 무관)")
    gen.add_argument("--limit", type=int,
                     help="todo 토픽 중 앞에서 N개만 생성 (검증용)")
    gen.add_argument("--guide")
    gen.set_defaults(func=_generate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ws = Workspace(args.registry, args.store, args.notes)
    try:
        return args.func(ws, args)
    except (KeyError, ValueError, LLMError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
