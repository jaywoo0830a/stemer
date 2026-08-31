# study pipeline — architecture

Functional, phase-based pipeline. The RAG indexing phases and the LLM
generation phase are **mutually exclusive**: on the shared 64 GB server the
27B llama-server is only ever running during generation, never while
indexing. The host shell script `study/pipeline.sh` is the single scheduler;
the container exposes one unified CLI (`tools/study.py`) with small,
pure-ish, testable commands.

## Phases

| Phase | Work | LLM | Owner |
|---|---|---|---|
| A · index | Docling parse → chunk → BM25 + vector index (books/inbox → processed/) | **off** | `study.py index` |
| B · figures | figure extraction + descriptions (native docling VLM or custom VLM) | off | inside A |
| C · generate | retrieve → rerank → llama-server → notes/problems | **on (up.sh), then off (down.sh)** | `study.py generate` |

Rule: **선 RAG → 후 생성** — `generate` refuses to run while any inbox PDF is
not yet indexed (`pending_index > 0`).

## Process topology (host + container)

```
HOST                                CONTAINER (study/docker)
┌──────────────────────┐
│ pipeline.sh           │──run──▶  pipeline (run-once, unified CLI study.py)
│   watch|once|index    │
│   generate|prefetch|status│      llama-server: HOST only (up.sh/down.sh)
│   │  up.sh  (LLM on)  │
│   │  down.sh (LLM off)│
└──────────────────────┘
```

- `pipeline` image = the single run-once container (`restart: "no"`), driven by
  `pipeline.sh`. There is **no always-on container** — no web UI/API.
- llama-server lives on the HOST (`up.sh`/`down.sh` reuse PID file + health
  wait) and is started only around `generate`, then stopped to free ~30 GB.

## Functional design

- `tools/study.py` — the ONE CLI (merged old pipeline.py/manage.py/gen_note.py):
  phase commands (`index|generate|all|prefetch|note`), registry commands
  (`init|import|export|status|reset-all|books|topics|docs`), `reindex`.
  Phase functions return a frozen `PhaseReport`-style result and keep only
  `_failed_topics` as hidden state (same-process retry guard).
- **DB backend** (2026-08): Postgres 17 + pgvector (`db` compose service) when
  `DATABASE_URL` is set — pg_trgm lexical + HNSW vector in one DB. `DATABASE_URL`
  empty = legacy SQLite FTS5 (`rag.db`) + Chroma. Both backends expose the same
  Store interface (`rag.store.open_store()` picks the backend; `rag/pgstore.py`
  is the Postgres implementation).
- **Multi-core** (2026-08): `study.py index --jobs N` parses N PDFs concurrently
  via `multiprocessing` (fork) — parse/chunk run in workers, DB write + embed
  stay in the single parent (not fork-safe to share SQLite/Chroma/pgvector
  writers). ~4-6GB RAM per worker.
- Runtime: Python 3.14 image; dependencies locked in `requirements.lock`
  (`uv pip compile requirements.txt -o requirements.lock --python-version 3.14`).
- `rag/llm.py` — pure-ish LLM helpers: `is_healthy`, `wait_healthy`,
  `require_llm` (raises `LLMUnavailable`). Containers cannot exec host
  processes, so they only wait/assert; the start/stop *effects* live in
  `up.sh`/`down.sh`, driven by `pipeline.sh`.
- `rag/config.py` — the full environment surface (all options, incl. docling).
- Resume safety = `registry.db` status: todo → draft → review → done.

## CLI

```bash
# container (invoked by pipeline.sh, or directly)
python tools/study.py index        # Phase A+B only (LLM off)
python tools/study.py generate     # Phase C only (guard: pending_index == 0)
python tools/study.py prefetch     # download embedding/rerank models
python tools/study.py status       # one-view status; ends with pending_index=N pending_generate=M
python tools/study.py note TOPIC   # generate ONE topic immediately
python tools/study.py reindex BOOK_ID | reindex --all   # re-chunk from markdown cache
python tools/study.py books|topics|docs|init|import|export|reset-all

# host — the only scheduler that touches llama-server
bash study/pipeline.sh once           # index; if nothing left → generate (LLM up/down)
bash study/pipeline.sh watch          # loop `once` every WATCH_INTERVAL_S
bash study/pipeline.sh index|generate|prefetch|status [--force] [--book X]
```

`LLM_MANAGED=off` (study/docker/.env) → `pipeline.sh` never starts/stops the
server; you manage it with `./up.sh`/`./down.sh` manually.

## Daily lifecycle

1. Drop PDFs in `books/inbox/` (SCP — there is no upload UI).
2. `bash study/pipeline.sh watch` (nohup or a systemd timer):
   - index phase runs with LLM off (RAM free for docling/embedding),
   - once the inbox is empty and `todo` topics exist → `up.sh` (start 27B) →
     `generate` → `down.sh` (free ~30 GB).
3. Idle: no container is running; no llama-server is loaded (~30 GB free).
