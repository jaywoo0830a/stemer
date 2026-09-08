import json
import tempfile
from pathlib import Path

# Ensure the `study_lib` package can be imported (it lives under `study/`)
import sys
sys.path.append(str(Path(__file__).resolve().parent.parent / "study"))

# Study‑library imports
from study_lib.parse import parse_source
from study_lib.chunk import chunk_markdown
from study_lib.embed import StubEmbedder
from study_lib.store import IndexStore, JsonDurableSink
from study_lib.discover import discover_topics
from study_lib.protocol import load_schema
from study_lib.factory import generate_one
from study_lib.llm import LLMResult, Usage
from study_lib.registry import Library, JsonFileStore


class DummyLLM:
    """A minimal LLM client that returns a static JSON payload.
    The payload satisfies the *exam* schema (lecture, cs, r)."""

    def complete(self, *, system: str, user: str, max_tokens: int = 1000, json_object: bool = True) -> LLMResult:
        # Very small, token‑cheap payload – all strings, within budget.
        payload = {
            "lecture": "A concise lecture for the topic.",
            "cs": [
                {
                    "c": "Test concept",
                    "d": "Definition of the concept.",
                    "f": "$x = y$",
                    "k": "Intuition behind the concept.",
                    "m": "Common mistake to avoid."
                }
            ],
            "r": "Recipe or step‑by‑step guidance."
        }
        # LLMResult carries the content and a usage object (all counters zero).
        return LLMResult(content=payload, usage=Usage())


def test_end_to_end_pipeline():
    """Full pipeline contract test (parse → chunk → embed → store → discover → generate)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # ------------------------------------------------------------------
        # 1️⃣  Create a tiny markdown book with a numbered heading (3.5 …)
        # ------------------------------------------------------------------
        books_dir = root / "books"
        books_dir.mkdir()
        md_path = books_dir / "calc.md"
        md_path.write_text(
            "# 3.5 Example Topic\n\n"
            "Some explanatory text that will become a chunk.\n",
            encoding="utf-8",
        )

        # ------------------------------------------------------------------
        # 2️⃣  Initialise persistent artefacts (registry, store, notes) in temp dirs
        # ------------------------------------------------------------------
        registry_path = root / "registry.json"
        store_path = root / "store"
        notes_path = root / "notes"

        # ------------------------------------------------------------------
        # 3️⃣  Register the book in the Library (required for discover_topics)
        # ------------------------------------------------------------------
        library = Library(JsonFileStore(registry_path))
        library.add_book(book_id="calc", title="calc", subject="math")
        library.save()

        # ------------------------------------------------------------------
        # 4️⃣  Parse the markdown file (profile = "text") → ParsedBook
        # ------------------------------------------------------------------
        book = parse_source(md_path, profile="text", book_id="calc")

        # ------------------------------------------------------------------
        # 5️⃣  Chunk the markdown, embed, and persist the vectors
        # ------------------------------------------------------------------
        chunks = chunk_markdown(book.markdown, book_id=book.book_id)
        embedder = StubEmbedder()
        vectors = embedder.embed_texts([c.text for c in chunks])
        store = IndexStore(JsonDurableSink(store_path))
        store.add_many(chunks, vectors=vectors)
        store.flush()

        # ------------------------------------------------------------------
        # 6️⃣  Discover numbered sections → create a todo topic for "3.5"
        # ------------------------------------------------------------------
        discover_report = discover_topics("calc", library=library, store=store, kind="exam")
        assert discover_report.added, "discover_topics did not create any topic"
        topic_id = discover_report.added[0]
        topic = library.topic(topic_id)

        # ------------------------------------------------------------------
        # 7️⃣  Load the JSON schema for validation
        # ------------------------------------------------------------------
        schema = load_schema()

        # ------------------------------------------------------------------
        # 8️⃣  Run the generation step using the dummy LLM
        # ------------------------------------------------------------------
        result = generate_one(
            topic,
            library=library,
            store=store,
            embedder=embedder,
            llm=DummyLLM(),
            schema=schema,
            guide="",
            notes_dir=notes_path,
        )

        # ------------------------------------------------------------------
        # 9️⃣  Assertions – result status, note file, topic status
        # ------------------------------------------------------------------
        assert result.status == "draft", f"expected draft, got {result.status}"
        note_file = notes_path / f"{topic_id}.md"
        assert note_file.is_file(), f"note file {note_file} was not created"
        assert library.topic(topic_id).status == "draft", "topic status was not updated to draft"

        # Clean up is automatic via the TemporaryDirectory context manager.

# The test can be executed with `pytest -q brain/test_pipeline_contract.py`.
