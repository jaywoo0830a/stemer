"""Central configuration for the study-note RAG pipeline.

All values can be overridden with environment variables:

    STUDY_*    paths and pipeline behaviour
    LLAMA_*    llama-server connection and generation defaults

The pipeline runs inside Docker with STUDY_ROOT=/data (the mounted study/
folder), but every module also works directly on the host.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# In the container, /data is the mounted study/ directory. On the host,
# fall back to the study/ directory that contains tools/rag/.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    # --- paths -----------------------------------------------------------
    study_root: Path = field(default_factory=lambda: Path(_env("STUDY_ROOT", str(_DEFAULT_ROOT))))

    @property
    def books_inbox(self) -> Path:
        return self.study_root / "books" / "inbox"

    @property
    def books_processed(self) -> Path:
        return self.study_root / "books" / "processed"

    @property
    def books_markdown(self) -> Path:
        return self.study_root / "books" / "markdown"

    @property
    def figures_dir(self) -> Path:
        return self.study_root / "books" / "figures"

    @property
    def index_dir(self) -> Path:
        return self.study_root / "index"

    @property
    def notes_dir(self) -> Path:
        return self.study_root / "notes"

    @property
    def problems_dir(self) -> Path:
        return self.study_root / "problems"

    @property
    def exam_dir(self) -> Path:
        """Exam-prep study guides (concepts + problems + solutions in one file)."""
        return self.study_root / "exam"

    @property
    def logs_dir(self) -> Path:
        return self.study_root / "logs"

    @property
    def topics_file(self) -> Path:
        return self.study_root / "TOPICS.md"

    @property
    def agents_file(self) -> Path:
        return self.study_root / "AGENTS.md"

    @property
    def template_file(self) -> Path:
        return self.study_root / "templates" / "warmup.md"

    @property
    def registry_file(self) -> Path:
        return self.study_root / "registry.db"

    @property
    def chunking_dir(self) -> Path:
        """YAML chunking profiles: chunking.yaml + per-book chunking.<book_id>.yaml."""
        return self.study_root / "config"

    # --- chunking ----------------------------------------------------------
    chunk_min_chars: int = field(default_factory=lambda: _env_int("CHUNK_MIN_CHARS", 400))
    chunk_max_chars: int = field(default_factory=lambda: _env_int("CHUNK_MAX_CHARS", 1500))
    chunk_overlap: int = field(default_factory=lambda: _env_int("CHUNK_OVERLAP", 150))

    # --- database (Postgres + pgvector) --------------------------------------
    # Set DATABASE_URL (e.g. postgresql://study:study@127.0.0.1:5432/study) to
    # use the Dockerized Postgres+pgvector backend. Empty = SQLite+Chroma
    # (rag.db + index/chroma) as before.
    database_url: str = _env("DATABASE_URL", "")

    @property
    def use_pg(self) -> bool:
        """True when DATABASE_URL is set (Postgres+pgvector backend)."""
        return bool(self.database_url)

    # --- models -------------------------------------------------------------
    embed_model: str = _env("EMBED_MODEL", "BAAI/bge-m3")
    rerank_model: str = _env("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    embed_batch: int = field(default_factory=lambda: _env_int("EMBED_BATCH", 64))

    # --- retrieval -----------------------------------------------------------
    top_k_candidates: int = field(default_factory=lambda: _env_int("TOP_K_CANDIDATES", 30))
    rerank_pool: int = field(default_factory=lambda: _env_int("RERANK_POOL", 20))
    n_crossref: int = field(default_factory=lambda: _env_int("N_CROSSREF", 3))

    # --- problem sets ----------------------------------------------------------
    problems_basic: int = field(default_factory=lambda: _env_int("PROBLEMS_BASIC", 10))
    problems_advanced: int = field(default_factory=lambda: _env_int("PROBLEMS_ADVANCED", 10))

    # --- exam study guide --------------------------------------------------------
    exam_archetypes: int = field(default_factory=lambda: _env_int("EXAM_ARCHETYPES", 5))
    exam_basic: int = field(default_factory=lambda: _env_int("EXAM_BASIC", 8))
    exam_intermediate: int = field(default_factory=lambda: _env_int("EXAM_INTERMEDIATE", 8))
    exam_advanced: int = field(default_factory=lambda: _env_int("EXAM_ADVANCED", 4))

    # --- figures / VLM -----------------------------------------------------------
    figures_enabled: str = _env("FIGURES_ENABLED", "on")   # on | off
    vlm_base_url: str = _env("VLM_BASE_URL", "")            # empty = VLM disabled
    vlm_api_key: str = _env("VLM_API_KEY", "")
    vlm_model: str = _env("VLM_MODEL", "local")
    vlm_timeout_s: int = field(default_factory=lambda: _env_int("VLM_TIMEOUT_S", 600))
    # docling's built-in picture description (do_picture_description); when on,
    # the custom VLM figure-captioning (figures.py/vlm.py) is skipped.
    docling_picture_description: str = _env("DOCLING_PICTURE_DESCRIPTION", "off")  # on | off

    @property
    def native_picture_description(self) -> bool:
        """Whether docling's built-in picture description is enabled."""
        return self.docling_picture_description.strip().lower() == "on"

    # --- docling parsing (2.123.1) ----------------------------------------------
    docling_images_scale: float = field(default_factory=lambda: _env_float("DOCLING_IMAGES_SCALE", 2.0))
    docling_formula_preset: str = _env("DOCLING_FORMULA_PRESET", "codeformulav2")
    docling_ocr_mode: str = _env("DOCLING_OCR_MODE", "default")     # default | full_page
    docling_ocr_lang: str = _env("DOCLING_OCR_LANG", "en")
    docling_heading_hierarchy: str = _env("DOCLING_HEADING_HIERARCHY", "on")
    docling_chart_extraction: str = _env("DOCLING_CHART_EXTRACTION", "on")
    docling_code_enrichment: str = _env("DOCLING_CODE_ENRICHMENT", "on")
    docling_picture_preset: str = _env("DOCLING_PICTURE_PRESET", "smolvlm")
    docling_picture_area_threshold: float = field(default_factory=lambda: _env_float("DOCLING_PICTURE_AREA_THRESHOLD", 0.05))
    docling_layout_preset: str = _env("DOCLING_LAYOUT_PRESET", "")  # "" = docling default

    @property
    def docling_heading_hierarchy_enabled(self) -> bool:
        return self.docling_heading_hierarchy.strip().lower() != "off"

    @property
    def docling_chart_extraction_enabled(self) -> bool:
        return self.docling_chart_extraction.strip().lower() != "off"

    @property
    def docling_code_enrichment_enabled(self) -> bool:
        return self.docling_code_enrichment.strip().lower() != "off"

    # --- llama-server ---------------------------------------------------------
    llama_base_url: str = _env("LLAMA_BASE_URL", "http://127.0.0.1:8000/v1")
    llama_api_key: str = _env("LLAMA_API_KEY", "")
    reasoning_effort: str = _env("REASONING_EFFORT", "low")  # low | medium | high | off
    max_tokens: int = field(default_factory=lambda: _env_int("MAX_TOKENS", 4096))
    temperature: float = 0.6
    top_p: float = 0.95
    top_k: int = 20
    request_timeout_s: int = field(default_factory=lambda: _env_int("REQUEST_TIMEOUT_S", 3 * 3600))
    # how long the generate phase waits for the host to bring llama-server up
    llm_start_timeout_s: int = field(default_factory=lambda: _env_int("LLM_START_TIMEOUT_S", 180))

    # --- pipeline --------------------------------------------------------------
    watch_interval_s: int = field(default_factory=lambda: _env_int("WATCH_INTERVAL_S", 300))
    katex_lint: str = _env("KATEX_LINT", "on")  # on | off — cheap inline KaTeX policy lint

    def ensure_dirs(self) -> None:
        for p in (self.books_inbox, self.books_processed, self.books_markdown,
                  self.index_dir, self.notes_dir, self.problems_dir, self.exam_dir,
                  self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
