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
        from docling.datamodel.pipeline_options import (
            CodeFormulaVlmOptions,
            OcrMode,
            PdfPipelineOptions,
            RapidOcrOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        log.info("Initializing Docling (first call downloads layout + formula models) ...")
        opts = PdfPipelineOptions()

        # Formula enrichment: decode display equations to LaTeX. The default
        # "codeformulav2" preset is a local VLM specialised for code/formulas;
        # "granite_docling" (258M) is a lighter alternative. The model is
        # downloaded on first use into the HF cache (volume-mounted).
        opts.do_formula_enrichment = True
        preset = os.environ.get("DOCLING_FORMULA_PRESET", "codeformulav2").strip().lower()
        opts.code_formula_options = CodeFormulaVlmOptions.from_preset(preset)

        # OCR: RapidOCR replaces/supplements the programmatic text layer. Many
        # textbook PDFs embed math with a broken glyph encoding ("f s 2 x d"
        # instead of f(2x)) — OCR recovers it. default = PDF-aware layout
        # regions (fast); full_page = force OCR over the whole page (slower,
        # best recovery for inline math).
        opts.do_ocr = True
        ocr_mode = os.environ.get("DOCLING_OCR_MODE", "default").strip().lower()
        opts.ocr_options = RapidOcrOptions(
            lang=[os.environ.get("DOCLING_OCR_LANG", "en").strip()],
            mode=OcrMode.FULL_PAGE if ocr_mode == "full_page" else OcrMode.DEFAULT,
        )

        # Heading level inference (PDF bookmarks/numbering/font style) so the
        # exported markdown gets proper #/##/### nesting instead of flat ##.
        # Chunking is content-based and unaffected by heading levels.
        if os.environ.get("DOCLING_HEADING_HIERARCHY", "on").strip().lower() != "off":
            opts.heading_hierarchy_options.enabled = True
            opts.generate_parsed_pages = True  # needed for style-based inference

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
