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
from .ingest import ingest_dir, ingest_one, ingest_piece
from .llm import FlashClient, LLMError
from .parse import parse_source, parser_names
from .profiles import load_profile, profile_names
from .protocol import load_schema
from .registry import INDEXED, JsonFileStore, Library
from .store import IndexStore, JsonDurableSink
from .subjects import SUBJECTS
from .postproc_katex import default_ollama_client, guard_ok, postkatex_if_enabled


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


def _books_rm(ws: Workspace, args) -> int:
    """책·토픽·store 청크·notes 파일을 모두 삭제 (--yes 필수)."""
    print(f"WARNING: this deletes book '{args.book}', its topics, "
          f"stored chunks, and notes files.", file=sys.stderr)
    if not args.yes:
        print("aborted: pass --yes to confirm", file=sys.stderr)
        return 1
    lib = ws.library()
    topics = len(lib.topics(book_id=args.book))
    # notes 파일 삭제
    removed_notes = 0
    for t in lib.topics(book_id=args.book):
        if t.note_path:
            np = Path(t.note_path)
            if np.exists():
                np.unlink()
                removed_notes += 1
    lib.delete_book(args.book)          # book + topics
    lib.save()
    # store 청크 삭제
    store_path = Path(args.store)
    jsonl = store_path / f"{args.book}.jsonl"
    if jsonl.exists():
        jsonl.unlink()
    print(f"deleted book {args.book}: topics={topics} notes_files={removed_notes}")
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


def _ingest_piece(ws: Workspace, args) -> int:
    """이미 있는 책에 page 조각을 추가 누적(멱등) — store 를 지우지 않는다.

    이미 그 페이지가 들어 있으면 skip, 부분 중복이면 새 페이지만 docling 으로.
    accumulate 이라 이 책에 쌓인 여러 조각이 store 에 함께 남는다.
    수식 LaTeX 는 컨테이너 env(DOCLING_FORMULAS=1 등) 로 켠다.
    """
    def src_path() -> Path:
        if args.source:
            return Path(args.source)
        book = ws.library().book(args.book)
        if not book.source:
            raise ValueError(f"book {args.book} has no source; pass --source PATH")
        return Path(book.source)

    lib = ws.library()
    parser = lib.book(args.book).parser or "docling"
    chunk_profile = load_profile(args.chunk_profile) if args.chunk_profile else None
    embedder = _resolve_embedder(args.embedder)
    print(f"[accumulate] {args.book} pages={args.pages} parser={parser} "
          f"embedder={type(embedder).__name__} "
          f"(FORMULAS={os.environ.get('DOCLING_FORMULAS', '0')})", flush=True)
    res = ingest_piece(src_path(), library=lib, store=ws.open_store(),
                       embedder=embedder, book_id=args.book, pages=args.pages,
                       chunk_profile=chunk_profile,
                       log=lambda msg: print(msg, flush=True))
    if not res.ok:
        print(f"failed   {args.book}: {res.error}", file=sys.stderr, flush=True)
        return 1
    if res.chunks == 0:
        print(f"skip     {args.book}: pages {args.pages} already covered (no new)")
        return 0
    print(f"ingested {args.book}: +{res.chunks} chunks over pages={res.pages} "
          f"(parser={res.parser})")
    print(f"now covered intervals: {lib.book(args.book).intervals}")
    return 0


# ---- postkatex (로컬 Ollama 로 완성된 note 의 수식만 다양화/후처리) ----
def _postkatex_cmd(ws: Workspace, args) -> int:
    lib = ws.library()
    targets: list[Path] = []
    if args.path:
        targets = [Path(args.path)]
    elif args.book:
        targets = [Path(t.note_path) for t in lib.topics(book_id=args.book)
                   if t.note_path and Path(t.note_path).exists()]
    else:
        print("error: pass --path FILE or --book", file=sys.stderr)
        return 2
    if not targets:
        print("no note files matched")
        return 0
    client = default_ollama_client()
    print(f"[postkatex] model={client.model} host={client.base_url} "
          f"files={len(targets)}")
    changed = kept = 0
    for p in targets:
        before = p.read_text(encoding="utf-8")
        out = postkatex_if_enabled(p, force=True)   # env 없어도 force
        if out and out != before:
            changed += 1
            print(f"reformatted {p.name}")
        else:
            kept += 1
    print(f"done: changed={changed} kept={kept}")
    return 0


# ---- chapter focus (챕터 단위 소규모 재인제스트) ----
# 챕터는 사용자가 직접 페이지 범위(--range)로 지정한다.
# EXAMPLES:  python -m study_lib.cli chapter focus --book calc --range 42-90
def _chapter_source(args, ws) -> Path:
    """책 source 의 로컬 PDF 경로 결정: --source 명시 → 책 메타 source."""
    if getattr(args, "source", None):
        return Path(args.source)
    book = ws.library().book(args.book)
    if not book.source:
        raise ValueError(f"book {args.book} has no source; pass --source PATH")
    return Path(book.source)


def _chapter_focus(ws: Workspace, args) -> int:
    """사용자가 고른 챕터 페이지 범위만 재인제스트 (기본 docling+FORMULAS env).

    흐름:
      1) book.page_range = args.range   (그 챕터 구간만 파싱)
      2) 기존 store 청크 삭제 → 재인제스트 (FORMULAS 여부는 컨테이너 env 로)
      3) 파서 기본 docling (책별 books set-parser 우선)
    """
    lib = ws.library()
    book = lib.book(args.book)
    src = _chapter_source(args, ws)
    lib.set_book_page_range(args.book, args.range_)
    lib.save()

    store = ws.open_store()
    store.load_all()
    store.delete_book(args.book)   # 무해(청크 없으면 no-op), ingest_one 이 재기록

    parser = book.parser or "docling"
    embedder = _resolve_embedder(args.embedder)
    print(f"[chapter] focusing {args.book} pages={args.range_} "
          f"parser={parser} embedder={type(embedder).__name__} "
          f"(FORMULAS={os.environ.get('DOCLING_FORMULAS', '0')})",
          flush=True)

    def report_line(report) -> None:
        if report.ok:
            print(f"ingested {args.book}: {report.chunks} chunks "
                  f"(parser={report.parser}, pages={report.pages})", flush=True)
        else:
            print(f"failed   {args.book}: {report.error}", file=sys.stderr, flush=True)

    res = ingest_one(
        src, library=lib, store=store, embedder=embedder, profile=parser,
        book_id=args.book,
        chunk_profile=load_profile(args.chunk_profile) if args.chunk_profile else None,
        log=lambda msg: print(msg, flush=True),
        figures_registry=None,
    )
    report_line(res)
    if not res.ok:
        print(f"note: page_range 를 {args.range_} 로 남겨둡니다 — 필요시 "
              f"'books set-page-range --book {args.book} --range <원래>' 로 되돌리세요",
              file=sys.stderr)
    else:
        print(f"[chapter] page_range set to {args.range_}. "
              f"같은 책 다음 챕터는 범위만 바꿔 다시 focus 하면 됩니다.",
              flush=True)
    return 1 if not res.ok else 0


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
    prm = bs.add_parser("rm", parents=[common])
    prm.add_argument("--book", required=True)
    prm.add_argument("--yes", action="store_true",
                     help="삭제 승인 (없으면 중단)")
    prm.set_defaults(func=_books_rm)

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

    acc = subp("accumulate")
    acc.add_argument("--book", required=True)
    acc.add_argument("--pages", dest="pages", required=True,
                     help="추가할 페이지 조각 '786-795' (1-based, 직접 입력)")
    acc.add_argument("--source", help="PDF 경로(명시 안 하면 책 메타 source)")
    acc.add_argument("--chunk-profile", choices=list(profile_names()))
    acc.set_defaults(func=_ingest_piece)

    pk = subp("postkatex")
    pk.add_argument("--path", help="개별 note 파일 후처리")
    pk.add_argument("--book", help="책의 모든 draft note 파일 후처리")
    pk.set_defaults(func=_postkatex_cmd)

    # ---- chapter focus: 챕터는 사용자가 페이지 범위로 직접 지정 ----
    chap = subp("chapter")
    cs = chap.add_subparsers(dest="action", required=True)
    chf = cs.add_parser("focus", parents=[common])
    chf.add_argument("--book", required=True)
    chf.add_argument("--range", dest="range_", required=True,
                     help="챕터 페이지 범위 '42-90' (1-based, 직접 입력)")
    chf.add_argument("--source", help="PDF 경로(명시 안 하면 책 메타 source)")
    chf.add_argument("--chunk-profile", choices=list(profile_names()))
    chf.set_defaults(func=_chapter_focus)

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
