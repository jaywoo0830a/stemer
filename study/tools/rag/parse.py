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
        import os

        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        log.info("Initializing Docling (first call downloads layout models) ...")
        opts = PdfPipelineOptions()
        # Keep page/element rasters so figures can be cropped & saved
        # (official export_figures.py pattern).
        opts.generate_page_images = True
        opts.generate_picture_images = True
        opts.images_scale = float(os.environ.get("DOCLING_IMAGES_SCALE", "2.0"))
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    return _converter


def parse_pdf(pdf_path: Path, force: bool = False) -> str:
    """Convert one PDF to markdown (cached) and extract figures (cached)."""
    from . import figures

    md_path = settings.books_markdown / (pdf_path.stem + ".md")
    fig_meta = figures.metadata_path(pdf_path.stem)
    if md_path.exists() and fig_meta.exists() and not force:
        log.info("Using cached markdown %s", md_path.name)
        return md_path.read_text(encoding="utf-8")

    log.info("Parsing %s with Docling (tens of minutes per book is normal) ...", pdf_path.name)
    t0 = time.time()
    result = get_converter().convert(str(pdf_path))
    md = result.document.export_to_markdown()
    md_path.write_text(md, encoding="utf-8")
    if settings.figures_enabled.lower() != "off":
        try:
            figures.extract_figures(result.document, pdf_path.stem)
        except Exception:
            log.exception("Figure extraction failed for %s", pdf_path.name)
    log.info(
        "Parsed %s -> %s in %.1f min (%d chars)",
        pdf_path.name, md_path.name, (time.time() - t0) / 60, len(md),
    )
    return md
