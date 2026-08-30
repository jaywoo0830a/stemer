"""Functional llama-server lifecycle helpers (container side).

The llama-server *process* is started/stopped by the HOST orchestrator
(study/pipeline.sh, reusing up.sh/down.sh) — a container cannot exec host
processes. This module only inspects health and blocks the generate phase
until the server is ready, raising LLMUnavailable with a clear message when
it is not.
"""
from __future__ import annotations

import logging
import time

import httpx

from .config import settings

log = logging.getLogger("rag")


def is_healthy(base_url: str | None = None, api_key: str | None = None, timeout: float = 5.0) -> bool:
    """True if the llama-server /health endpoint answers 200."""
    base_url = base_url or settings.llama_base_url
    api_key = settings.llama_api_key if api_key is None else api_key
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = httpx.get(base_url.rstrip("/") + "/health", headers=headers, timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def wait_healthy(timeout: float, interval: float = 2.0) -> bool:
    """Poll /health until it succeeds or `timeout` seconds elapse."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_healthy():
            return True
        time.sleep(interval)
    return False


class LLMUnavailable(RuntimeError):
    """Raised when the llama-server is required but not reachable."""


def require_llm(timeout: float | None = None) -> None:
    """Fail fast if the LLM is not up (generation must never silently skip).

    The host orchestrator starts the server (up.sh) before invoking the
    generate phase; this is only a safety net with a bounded wait.
    """
    timeout = settings.llm_start_timeout_s if timeout is None else timeout
    if not wait_healthy(timeout):
        raise LLMUnavailable(
            f"llama-server not reachable at {settings.llama_base_url} "
            f"after {timeout:.0f}s. Start it via: bash study/pipeline.sh generate"
        )
