from pathlib import Path

import pytest

from study_lib.protocol import load_schema

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "gen_protocol" / "v1" / "schema.json"
)


@pytest.fixture
def schema():
    """GEN-PROTOCOL v1 스키마 (config/gen_protocol/v1/schema.json)."""
    return load_schema(SCHEMA_PATH)
