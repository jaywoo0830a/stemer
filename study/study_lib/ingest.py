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

EMBED_LOG_EVERY = 50   # 임베딩 진행 로그 주기(청크 수) — CPU 인코딩은 느려 200은 너무 듬성
EMBED_BATCH = 32        # 워커당 임베딩 배치


def _embed_with_progress(embedder, texts: list, label: str,
                         log: Callable[[str], None] | None) -> list:
    """배치 단위 임베딩 + 진행 로그(경과시간 포함) — 멈춤 vs 느림 구분용."""
    out: list = []
    total = len(texts)
    t0 = time.monotonic()
    if log:
        log(f"[{label}] embed 0/{total}")
    for start in range(0, total, EMBED_BATCH):
        end = min(start + EMBED_BATCH, total)
        out.extend(embedder.embed_texts(texts[start:end], batch_size=EMBED_BATCH))
        if log and (end == total or end % EMBED_LOG_EVERY == 0):
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
          threads: int) -> dict:
    return {"path": str(path), "book_id": book_id, "profile": profile,
            "chunk_profile": chunk_profile, "embedder_spec": embedder_spec,
            "threads": threads}


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


def _worker_ingest(payload: dict) -> dict:
    """워커: 파싱→청킹→임베딩만 수행(진행 로그 출력). 저장/registry 는 부모가 한다."""
    bid = payload["book_id"]
    # torch/OMP 스레드 상한을 import 전에 보장 (oversubscription → livelock 방지)
    _cap_worker_threads(payload.get("threads") or 1)
    try:
        parsed = parse_source(payload["path"], profile=payload["profile"], book_id=bid)
        _guard_quality(parsed)
        chunks = chunk_markdown(parsed.markdown, book_id=bid,
                                profile=payload["chunk_profile"])
        if not chunks:
            raise ValueError(f"no chunks extracted from "
                             f"{Path(payload['path']).name} (scanned PDF? use profile=docling)")
        print(f"[{bid}] parse done pages={parsed.pages} chunks={len(chunks)}", flush=True)
        embedder = _make_embedder(payload["embedder_spec"])
        vectors = _embed_with_progress(embedder, [c.text for c in chunks], bid, print)
        print(f"[{bid}] embed done ({len(chunks)} chunks)", flush=True)
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
        parsed = parse_source(p, profile=profile, book_id=bid)
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


def _plan(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in _EXTS
    )


def ingest_dir(directory: str | Path, *, library: Library, store: IndexStore,
               subject: str = "math", profile: str | None = None,
               embedder_spec: tuple = ("auto",), force: bool = False,
               jobs: int = 1, chunk_profile: ChunkProfile | None = None,
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
            library.add_book(bid, p.stem, subject, source=p.name)
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
            payloads.append(_task(p, bid, profile,
                                  _resolve_profile(library, bid, chunk_profile),
                                  embedder_spec, thr))
        # 컨테이너(pid1·멀티스레드)에서 fork 경고/데드락 회피: STUDY_MP_START=spawn
        ctx = multiprocessing.get_context(os.environ.get("STUDY_MP_START"))
        with ctx.Pool(processes=jobs) as pool:
            for res in pool.imap_unordered(_worker_ingest, payloads):
                report = _finalize(res, library, store)
                (ingested if report.ok else failed).append(report)
                if on_report:
                    on_report(report)
    else:
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
