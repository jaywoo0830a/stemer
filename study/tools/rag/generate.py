"""Call the local llama-server to write a grounded study note."""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from pathlib import Path

import httpx

from . import registry
from .config import settings
from .store import Hit

log = logging.getLogger("rag")

_FORBIDDEN_ENV_RE = re.compile(
    r"\\begin\{(align|equation|eqnarray|gather|split|proof|theorem|lemma|corollary)\*?\}"
)
_FORBIDDEN_MACROS = (r"\bm", r"\mathds", r"\mathbbm")

_DEFAULT_AGENTS = """# Study note conventions (fallback — study/AGENTS.md takes precedence)
- English only (US English); one topic, about one page (~400-900 words). No deep proofs.
- Textbook-first: every definition/notation must match the textbook exactly;
  always link to the textbook chapter/section (e.g. "see §3.5"); never guess section numbers.
- KaTeX only: allowed environments aligned/cases/matrix/pmatrix/bmatrix/vmatrix/array/alignedat/smallmatrix;
  forbidden: align/equation/eqnarray/gather/split/proof/theorem;
  use \\operatorname and \\boldsymbol (not \\bm), \\mathrm{d}; never \\mathds.
- Display math only inside $$...$$, inline math inside $...$.
- Status lifecycle: draft -> review -> done.
"""

_DEFAULT_TEMPLATE = """# <Topic name>

## Motivation
...

## Key definitions
...

## Main idea
...

## Formulas
$$ ... $$

## Link to textbook
See §... .

## Checkpoints
1. ...
"""


def lint_katex(text: str) -> list[str]:
    """Cheap inline KaTeX policy lint (forbidden environments / macros)."""
    problems = []
    for m in _FORBIDDEN_ENV_RE.finditer(text):
        problems.append(f"forbidden environment \\begin{{{m.group(1)}}}")
    for macro in _FORBIDDEN_MACROS:
        if re.search(re.escape(macro) + r"(?![A-Za-z])", text):
            problems.append(f"forbidden macro {macro}")
    return problems


def read_or_fallback(path: Path, fallback: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return fallback


def _doc_content(key: str, fallback_path: Path, fallback_text: str) -> str:
    """Prompt doc from the registry DB, falling back to the file on disk."""
    try:
        content = registry.get_doc(key)
        if content:
            return content
    except Exception as exc:  # registry trouble must never block generation
        log.warning("Registry read failed (%s) — using file fallback.", exc)
    return read_or_fallback(fallback_path, fallback_text)


def build_messages(
    topic: str,
    book_title: str,
    book_id: str,
    section_refs: str,
    hits: list[Hit],
) -> list[dict]:
    agents_md = _doc_content("agents", settings.agents_file, _DEFAULT_AGENTS)
    template_md = _doc_content("template", settings.template_file, _DEFAULT_TEMPLATE)

    excerpts = "\n\n".join(
        f"### [{h.chapter} | {h.section}] ({h.book_id})\n{h.text}" for h in hits
    )

    system = (
        "You are an expert writer of warm-up study notes. Follow the AGENTS rules"
        " below EXACTLY.\n\n"
        "<AGENTS>\n" + agents_md + "\n</AGENTS>\n\n"
        "<TEMPLATE>\n" + template_md + "\n</TEMPLATE>\n\n"
        "Output ONLY the finished note as Markdown. No commentary outside the note."
    )

    user = (
        f"Topic: {topic}\n"
        f"Book: {book_title} (book_id: {book_id})\n"
        f"Primary textbook sections: {section_refs or '(not specified)'}\n\n"
        "Use ONLY the textbook excerpts below as ground truth for definitions,"
        " notation and section references. Never invent facts that are not in the"
        " excerpts. If something is missing, write 'see the textbook' instead of"
        " guessing.\n\n"
        "<EXCERPTS>\n" + (excerpts or "(no excerpts available)") + "\n</EXCERPTS>\n\n"
        "Write the note now."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def call_llama(messages: list[dict]) -> str:
    url = settings.llama_base_url.rstrip("/") + "/chat/completions"
    headers: dict[str, str] = {}
    if settings.llama_api_key:
        headers["Authorization"] = f"Bearer {settings.llama_api_key}"

    body: dict = {
        "model": "local",  # llama-server serves whatever model is loaded
        "messages": messages,
        "temperature": settings.temperature,
        "top_p": settings.top_p,
        "top_k": settings.top_k,
        "max_tokens": settings.max_tokens,
        "stream": False,
    }
    effort = (settings.reasoning_effort or "").strip().lower()
    if effort and effort != "off":
        body["chat_template_kwargs"] = {"thinking": "on", "reasoning_effort": effort}

    log.info("Calling %s (effort=%s, max_tokens=%d) ...", url, effort, settings.max_tokens)
    t0 = time.time()
    with httpx.Client(timeout=settings.request_timeout_s) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    if not content:
        raise RuntimeError(f"Empty response from llama-server: {str(data)[:200]}")
    log.info("Generation finished in %.1f min (%d chars).", (time.time() - t0) / 60, len(content))
    return content


def slugify(topic: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return s or "note"


def save_note(
    topic: str,
    book_id: str,
    section_refs: str,
    content: str,
    out_path: str | None = None,
) -> Path:
    path = Path(out_path) if out_path else (settings.notes_dir / f"{slugify(topic)}.md")
    if not path.is_absolute():
        path = settings.study_root / path
    path.parent.mkdir(parents=True, exist_ok=True)

    today = dt.date.today().isoformat()
    frontmatter = (
        f"---\ntopic: {topic}\nbook: {book_id}\nsections: {section_refs}\n"
        f"status: draft\ngenerated: {today}\n---\n\n"
    )
    path.write_text(frontmatter + content.strip() + "\n", encoding="utf-8")

    if settings.katex_lint.lower() != "off":
        problems = lint_katex(content)
        if problems:
            log.warning("KaTeX lint issues in %s: %s", path.name, "; ".join(problems))
    return path
