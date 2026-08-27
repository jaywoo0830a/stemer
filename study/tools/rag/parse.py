"""PDF -> Markdown conversion via Docling.

Slow but one-time: the result is cached in books/markdown/ so re-indexing
never re-parses the PDF (unless --force is used).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import settings

log = logging.getLogger("rag")

_converter = None


def get_converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter

        log.info("Initializing Docling (first call downloads layout models) ...")
        _converter = DocumentConverter()
    return _converter


def parse_pdf(pdf_path: Path, force: bool = False) -> str:
    """Convert one PDF to markdown, cached on disk."""
    md_path = settings.books_markdown / (pdf_path.stem + ".md")
    if md_path.exists() and not force:
        log.info("Using cached markdown %s", md_path.name)
        return md_path.read_text(encoding="utf-8")

    log.info("Parsing %s with Docling (tens of minutes per book is normal) ...", pdf_path.name)
    t0 = time.time()
    result = get_converter().convert(str(pdf_path))
    md = result.document.export_to_markdown()
    md_path.write_text(md, encoding="utf-8")
    log.info(
        "Parsed %s -> %s in %.1f min (%d chars)",
        pdf_path.name, md_path.name, (time.time() - t0) / 60, len(md),
    )
    return md
