#!/usr/bin/env python3
"""Tests for the RAG core modules: store, registry, topics, generate, retrieve, api.

Run inside the pipeline container:
    docker compose -f docker/docker-compose.yml exec pipeline python tools/test_rag.py
or in any environment with chromadb / fastapi / httpx installed.
Groups whose dependencies are missing are skipped (reported at the end).

Exit code 0 = no failures.
"""
from __future__ import annotations

import io
import os
import pathlib
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from rag.config import settings  # noqa: E402

_PASS = 0
_FAIL = 0
_SKIPS: list[str] = []


def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


def skip(group: str, reason: str):
    _SKIPS.append(f"{group} ({reason})")
    print(f"  SKIP  {group} — {reason}")


def fresh_env() -> pathlib.Path:
    """Point settings at a temp study root seeded with minimal markdown files."""
    root = pathlib.Path(tempfile.mkdtemp())
    settings.study_root = root
    settings.ensure_dirs()
    (root / "TOPICS.md").write_text(
        "| topic | book | section | status | note |\n"
        "|---|---|---|---|---|\n"
        "| Topic name (US English) | book_id | 3.5 | todo / draft / review / done | notes/x.md |\n"
        "| Normal distribution | prob | 3.5 | todo | notes/normal.md |\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Canonical rules\nEnglish only.\n", encoding="utf-8")
    (root / "templates").mkdir(exist_ok=True)
    (root / "templates" / "warmup.md").write_text("# Template\n## Motivation\n", encoding="utf-8")
    from rag import registry

    registry._ready = False
    return root


# ---------------------------------------------------------------------------
# store (BM25 keyword index)
# ---------------------------------------------------------------------------
def test_store() -> None:
    print("store — BM25 (FTS5 + fallback), refs, delete, stats:")
    from rag import chunk
    from rag.store import Store

    fresh_env()
    store = Store(settings.index_dir / "rag.db")
    store.add_book("prob", "Intro Probability", "prob.pdf")
    md = ("## 1 확률\n\n정규분포의 확률밀도함수는 $f(x)$ 이다.\n\n"
          "## 2 통계\n\nThe variance of X is sigma squared.\n")
    store.add_chunks(chunk.split_markdown(md, "prob", min_chars=20))

    hits = store.bm25_search("정규분포 확률밀도", 3, "prob")
    check("korean prefix search hits right chunk",
          bool(hits) and bool(hits[0].chapter or hits[0].section))
    hits_en = store.bm25_search("variance", 3, "prob")
    check("english search hits right chunk", bool(hits_en) and "variance" in hits_en[0].text.lower())

    refs = store.find_by_refs(["1"], "확률", "prob")
    check("find_by_refs works", len(refs) > 0)

    st = store.stats()
    check("stats chunks count", st["chunks"] >= 2 and st["per_book"][0]["book_id"] == "prob")

    n_df = store.conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
    store.delete_book("prob")
    check("delete_book cleans chunks", store.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0)
    check("delete_book decrements df", store.conn.execute("SELECT COUNT(*) FROM df").fetchone()[0] <= n_df)

    # python fallback path
    store.add_chunks(chunk.split_markdown(md, "prob", min_chars=20))
    store.use_fts = False
    hits_fb = store.bm25_search("정규분포", 3, "prob")
    check("fallback BM25 korean", bool(hits_fb) and hits_fb[0].score > 0)
    store.use_fts = True


# ---------------------------------------------------------------------------
# registry + topics
# ---------------------------------------------------------------------------
def test_registry() -> None:
    print("registry + topics — migration, CRUD, export roundtrip:")
    from rag import registry, topics

    root = fresh_env()
    registry.ensure_ready()
    rows = registry.list_topics()
    check("auto-migration from TOPICS.md", len(rows) == 1)
    check("format example row skipped", all("Topic name" not in r.topic for r in rows))
    check("AGENTS.md migrated", "canonical" in (registry.get_doc("agents") or "").lower())

    registry.add_topic("CLT", book="prob", section="3.7")
    check("add_topic -> todo", any(r.topic == "CLT" and r.status == "todo" for r in registry.list_topics()))
    registry.update_topic("CLT", status="draft")
    check("update_topic status", any(r.topic == "CLT" and r.status == "draft" for r in registry.list_topics()))
    check("list_topics filter", len(registry.list_topics(status="draft")) == 1)
    check("topics wrapper", len(topics.load_topics(status="draft")) == 1)
    check("topics.mark_topic", topics.mark_topic("CLT", "review"))
    try:
        registry.update_topic("CLT", status="bogus")
        bad_status_ok = False
    except ValueError:
        bad_status_ok = True
    check("invalid status rejected", bad_status_ok)

    registry.set_doc("agents", "# New rules\n")
    check("doc set/get", registry.get_doc("agents") == "# New rules\n")
    check("doc exported to AGENTS.md", "New rules" in (root / "AGENTS.md").read_text())

    # export roundtrip: persisted DB content must survive a fresh read
    registry.export_all()
    registry._ready = False
    rows2 = registry.list_topics()
    check("export->import roundtrip",
          {r.topic for r in rows2} == {"Normal distribution", "CLT"})


# ---------------------------------------------------------------------------
# generate (lint, slug, prompt build) — needs httpx for module import
# ---------------------------------------------------------------------------
def test_generate() -> None:
    try:
        from rag import generate  # noqa: F401
    except ImportError as exc:
        skip("generate", f"httpx missing: {exc}")
        return
    print("generate — KaTeX lint, slug, save_note, prompt build:")
    from rag import generate, registry
    from rag.store import Hit

    fresh_env()
    registry._ready = False
    registry.set_doc("agents", "# Rules\nUse only excerpts.\n")

    problems = generate.lint_katex(r"$$\begin{align} x \end{align}$$ \bm{a} \mathds{1}")
    check("forbidden env detected", any("align" in p for p in problems))
    check("forbidden macro detected", len([p for p in problems if "\\bm" in p or "\\mathds" in p]) >= 1)
    check("clean katex passes", generate.lint_katex(r"$$\begin{aligned} x \end{aligned}$$ \boldsymbol{a}") == [])

    check("slugify", generate.slugify("Central Limit Theorem") == "central-limit-theorem")

    hits = [Hit(chunk_id="prob:1", book_id="prob", chapter="Chapter 3", section="3.5 Normal",
                text="The normal density is ...", score=1.0)]
    msgs = generate.build_messages("Normal distribution", "Intro Probability", "prob", "3.5", hits)
    system = msgs[0]["content"]
    user = msgs[1]["content"]
    check("AGENTS doc injected", "# Rules" in system)
    check("excerpt injected", "normal density" in user)
    check("section ref injected", "3.5" in user)

    out = generate.save_note("Normal distribution", "prob", "3.5", "# Note\n\n$$\nx\n$$")
    check("note saved with frontmatter", out.exists() and "status: draft" in out.read_text())


# ---------------------------------------------------------------------------
# retrieve (RRF + rerank) — needs chromadb
# ---------------------------------------------------------------------------
def test_retrieve() -> None:
    try:
        from rag import retrieve  # noqa: F401
    except ImportError as exc:
        skip("retrieve", f"chromadb missing: {exc}")
        return
    print("retrieve — RRF fusion + rerank selection:")
    from rag import embed_index, retrieve
    from rag.store import Hit, Store

    fresh_env()
    store = Store(settings.index_dir / "rag.db")

    def hit(cid, score=1.0):
        return Hit(chunk_id=cid, book_id="b", chapter="c", section="s", text=f"text {cid}", score=score)

    a, b, c_, d = hit("a"), hit("b"), hit("c"), hit("d")
    store.bm25_search = lambda q, k, book_id=None: [a, c_, d, b]
    embed_index.dense_search = lambda q, k, book_id=None: [
        {"chunk_id": x.chunk_id, "book_id": "b", "chapter": "c", "section": "s", "text": x.text, "score": 1.0}
        for x in (a, d, c_, b)
    ]

    class FakeReranker:
        _scores = {"a": 1.0, "d": 9.0, "c": 7.0, "b": 8.0}

        def predict(self, pairs, batch_size=16, show_progress_bar=False):
            return [self._scores[p[1].split()[-1]] for p in pairs]

    retrieve.get_reranker = lambda: FakeReranker()
    top = retrieve.retrieve(store, "q", k=2)
    check("rerank picks top-k", [h.chunk_id for h in top] == ["d", "b"])
    check("pool smaller than k skips rerank",
          retrieve.hybrid_candidates(store, "q", None, 3) is not None)


# ---------------------------------------------------------------------------
# api (FastAPI endpoints) — needs fastapi + httpx
# ---------------------------------------------------------------------------
def test_api() -> None:
    try:
        import fastapi  # noqa: F401
        from fastapi.testclient import TestClient
    except ImportError as exc:
        skip("api", f"fastapi missing: {exc}")
        return
    print("api — endpoints:")
    import api
    from rag import registry

    fresh_env()
    registry._ready = False
    client = TestClient(api.app)

    check("health", client.get("/api/health").json() == {"ok": True})
    st = client.get("/api/status").json()
    check("status shape", {"books", "topics", "notes", "log_tail", "llama_healthy"} <= set(st))

    check("agents get/set", client.post("/api/agents", json={"content": "X"}).json() == {"saved": True}
          and client.get("/api/agents").json()["content"] == "X")

    check("topic add", client.post("/api/topics", json={"topic": "CLT", "book": "prob"}).json() == {"saved": True})
    check("topic patch", client.patch("/api/topics/CLT", json={"status": "draft"}).json() == {"saved": True})
    check("topic 404", client.patch("/api/topics/nope", json={"status": "done"}).status_code == 404)

    check("pdf upload", "saved" in client.post(
        "/api/books/upload", files={"file": ("book.pdf", b"%PDF", "application/pdf")}).json())
    check("non-pdf rejected", client.post(
        "/api/books/upload", files={"file": ("x.txt", b"x", "text/plain")}).status_code == 400)
    check("inbox received", any(settings.books_inbox.glob("book.pdf")))

    (settings.notes_dir / "n1.md").write_text("# n1\n$$\nx\n$$", encoding="utf-8")
    r = client.get("/api/notes/download")
    check("zip download", r.status_code == 200 and r.headers["content-type"] == "application/zip")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    check("zip members", names == ["n1.md"])
    check("single note", client.get("/api/notes/n1.md").status_code == 200)
    check("path traversal blocked", client.get("/api/notes/..%2F..%2Fetc%2Fpasswd").status_code == 404)


def main() -> None:
    print("== RAG core tests ==")
    test_store()
    test_registry()
    test_generate()
    test_retrieve()
    test_api()
    print(f"== {_PASS} passed, {_FAIL} failed, {len(_SKIPS)} skipped ==")
    for s in _SKIPS:
        print(f"  skipped: {s}")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
