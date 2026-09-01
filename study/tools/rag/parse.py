"""PDF -> Markdown conversion via Docling.

Slow but one-time: the result is cached in books/markdown/ so re-indexing
never re-parses the PDF (unless --force is used).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from .config import settings

log = logging.getLogger("rag")

_converter = None


class _DoclingProgressFilter(logging.Filter):
    """Keep docling's page/VLM progress lines; drop the rest of its DEBUG noise.

    Attached to the ROOT handlers — logger-level filters are NOT consulted
    during propagation, so they can't be used here. Non-docling (rag) records
    pass through untouched.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith("docling"):
            return True
        msg = record.getMessage()
        return ("Finished converting pages" in msg
                or "PIPELINE_PROFILING Stage" in msg
                or "Batch processed" in msg)


_PAGE_PROGRESS_INSTALLED = False


def install_page_progress() -> None:
    """Surface docling's per-batch page + VLM progress in the pipeline log.

    Raises docling to DEBUG so its progress lines are emitted, then filters at
    the ROOT handlers to keep only: per-batch page progress (page X/N, threaded
    stage batches) and VLM enrichment batches ("Batch processed N images ...").
    Everything else from docling stays hidden; our own rag logs pass through.
    """
    global _PAGE_PROGRESS_INSTALLED
    if _PAGE_PROGRESS_INSTALLED:
        return
    _PAGE_PROGRESS_INSTALLED = True
    logging.getLogger("docling").setLevel(logging.DEBUG)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, _DoclingProgressFilter) for f in handler.filters):
            handler.addFilter(_DoclingProgressFilter())


def _pdf_page_count(pdf_path: Path) -> int | None:
    """Total pages (for progress reporting), or None if it can't be read."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(pdf_path))
        n = len(pdf)
        pdf.close()
        return n
    except Exception:
        return None


def get_converter():
    global _converter
    install_page_progress()  # surface docling's per-batch page progress in the log
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            CodeFormulaVlmOptions,
            LayoutObjectDetectionOptions,
            OcrMode,
            PdfPipelineOptions,
            PictureDescriptionVlmEngineOptions,
            RapidOcrOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        log.info("Initializing Docling (first call downloads layout + formula models) ...")
        opts = PdfPipelineOptions()

        # Formula enrichment -> LaTeX (local VLM preset, config.docling_formula_preset).
        opts.do_formula_enrichment = True
        opts.code_formula_options = CodeFormulaVlmOptions.from_preset(settings.docling_formula_preset)

        # OCR: recover MathType-garbled inline math from the text layer
        # ("f s 2 x d" instead of f(2x)). default = PDF-aware regions (fast);
        # full_page = force OCR over the whole page (best recovery, slow).
        opts.do_ocr = True
        opts.ocr_options = RapidOcrOptions(
            lang=[settings.docling_ocr_lang],
            mode=OcrMode.FULL_PAGE if settings.docling_ocr_mode == "full_page" else OcrMode.DEFAULT,
        )

        # Heading level inference (bookmarks/numbering/font style) so the
        # exported markdown gets proper #/##/### nesting. Chunking is
        # content-based and unaffected by heading levels.
        if settings.docling_heading_hierarchy_enabled:
            opts.heading_hierarchy_options.enabled = True
            opts.generate_parsed_pages = True  # needed for style-based inference

        # Picture classification (always on), chart + code enrichment (knobs).
        opts.do_picture_classification = True
        if settings.docling_chart_extraction_enabled:
            opts.do_chart_extraction = True
        if settings.docling_code_enrichment_enabled:
            opts.do_code_enrichment = True

        # Native picture description — replaces the custom VLM when enabled
        # (see rag/figures.py + rag/vlm.py).
        if settings.native_picture_description:
            opts.do_picture_description = True
            opts.picture_description_options = PictureDescriptionVlmEngineOptions.from_preset(
                settings.docling_picture_preset
            )
            opts.picture_description_options.picture_area_threshold = settings.docling_picture_area_threshold

        # Layout model: "" = docling default (layout-heron); layout_egret_large
        # is more accurate on dense textbooks but much slower on CPU.
        if settings.docling_layout_preset:
            opts.layout_options = LayoutObjectDetectionOptions.from_preset(settings.docling_layout_preset)

        # Keep page/element rasters so figures can be cropped & saved.
        opts.generate_page_images = True
        opts.generate_picture_images = True
        opts.images_scale = settings.docling_images_scale
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    return _converter


def parse_pdf(pdf_path: Path, force: bool = False) -> str:
    """Convert one PDF to markdown (cached) and extract figures (cached)."""
    from . import figures

    md_path = settings.books_markdown / (pdf_path.stem + ".md")
    fig_meta = figures.metadata_path(pdf_path.stem)
    cache_ok = md_path.exists() and fig_meta.exists() and not force
    if settings.native_picture_description:
        # Native picture descriptions live in descriptions.json; without it the
        # cache is incomplete, so flipping the knob forces a re-parse.
        cache_ok = cache_ok and figures.descriptions_path(pdf_path.stem).exists()
    if cache_ok:
        log.info("Using cached markdown %s", md_path.name)
        return md_path.read_text(encoding="utf-8")

    log.info("Parsing %s with Docling (tens of minutes per book is normal) ...", pdf_path.name)
    total_pages = _pdf_page_count(pdf_path)
    if total_pages:
        log.info("Page progress: %s has %d pages total.", pdf_path.name, total_pages)
    t0 = time.time()

    # Liveness heartbeat: docling is quiet at WARNING, so report elapsed time
    # every PARSE_HEARTBEAT_S so the log never goes silent during a long parse.
    _stop = threading.Event()

    def _beat() -> None:
        while not _stop.wait(settings.parse_heartbeat_s):
            log.info(
                "Still parsing %s — %.0f min elapsed (page progress above).",
                pdf_path.name, (time.time() - t0) / 60,
            )

    threading.Thread(target=_beat, daemon=True).start()
    try:
        result = get_converter().convert(str(pdf_path))
    finally:
        _stop.set()
    md = result.document.export_to_markdown()
    md_path.write_text(md, encoding="utf-8")
    if settings.figures_enabled.lower() != "off":
        try:
            figures.extract_figures(result.document, pdf_path.stem)
            if settings.native_picture_description:
                figures.native_descriptions(result.document, pdf_path.stem)
        except Exception:
            log.exception("Figure extraction failed for %s", pdf_path.name)
    log.info(
        "Parsed %s -> %s in %.1f min (%d chars)",
        pdf_path.name, md_path.name, (time.time() - t0) / 60, len(md),
    )
    return md
