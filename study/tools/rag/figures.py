"""Figure extraction + optional VLM descriptions for textbook indexing.

Docling exposes pictures as PictureItem with captions and cropped images
(official API: doc.pictures, PictureItem.get_image(doc), caption_text(doc);
enable via PdfPipelineOptions.generate_page_images / generate_picture_images
/ images_scale). We save PNGs + JSON metadata, optionally describe each
figure with a local multimodal llama-server, and attach the descriptions to
the chunks whose text contains the figure caption.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .config import settings

log = logging.getLogger("rag")


def _slug(stem: str) -> str:
    return re.sub(r"[^a-z0-9\uac00-\ud7a3]+", "-", stem.lower()).strip("-") or stem


def figures_dir_for(stem: str) -> Path:
    return settings.figures_dir / _slug(stem)


def metadata_path(stem: str) -> Path:
    return figures_dir_for(stem) / "figures.json"


def descriptions_path(stem: str) -> Path:
    return figures_dir_for(stem) / "descriptions.json"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _page_of(pic) -> int | None:
    try:
        if pic.prov:
            return pic.prov[0].page_no
    except Exception:
        pass
    return None


def extract_figures(doc, stem: str, force: bool = False) -> int:
    """Save every picture of a DoclingDocument as a PNG plus metadata JSON.

    Works with any duck-typed document exposing `.pictures` (PictureItem-like)
    and `.pages` (page objects with `.image`). Page images are freed after
    extraction to cap memory on long books.
    """
    meta = metadata_path(stem)
    if meta.exists() and not force:
        try:
            return json.loads(meta.read_text(encoding="utf-8"))["count"]
        except (OSError, KeyError):
            pass

    out_dir = figures_dir_for(stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    count = 0
    for pic in getattr(doc, "pictures", []):
        try:
            caption = (pic.caption_text(doc) or "").strip()
        except Exception:
            caption = ""
        try:
            img = pic.get_image(doc)
        except Exception:
            img = None
        if img is None:
            continue
        count += 1
        fname = f"fig-{count:05d}.png"
        img.save(out_dir / fname, "PNG")
        records.append({"file": fname, "caption": caption, "page": _page_of(pic)})

    # Free page rasters (pictures have been cropped already).
    for page in getattr(doc, "pages", {}).values():
        try:
            page.image = None
        except Exception:
            pass

    meta.write_text(
        json.dumps({"count": count, "figures": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Extracted %d figures for %s.", count, stem)
    return count


def load_descriptions(stem: str) -> dict[str, str]:
    path = descriptions_path(stem)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def caption_figures(stem: str) -> int:
    """Describe every extracted figure with the local VLM (no-op if disabled).

    Results persist after each figure, so an interrupted overnight run resumes.
    """
    from . import vlm

    meta = metadata_path(stem)
    if not vlm.is_enabled() or not meta.exists():
        return 0
    try:
        figures = json.loads(meta.read_text(encoding="utf-8")).get("figures", [])
    except (OSError, json.JSONDecodeError):
        return 0

    out_dir = figures_dir_for(stem)
    descs = load_descriptions(stem)
    done = 0
    for fig in figures:
        fname = fig.get("file", "")
        if fname in descs and descs[fname]:
            continue
        img_path = out_dir / fname
        if not fname or not img_path.exists():
            continue
        desc = vlm.describe_image(img_path, fig.get("caption", ""))
        if desc:
            descs[fname] = desc
            done += 1
            descriptions_path(stem).write_text(
                json.dumps(descs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    log.info("Described %d figure(s) for %s (%d total).", done, stem, len(descs))
    return done


def native_descriptions(doc, stem: str) -> int:
    """Harvest docling's built-in picture descriptions into descriptions.json.

    Runs inside parse_pdf right after extract_figures when native picture
    description (do_picture_description) is enabled. Both functions iterate
    doc.pictures with the same no-image skip logic, so fig-<n>.png names line up
    with the pictures and their meta.description texts.
    """
    out_dir = figures_dir_for(stem)
    if not out_dir.exists():
        return 0
    descs: dict[str, str] = {}
    count = 0
    for pic in getattr(doc, "pictures", []):
        try:
            img = pic.get_image(doc)
        except Exception:
            img = None
        if img is None:
            continue
        count += 1
        try:
            meta = getattr(pic, "meta", None)
            desc = ""
            if meta is not None:
                d = getattr(meta, "description", None)
                if d is not None:
                    desc = (getattr(d, "text", "") or "").strip()
        except Exception:
            desc = ""
        if desc:
            descs[f"fig-{count:05d}.png"] = desc
    if not descs:
        return 0
    descriptions_path(stem).write_text(
        json.dumps(descs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Harvested %d native docling picture description(s) for %s.", len(descs), stem)
    return len(descs)


def attach_descriptions(chunks: list, stem: str) -> int:
    """Append figure descriptions to chunks containing the figure caption.

    Anchors on the caption text: the chunk whose text contains the caption
    gets a `Figure description: ...` block, keeping descriptions in the same
    section as the figure reference.
    """
    descs = load_descriptions(stem)
    if not descs or not chunks:
        return 0
    meta = metadata_path(stem)
    captions: dict[str, str] = {}
    if meta.exists():
        try:
            for fig in json.loads(meta.read_text(encoding="utf-8")).get("figures", []):
                captions[fig.get("file", "")] = (fig.get("caption") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass

    attached = 0
    for fname, desc in descs.items():
        cap = captions.get(fname, "")
        sig = _norm(cap)[:60]
        if not sig:
            continue
        for chunk in chunks:
            if sig and sig in _norm(chunk.text):
                label = f"[Figure: {cap or fname}]"
                chunk.text = f"{chunk.text}\n\n{label}\nFigure description: {desc}"
                attached += 1
                break
    return attached
