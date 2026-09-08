# Local AI Service Suite

This directory contains a **FastAPI** implementation of the four micro‑services defined in `api_contract.yaml`:

* `POST /parse`   – parses a source file into markdown.
* `POST /embed`   – converts a list of texts into dense vectors.
* `POST /retrieve` – retrieves the nearest chunks for a query vector.
* `POST /generate` – generates a JSON‑structured payload for a topic.

The implementation re‑uses the existing `study_lib` package, so the same logic that the original pipeline uses is available via HTTP.

## Quick start
```bash
# 1️⃣ Install dependencies (inside the repository root)
python3 -m venv .venv
source .venv/bin/activate
pip install -r brain/requirements.txt

# 2️⃣ Run the service (default port 8000)
uvicorn brain.services:app --host 0.0.0.0 --port 8000
```

You can now call the endpoints with any HTTP client (curl, httpie, Postman, generated client code, …). The OpenAPI specification is stored in `brain/api_contract.yaml`; tools such as `openapi-generator` can generate language‑specific client stubs directly from that file.

## Example curl commands
```bash
# Parse
curl -X POST http://localhost:8000/parse \
  -H "Content-Type: application/json" \
  -d '{"path":"/absolute/path/to/book.md","profile":"text","book_id":"book1"}'

# Embed
curl -X POST http://localhost:8000/embed \
  -H "Content-Type: application/json" \
  -d '{"texts":["First sentence.","Second sentence."]}'

# Retrieve
curl -X POST http://localhost:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{"vector":[0.1,0.2,0.3],"k":5}'

# Generate
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"topic":{"topic_id":"t1","book_id":"book1","title":"Demo","kind":"exam"},"context":[],"schema":{},"json_object":true}'
```

## Notes
* The service uses **StubEmbedder** (a deterministic hash‑based embedder). Replace it with `TransformerEmbedder` if you have a real model.
* `generate_one` is called with a **dummy LLM** that returns a minimal payload satisfying the schema. Swap `DummyLLM` for `FlashClient`, `LocalClient`, etc., to use a real LLM.
* All data (index, registry, notes) is stored under `brain/` (`store/`, `registry.json`, `notes/`).

## Multi‑service deployment
If you want each micro‑service on its own port (as described in NEW‑METHOD.md), you can split `services.py` into four separate FastAPI apps and run them with Docker‑Compose. The OpenAPI contract (`api_contract.yaml`) stays the same, so client code does not need to change.
