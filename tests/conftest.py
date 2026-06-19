"""Shared test fixtures for FileKB.

Provides:
- temp_db: Temporary SQLite database with full schema
- temp_store: Store instance connected to temp_db
- sample_md / sample_zh_md / sample_py / sample_csv / sample_json paths
- mock_llm_client: Mock LLMClient for extraction/query tests
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from filekb.store import Store

# Test data directory
TESTS_DIR = Path(__file__).parent
DATA_DIR = TESTS_DIR / "data"


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for tests."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def store(temp_db):
    """Create a Store instance with full schema."""
    s = Store(temp_db)
    yield s
    s.close()


@pytest.fixture
def store_with_data(store):
    """Store with sample directory, file, chunks, and facts."""
    # Add a directory
    store.add_directory("/tmp/test-docs")
    # Add a file
    store.upsert_file(
        path="/tmp/test-docs/sample.md",
        sha256="abc123",
        directory_id=1,
        file_size=500,
    )
    # Add chunks
    store.insert_chunk(1, 0, "Sample chunk content for testing.")
    store.insert_chunk(1, 1, "Another chunk with more text.")
    # Add facts
    store.insert_fact(
        file_id=1, chunk_id=1,
        subject="Alice", predicate="works_at", object="Acme Corp",
        title="Alice works at Acme Corp",
        description="Alice has been employed at Acme Corp since 2020.",
        evidence_span="Alice joined Acme Corp in 2020.",
        confidence=85,
    )
    store.insert_fact(
        file_id=1, chunk_id=1,
        subject="Alice", predicate="collaborates_with", object="Bob",
        title="Alice collaborates with Bob",
        confidence=70,
    )
    return store


@pytest.fixture
def sample_md():
    """Path to sample English markdown file."""
    path = DATA_DIR / "sample.md"
    yield path


@pytest.fixture
def sample_zh_md():
    """Path to sample Chinese markdown file."""
    path = DATA_DIR / "sample_zh.md"
    yield path


@pytest.fixture
def sample_py():
    """Path to sample Python file."""
    path = DATA_DIR / "sample.py"
    yield path


@pytest.fixture
def sample_csv():
    """Path to sample CSV file."""
    path = DATA_DIR / "sample.csv"
    yield path


@pytest.fixture
def sample_json():
    """Path to sample JSON file."""
    path = DATA_DIR / "sample.json"
    yield path


@pytest.fixture
def mock_llm_client():
    """Mock LLMClient that returns canned extraction responses."""
    mock = MagicMock()

    def fake_extract(chunk_text, system_prompt, max_tokens=4096, temperature=0.3):
        from filekb.llm import LLMResponse
        facts_json = json.dumps({
            "facts": [
                {
                    "title": "Alice works at Acme Corp",
                    "subject": "Alice",
                    "predicate": "works_at",
                    "object": "Acme Corp",
                    "description": "Alice has worked at Acme Corp since 2020.",
                    "evidence_span": "Alice joined Acme Corp in 2020.",
                    "confidence": 85,
                    "tags": ["employment"],
                }
            ]
        })
        return LLMResponse(content=facts_json, finish_reason="stop")

    mock.extract_facts.side_effect = fake_extract

    def fake_answer(question, context, system_prompt, max_tokens=1024, temperature=0.3):
        from filekb.llm import LLMResponse
        return LLMResponse(
            content="Based on the knowledge base: Alice works at Acme Corp. (source: /tmp/test-docs/sample.md)",
            finish_reason="stop",
        )

    mock.generate_answer.side_effect = fake_answer
    mock.continue_truncated.return_value = MagicMock(content="{}", finish_reason="stop")

    return mock
