"""E2E: full index pipeline — file → facts → FAISS → graph.

Requires LLM server running. Skip with: pytest -m "not llm"
"""

import tempfile
from pathlib import Path

import pytest

from filekb.config import load_config
from filekb.dedup import inline_dedup
from filekb.embed import configure, embed_query
from filekb.extractor import extract_facts
from filekb.graph_store import GraphStore
from filekb.llm import LLMClient
from filekb.parser import parse_file
from filekb.splitter import chunk_text
from filekb.store import Store
from filekb.vector_store import VectorStore

pytestmark = pytest.mark.llm


@pytest.fixture
def llm():
    cfg = load_config()
    return LLMClient(base_url=cfg.llm.base_url, model=cfg.llm.model, timeout=cfg.llm.timeout)


@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    s = Store(path)
    yield s
    s.close()
    Path(path).unlink(missing_ok=True)


def test_full_pipeline(llm, store):
    """Parse a Markdown file → extract facts → store → rebuild indices → search."""
    from filekb.embed import embed_fact

    # 1. Create + parse a test document
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write(
            "# Test Document\n\n"
            "Alice is a software engineer at Acme Corp since 2020.\n"
            "She leads the platform team and reports to Bob.\n\n"
            "The platform handles 10,000 requests per second with 99.9% uptime.\n"
        )
        doc_path = f.name

    try:
        text = parse_file(doc_path)
        assert "Alice" in text

        # 2. Chunk
        chunks = chunk_text(text, max_chars=4000)
        assert len(chunks) >= 1

        # 3. Extract
        store.add_directory("/tmp/test")
        fid = store.upsert_file(doc_path, "test_hash", 1, len(text))

        total_facts = 0
        for ci, chunk in enumerate(chunks):
            cid = store.insert_chunk(fid, ci, chunk)
            result = extract_facts(llm, chunk, chunk_id=cid, rounds=1)
            deduped = inline_dedup(result.facts)

            for fact in deduped:
                emb = embed_fact(fact.subject, fact.predicate, fact.object,
                                 fact.title or "", fact.description or "")
                store.insert_fact(
                    file_id=fid, chunk_id=cid,
                    subject=fact.subject, predicate=fact.predicate,
                    object=fact.object, title=fact.title,
                    description=fact.description, confidence=fact.confidence,
                    tags=fact.tags, embedding=emb,
                )
                total_facts += 1

        assert total_facts > 0, "Should extract at least 1 fact"

        # 4. Rebuild FAISS + graph
        vs = VectorStore(dimension=1024)
        vs.rebuild_from_sqlite(store)
        assert vs.size == total_facts

        gs = GraphStore()
        rows = store.conn.execute(
            "SELECT id, subject, predicate, object, confidence FROM facts WHERE status='active'"
        ).fetchall()
        gs.rebuild_from_facts([dict(r) for r in rows])
        assert gs.node_count > 0
        assert gs.edge_count == total_facts

        # 5. Search
        configure(backend="omlx")
        q_vec = embed_query("Who works at Acme Corp?")
        results = vs.search(q_vec, k=5)
        assert len(results) > 0

        fts = store.search_facts_fts("Alice", limit=3)
        assert len(fts) > 0

    finally:
        Path(doc_path).unlink(missing_ok=True)
