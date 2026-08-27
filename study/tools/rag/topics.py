"""Topic registry access for the pipeline (backed by registry.db).

Thin wrapper around rag.registry: books/topics live in the DB (source of
truth); TOPICS.md is an exported snapshot kept in sync automatically.
"""
from __future__ import annotations

from . import registry
from .registry import TopicRow  # re-export for callers


def load_topics(status: str | None = None, book: str | None = None) -> list[TopicRow]:
    return registry.list_topics(status=status, book=book)


def mark_topic(topic: str, new_status: str) -> bool:
    """Flip the status of one topic (DB + re-export of TOPICS.md)."""
    return registry.update_topic(topic, status=new_status)
