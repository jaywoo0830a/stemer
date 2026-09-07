"""인제스트 — 사전 준비(대량 인덱싱) 오케스트레이션.

클라이언트 관점:
    report = ingest_dir("books/", library=lib, store=store,
                        subject="math", embedder_spec=("stub",),
                        profile=None, jobs=2)

- 폴더의 PDF/MD/TXT 를 책 단위로: 등록 → 파싱 → 청킹 → 임베딩 → 저장 → `indexed`.
- **재개/실패 관리**: 이미 `indexed` 책은 스킵(`force` 시 재인덱스), 실패 책은 `failed` +
  사유(Book.error)로 남기고 다음 실행에서 재시도.
- **병렬**: `jobs>1` 이면 멀티프로세싱으로 파싱+청킹+임베딩을 워커에서 수행하고,
  저장(store.flush)과 registry 전이는 부모에서 단일 수행(RAM 스테이징).
"""
from __future__ import annotations

import multiprocessing
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .chunk import ChunkProfile, chunk_markdown
from .embed import StubEmbedder, TransformerEmbedder
from .figures import register_parsed_figures
from .parse import detect_scanned, parse_source
from .profiles import load_profile
from .registry import INDEXED, Library, slugify
from .store import IndexStore

_EXTS = {".pdf", ".md", ".txt"}

EMBED_BATCH = 32        # 워커당 임베딩 배치


def _embed_with_progress(embedder, texts: list, label: str,
                         log: Callable[[str], None] | None) -> list:
    """배치 단위 임베딩 + 배치마다 진행 로그(경과시간 포함).

    주의: `end % N == 0` 방식은 end 가 배치(32)의 배수라 lcm(N,32) 마다만
    찍혀 조용한 구간이 길어진다 → **배치 완료마다** 로그한다.
    """
    out: list = []
    total = len(texts)
    t0 = time.monotonic()
    if log:
        log(f"[{label}] embed 0/{total}")
    for start in range(0, total, EMBED_BATCH):
        end = min(start + EMBED_BATCH, total)
        out.extend(embedder.embed_texts(texts[start:end], batch_size=EMBED_BATCH))
        if log:
            el = time.monotonic() - t0
            log(f"[{label}] embed {end}/{total} (+{el:.0f}s)")
    return out


@dataclass(frozen=True)
class IngestReport:
    book_id: str
    ok: bool
    chunks: int = 0
    parser: str = ""
    pages: int | None = None
    error: str = ""


@dataclass(frozen=True)
class BatchReport:
    ingested: tuple[IngestReport, ...] = ()
    skipped: tuple[str, ...] = ()
    failed: tuple[IngestReport, ...] = ()

    def summary(self) -> str:
        return (f"ingested={len(self.ingested)} skipped={len(self.skipped)} "
                f"failed={len(self.failed)}")


def _make_embedder(spec: tuple):
    """임베더 스펙 → 인스턴스. ('stub',) | ('auto',) | ('transformers', model?).

    'auto' 는 transformers 가 없으면 stub 으로 폴백(오프라인/개발).
    """
    kind = spec[0]
    if kind == "stub":
        return StubEmbedder()
    if kind == "auto":
        try:
            return TransformerEmbedder()
        except Exception:
            return StubEmbedder()
    if kind == "transformers":
        model = spec[1] if len(spec) > 1 and spec[1] else None
        return TransformerEmbedder(model=model) if model else TransformerEmbedder()
    raise ValueError(f"unknown embedder spec {spec!r}")


def _task(path: Path, book_id: str, profile: str | None,
          chunk_profile: ChunkProfile | None, embedder_spec: tuple,
          threads: int, page_range: str | None = None) -> dict:
    return {"path": str(path), "book_id": book_id, "profile": profile,
            "chunk_profile": chunk_profile, "embedder_spec": embedder_spec,
            "threads": threads, "page_range": page_range}


def _cap_worker_threads(threads: int) -> None:
    """CPU oversubscription(livelock) 방지: torch/OMP 스레드 상한을 env 로 보장.

    spawn 워커는 torch 를 여기서 처음 import 하므로, env 를 먼저 설정하면
    워커당 스레드 수가 `threads` 로 고정된다 (compose env 에 의존하지 않음).
    """
    threads = max(1, int(threads))
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["MKL_NUM_THREADS"] = str(threads)
    os.environ["OPENBLAS_NUM_THREADS"] = str(threads)


def _guard_quality(parsed) -> None:
    """품질 게이트 — 텍스트 파서 결과가 스캔 의심이면 docling 을 안내하며 중단."""
    if parsed.parser != "docling" and detect_scanned(parsed):
        raise ValueError(
            f"only {len(parsed.markdown.strip())} chars across {parsed.pages} pages "
            f"— likely scanned; use profile=docling"
        )


def _resolve_profile(library: Library, book_id: str, explicit: ChunkProfile | None) -> ChunkProfile | None:
    """책에 저장된 청크 프로필 이름 → ChunkProfile (명시 값이 우선)."""
    if explicit is not None:
        return explicit
    name = library.book(book_id).chunk_profile
    return load_profile(name) if name else None


def _resolve_parser(library: Library, book_id: str, explicit: str | None) -> str | None:
    """책별 파서 결정: 책.parser > CLI 기본(폴더) > None(확장자 추론)."""
    book_parser = library.book(book_id).parser
    if book_parser:
        return book_parser
    return explicit


def _book_page_range(library: Library, book_id: str) -> str | None:
    """책에 저장된 페이지 범위(예: '42-1249') — 없으면 None(전체)."""
    return library.book(book_id).page_range


def _worker_ingest(payload: dict) -> dict:
    """워커: 파싱→청킹→임베딩만 수행(진행 로그 출력). 저장/registry 는 부모가 한다."""
    bid = payload["book_id"]
    # torch/OMP 스레드 상한을 import 전에 보장 (oversubscription → livelock 방지)
    _cap_worker_threads(payload.get("threads") or 1)
    try:
        parsed = parse_source(payload["path"], profile=payload["profile"], book_id=bid,
                              page_range=payload.get("page_range"), log=print)
        _guard_quality(parsed)
        chunks = chunk_markdown(parsed.markdown, book_id=bid,
                                profile=payload["chunk_profile"])
        if not chunks:
            raise ValueError(f"no chunks extracted from "
                             f"{Path(payload['path']).name} (scanned PDF? use profile=docling)")
        print(f"[{bid}] parse done pages={parsed.pages} chunks={len(chunks)}", flush=True)
        embedder = _make_embedder(payload["embedder_spec"])
        t0 = time.monotonic()
        vectors = _embed_with_progress(embedder, [c.text for c in chunks], bid, print)
        el = time.monotonic() - t0
        rate = len(chunks) / el if el > 0 else float("inf")
        print(f"[{bid}] embed done {len(chunks)} chunks in {el:.0f}s ({rate:.1f}/s)", flush=True)
        return {"ok": True, "book_id": bid, "chunks": chunks,
                "vectors": vectors, "parser": parsed.parser, "pages": parsed.pages}
    except Exception as exc:  # 워커 실패 → 사유만 반환
        print(f"[{bid}] failed: {exc}", flush=True)
        return {"ok": False, "book_id": bid, "error": str(exc)}


def _commit_ok(library: Library, store: IndexStore, book_id: str,
               chunks: list, vectors: list, parser: str, pages: int | None) -> None:
    store.delete_book(book_id)          # 재인덱스 안전(원자적으로 교체)
    store.add_many(chunks, vectors=vectors)
    store.flush(book_id)
    library.set_book_status(book_id, INDEXED)
    library.save()


def ingest_one(path: str | Path, *, library: Library, store: IndexStore,
               embedder, profile: str | None = None,
               book_id: str | None = None,
               chunk_profile: ChunkProfile | None = None,
               figures_registry=None,
               log: Callable[[str], None] | None = None) -> IngestReport:
    """단일 책 인제스트 (직접 호출용). 성공 시 indexed, 실패 시 failed+사유."""
    p = Path(path)
    bid = book_id or slugify(p.stem)
    try:
        library.book(bid)  # 등록 확인
    except KeyError:
        library.add_book(bid, p.stem, "math", source=p.name)
        library.save()
    try:
        parser = _resolve_parser(library, bid, profile)
        parsed = parse_source(p, profile=parser, book_id=bid,
                              page_range=_book_page_range(library, bid),
                              log=log if log else None)
        _guard_quality(parsed)
        profile_obj = _resolve_profile(library, bid, chunk_profile)
        chunks = chunk_markdown(parsed.markdown, book_id=bid, profile=profile_obj)
        if not chunks:
            raise ValueError(f"no chunks extracted from {p.name} "
                             f"(scanned PDF? use profile=docling)")
        if log:
            log(f"[{bid}] parse done pages={parsed.pages} chunks={len(chunks)}")
        vectors = _embed_with_progress(embedder, [c.text for c in chunks], bid, log)
        _commit_ok(library, store, bid, chunks, vectors, parsed.parser, parsed.pages)
        if figures_registry is not None and parsed.figures:
            register_parsed_figures(figures_registry, parsed.figures, bid)
        if log:
            log(f"[{bid}] commit done ({len(chunks)} chunks)")
        return IngestReport(book_id=bid, ok=True, chunks=len(chunks),
                            parser=parsed.parser, pages=parsed.pages)
    except Exception as exc:
        library.set_book_error(bid, str(exc))
        library.save()
        return IngestReport(book_id=bid, ok=False, error=str(exc))


def ingest_piece(path: str | Path, *, library: Library, store: IndexStore,
                 embedder, book_id: str, pages: str,
                 chunk_profile: ChunkProfile | None = None,
                 figures_registry=None,
                 log: Callable[[str], None] | None = None) -> IngestReport:
    """증분 인제스트 — 이미 들은 책에 page 조각(pages 'A-B') 을 추가 누적.

    기존(전체) 인제스트와 달리 store 를 지우지 않는다:
      1) 아니 아직 안 넣은 페이지만 골라냄(library.pending_pages)
      2) 각 미커버 구간을 docling page_range 로 파싱해
         **기존 book 청크 seq 뒤부터** 이어 붙여 store 에 append
      3) 성공한 구간을 book.intervals 에 병합 → 나중 조각들과 함께 책 전체가 누적
    같은 페이지 재호출은 멱등(skip). 부분 중복이면 새 페이지만 파싱.
    """
    bid = book_id
    parsed_book = library.book(bid)          # 미등록 → KeyError
    pending = library.pending_pages(bid, pages)
    if not pending:
        if log:
            log(f"[{bid}] pages {pages} already covered -> skip")
        return IngestReport(book_id=bid, ok=True, chunks=0, parser="",
                            pages=len(pending))
    store.load_all()                          # 기존 청크를 반드시 RAM 에 (append 용)
    parser = parsed_book.parser or "docling"
    seq = store.book_max_seq(bid) + 1         # 기존 청크 뒤부터 (id 충돌 없음)

    all_chunks = []
    all_vectors = []
    seen_pages = 0
    try:
        for span in pending:
            rng = f"{span[0]}-{span[1]}"
            parsed = parse_source(path, profile=parser, book_id=bid, page_range=rng,
                                  log=log if log else None)
            _guard_quality(parsed)
            chunks = chunk_markdown(parsed.markdown, book_id=bid,
                                    profile=chunk_profile, start_seq=seq)
            seq += len(chunks)
            if not chunks:
                raise ValueError(f"no chunks extracted for pages {rng}")
            if log:
                log(f"[{bid}] parse pages={rng} chunks={len(chunks)}")
            vectors = _embed_with_progress(embedder, [c.text for c in chunks], bid, log)
            all_chunks.extend(chunks)
            all_vectors.extend(vectors)
            seen_pages += span[1] - span[0] + 1
        store.add_many(all_chunks, vectors=all_vectors)
        store.flush(bid)                      # 책 전체(기존 + 신규)를 한 파일로
        library.mark_pages_ingested(bid, pending)
        # 그림/표 레지스트리: 문단 하위 추가는 생략(있으면 확장)
        library.set_book_status(bid, INDEXED)
        library.save()
        if log:
            log(f"[{bid}] commit done (+{len(all_chunks)} chunks => "
                f"total {store.book_max_seq(bid)+1})")
        return IngestReport(book_id=bid, ok=True, chunks=len(all_chunks),
                            parser=parsed_book.parser or "docling",
                            pages=seen_pages)
    except Exception as exc:
        library.set_book_error(bid, str(exc))
        library.save()
        return IngestReport(book_id=bid, ok=False, error=str(exc))


def _plan(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in _EXTS
    )


def ingest_dir(directory: str | Path, *, library: Library, store: IndexStore,
               subject: str = "math", profile: str | None = None,
               embedder_spec: tuple = ("auto",), force: bool = False,
               jobs: int = 1, chunk_profile: ChunkProfile | None = None,
               page_range: str | None = None,
               figures_registry=None, log: Callable[[str], None] | None = None,
               on_report: Callable[[IngestReport], None] | None = None) -> BatchReport:
    """클라이언트 진입점 — 폴더 배치 인제스트."""
    dirp = Path(directory)
    if not dirp.is_dir():
        raise ValueError(f"not a directory: {directory}")

    ingested: list[IngestReport] = []
    skipped: list[str] = []
    failed: list[IngestReport] = []
    tasks: list[Path] = []

    # ① 책 등록/스킵 결정 (재개 관리)
    for p in _plan(dirp):
        bid = slugify(p.stem)
        try:
            book = library.book(bid)
        except KeyError:
            library.add_book(bid, p.stem, subject, source=p.name, parser=profile,
                             page_range=page_range)
            book = library.book(bid)
        if book.status == INDEXED and not force:
            skipped.append(bid)
        else:
            tasks.append(p)
    library.save()

    # ② 실행 (병렬 or 직렬) → 책 단위 완료 즉시 커밋 + 콜백(진행 가시화)
    if jobs > 1 and len(tasks) > 1:
        # 워커가 몇 개든 총 스레드 ≤ CPU 논리스레드 가 되도록 배분
        cpus = os.cpu_count() or 1
        thr = max(1, cpus // jobs)
        payloads = []
        for p in tasks:
            bid = slugify(p.stem)
            payloads.append(_task(p, bid, _resolve_parser(library, bid, profile),
                                  _resolve_profile(library, bid, chunk_profile),
                                  embedder_spec, thr,
                                  _book_page_range(library, bid)))
        # 컨테이너(pid1·멀티스레드)에서 fork 경고/데드락 회피: STUDY_MP_START=spawn
        ctx = multiprocessing.get_context(os.environ.get("STUDY_MP_START"))
        with ctx.Pool(processes=jobs) as pool:
            for res in pool.imap_unordered(_worker_ingest, payloads):
                report = _finalize(res, library, store)
                (ingested if report.ok else failed).append(report)
                if on_report:
                    on_report(report)
    else:
        # 순차 경로: 워커용 캡을 안 거치므로 compose 의 OMP 캡(2)이 남아
        # bge-m3 가 2스레드로 도는 문제 → 전체 코어로 재설정 후 embedder 생성
        # (torch 는 embedder 생성 시 lazy import → env 설정이 유효)
        _cap_worker_threads(os.cpu_count() or 1)
        embedder = _make_embedder(embedder_spec)
        for p in tasks:
            bid = slugify(p.stem)
            report = ingest_one(p, library=library, store=store, embedder=embedder,
                                profile=profile, book_id=bid,
                                chunk_profile=chunk_profile,
                                figures_registry=figures_registry, log=log)
            (ingested if report.ok else failed).append(report)
            if on_report:
                on_report(report)
    return BatchReport(ingested=tuple(ingested), skipped=tuple(skipped),
                       failed=tuple(failed))


def _finalize(res: dict, library: Library, store: IndexStore) -> IngestReport:
    """워커 결과를 커밋하고 IngestReport 로 반환한다 (책 단위)."""
    bid = res["book_id"]
    if res["ok"]:
        _commit_ok(library, store, bid, res["chunks"], res["vectors"],
                   res["parser"], res["pages"])
        return IngestReport(book_id=bid, ok=True, chunks=len(res["chunks"]),
                            parser=res["parser"], pages=res["pages"])
    library.set_book_error(bid, res["error"])
    library.save()
    return IngestReport(book_id=bid, ok=False, error=res["error"])
