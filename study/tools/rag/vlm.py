"""Minimal client for a local multimodal llama-server (OpenAI-compatible API).

Used to describe textbook figures so the text-only 27B generator can see
what pictures show. Disabled unless VLM_BASE_URL is set.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from .config import settings

log = logging.getLogger("rag")

PROMPT = (
    "You are describing a figure from a STEM textbook. In 2-4 sentences, describe "
    "what the figure shows: axes, labels, curves, shapes, structure and relationships. "
    "Mention any text, symbols or equations visible in the image."
)


def is_enabled() -> bool:
    return bool(settings.vlm_base_url)


def build_messages(image_bytes: bytes, caption: str, mime: str = "image/png") -> list[dict]:
    """OpenAI-style multimodal messages with a base64 data-URI image."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    text = PROMPT
    if caption.strip():
        text += f' The figure caption is: "{caption.strip()}"'
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ]


def describe_image(image_path: Path, caption: str = "") -> str | None:
    """Ask the local VLM to describe one figure image. Returns None on failure."""
    if not is_enabled():
        return None
    data = image_path.read_bytes()
    url = settings.vlm_base_url.rstrip("/") + "/chat/completions"
    headers: dict[str, str] = {}
    if settings.vlm_api_key:
        headers["Authorization"] = f"Bearer {settings.vlm_api_key}"
    body = {
        "model": settings.vlm_model,
        "messages": build_messages(data, caption),
        "temperature": 0.2,
        "max_tokens": 256,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=settings.vlm_timeout_s) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
        content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
        return content or None
    except Exception as exc:
        log.warning("VLM request failed for %s: %s", image_path.name, exc)
        return None
