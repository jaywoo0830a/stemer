# myLLM – Multi‑Agent Local AI System

This repository now implements the **multi‑agent architecture** described in `PLAN.md`. It supports the following model roles on a single 64 GB RAM server, loading models on‑demand:

| Role | Model | Approx. Size | Instances |
|------|-------|--------------|-----------|
| **Parser** | Qwen2.5‑7B‑Instruct (Q4_K_M) | ~5 GB | 1 |
| **Worker** | Qwen2.5‑7B‑Instruct (Q4_K_M) | ~5 GB | 4 |
| **Coder** | Qwen2.5‑Coder‑7B‑Instruct (Q4_K_M) | ~5 GB | 4 |
| **Setter** | Qwen2.5‑14B‑Instruct (Q5_K_M) | ~11 GB | 1 (on‑demand) |
| **Judge** | DeepSeek‑R1‑Distill‑Qwen‑14B (Q5_K_M) | ~11 GB | 1 (on‑demand) |
| **Reasoner** | DeepSeek‑R1‑Distill‑Qwen‑7B | ~5 GB | 1 |
| **Embedding** | bge‑small‑en‑v1.5 | ~0.5 GB | 1 (always loaded) |

The server now ships **environment files** for each model under `server/config/models/`. Use the existing `up.sh` script to start any model:

```bash
# Example: start the parser
bash server/scripts/up.sh parser
```

To start the **persistent (always-on) set** — the minimum needed for day-to-day work — run:

```bash
bash server/scripts/start_all.sh     # parser + worker1 + coder1 (on-demand 운영)
```

Heavy 14B models (Setter / Judge) are **mutually exclusive on-demand** — only one loads at a time:

```bash
bash server/scripts/start_heavy.sh setter   # or: judge
```

> Resource rationale (CPU-only 9700X, DDR5 64GB): decoding is **memory-bandwidth bound**,
> so running many duplicate servers does not increase total throughput — it only wastes RAM
> and threads. See `MODEL-ANALYSIS.md` for the full quantitative model.

## New Files
- `MODEL-ANALYSIS.md` – CPU-only resource model (memory-bandwidth bound decoding) + gap analysis.
- `model_registry.py` – Python dictionary describing the model registry (useful for orchestration code).
- `server/config/models/*.env` – Environment files for each model instance (parser, workers, coders, reasoner).
- `server/scripts/start_all.sh` – Start the always-on set (parser + worker1 + coder1).
- `server/scripts/start_heavy.sh` – Start a 14B model (setter|judge) with mutual exclusion.

## How It Works
1. **Base environment** (`server/config/env`) defines shared variables (MODEL_DIR, LLAMA_CPP_DIR, etc.).
2. Each model has its own `.env` file with a unique `LLAMA_PORT` and `MODEL_NAME`.
3. `up.sh` loads the base env, then the model‑specific env via `load_model_env`, and starts `llama-server` in the background.
4. `start_all.sh` iterates over the model slugs defined in the script and calls `up.sh` for each.

## Next Steps
- Implement a **FastAPI orchestrator** that watches a request queue, loads/unloads models on demand, and routes work to the appropriate model server.
- Add **RAG** pipelines (FAISS/Chroma) and integrate the embedding model.
- Update the client configuration to point to the appropriate model endpoints.

---

For detailed setup instructions, see `server/README.md`.
