#!/usr/bin/env python3
"""Minimal web API for the study RAG pipeline (FastAPI + uvicorn).

Serves the plain-HTML UI behind nginx:
  - PDF upload into books/inbox/ (the watcher indexes it on the next pass)
  - agent instructions (AGENTS.md content, stored in registry.db)
  - topic CRUD
  - pipeline status + log tail
  - generated notes as a ZIP download

Run: python tools/api.py   (binds 127.0.0.1:8001; nginx proxies /api/)
"""
from __future__ import annotations

import io
import logging
import os
import zipfile
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from rag import registry
from rag.config import settings

log = logging.getLogger("rag")

app = FastAPI(title="study-rag API")


class AgentsPayload(BaseModel):
    content: str


class TopicPayload(BaseModel):
    topic: str
    book: str = ""
    section: str = ""
    note: str = ""
    kind: str = "note"


class TopicUpdatePayload(BaseModel):
    status: str | None = None
    kind: str | None = None


class StatusPayload(BaseModel):
    status: str


def _log_tail(n: int = 40) -> str:
    path = settings.logs_dir / "pipeline.log"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-n:])


def _llama_healthy() -> bool:
    try:
        resp = httpx.get(settings.llama_base_url.rstrip("/") + "/health", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    notes = sorted(p.name for p in settings.notes_dir.glob("*.md")) \
        if settings.notes_dir.exists() else []
    problems = sorted(p.name for p in settings.problems_dir.glob("*.md")) \
        if settings.problems_dir.exists() else []
    return {
        "llama_healthy": _llama_healthy(),
        "books": [
            {"book_id": b.book_id, "title": b.title, "author": b.author}
            for b in registry.list_books()
        ],
        "topics": [t.__dict__ for t in registry.list_topics()],
        "notes": notes,
        "problems": problems,
        "log_tail": _log_tail(),
    }


@app.post("/api/books/upload")
async def upload_book(file: UploadFile):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="only .pdf files are accepted")
    settings.ensure_dirs()
    name = Path(file.filename).name
    dest = settings.books_inbox / name
    dest.write_bytes(await file.read())
    log.info("Uploaded %s -> books/inbox/", name)
    return {
        "saved": name,
        "message": "PDF queued. The watcher indexes it on the next pass.",
    }


@app.get("/api/agents")
def get_agents():
    return {"content": registry.get_doc("agents") or ""}


@app.post("/api/agents")
def set_agents(payload: AgentsPayload):
    registry.set_doc("agents", payload.content)
    log.info("Agent instructions updated.")
    return {"saved": True}


@app.get("/api/topics")
def list_topics():
    return [t.__dict__ for t in registry.list_topics()]


@app.post("/api/topics")
def add_topic(payload: TopicPayload):
    registry.add_topic(
        payload.topic, book=payload.book, section=payload.section,
        note=payload.note, kind=payload.kind,
    )
    log.info(
        "Topic added: %s (book=%s, section=%s, kind=%s)",
        payload.topic, payload.book, payload.section, payload.kind,
    )
    return {"saved": True}


@app.patch("/api/topics/{topic}")
def set_topic(topic: str, payload: TopicUpdatePayload):
    fields = {}
    if payload.status is not None:
        fields["status"] = payload.status
    if payload.kind is not None:
        fields["kind"] = payload.kind
    if not fields or not registry.update_topic(topic, **fields):
        raise HTTPException(status_code=404, detail="topic not found or nothing to update")
    return {"saved": True}


@app.get("/api/notes")
def list_notes():
    if not settings.notes_dir.exists():
        return []
    return sorted(p.name for p in settings.notes_dir.glob("*.md"))


@app.get("/api/problems")
def list_problems():
    if not settings.problems_dir.exists():
        return []
    return sorted(p.name for p in settings.problems_dir.glob("*.md"))


@app.get("/api/problems/{name}")
def get_problem_file(name: str):
    safe = Path(name).name
    p = settings.problems_dir / safe
    if not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return Response(p.read_text(encoding="utf-8"), media_type="text/markdown")


@app.get("/api/notes/download")
def download_notes_zip():
    entries: list[tuple[str, Path]] = []
    if settings.notes_dir.exists():
        entries += [(p.name, p) for p in settings.notes_dir.glob("*.md")]
    if settings.problems_dir.exists():
        entries += [(f"problems/{p.name}", p) for p in settings.problems_dir.glob("*.md")]
    if not entries:
        raise HTTPException(status_code=404, detail="no notes generated yet")
    entries.sort(key=lambda e: e[0])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, p in entries:
            zf.writestr(arcname, p.read_text(encoding="utf-8"))
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="study-notes.zip"'},
    )


@app.get("/api/notes/{name}")
def get_note(name: str):
    safe = Path(name).name
    p = settings.notes_dir / safe
    if not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return Response(p.read_text(encoding="utf-8"), media_type="text/markdown")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings.ensure_dirs()
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("API_PORT", "8001")))
