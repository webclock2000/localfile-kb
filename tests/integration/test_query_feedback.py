"""Query + feedback loop: downvote should affect future ranking."""

import tempfile
from pathlib import Path

import pytest

from filekb.personalization import allocate_delta, apply_feedback, decay_scores
from filekb.store import Store

pytestmark = pytest.mark.llm


def test_positive_feedback_increases_score():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    store = Store(db_path)
    try:
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test.md", "hash", 1, 100)
        cid = store.insert_chunk(fid, 0, "content")

        fact_id = store.insert_fact(fid, cid, "Alice", "works_at", "Acme",
                                     title="Alice works at Acme", confidence=80)

        # Verify initial score
        row = store.conn.execute("SELECT user_score FROM facts WHERE id=?", (fact_id,)).fetchone()
        assert row["user_score"] == 1.0

        # Apply positive feedback
        updated = apply_feedback(
            store,
            [{"fact_id": fact_id, "similarity_score": 1.0}],
            "positive", delta=0.1,
        )
        assert updated == 1

        # Score should increase
        row = store.conn.execute("SELECT user_score FROM facts WHERE id=?", (fact_id,)).fetchone()
        assert row["user_score"] > 1.0

    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)


def test_negative_feedback_decreases_score():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    store = Store(db_path)
    try:
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test.md", "hash", 1, 100)
        cid = store.insert_chunk(fid, 0, "content")

        fact_id = store.insert_fact(fid, cid, "Bob", "works_at", "Other",
                                     title="Bob works at Other", confidence=70)

        # Apply negative feedback
        apply_feedback(
            store,
            [{"fact_id": fact_id, "similarity_score": 1.0}],
            "negative", delta=0.1,
        )

        row = store.conn.execute("SELECT user_score FROM facts WHERE id=?", (fact_id,)).fetchone()
        assert row["user_score"] < 1.0

    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)


def test_allocate_delta_relevance():
    """Higher-similarity facts should get larger deltas."""
    facts = [
        {"fact_id": 1, "similarity_score": 0.9},
        {"fact_id": 2, "similarity_score": 0.6},
        {"fact_id": 3, "similarity_score": 0.1},
    ]
    deltas = allocate_delta(facts, total_delta=0.3, mode="relevance")
    assert deltas[1] > deltas[3]  # Higher sim → bigger delta
    assert sum(deltas.values()) == pytest.approx(0.3)


def test_score_decay_pulls_toward_one():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    store = Store(db_path)
    try:
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test.md", "hash", 1, 100)
        cid = store.insert_chunk(fid, 0, "content")

        fact_id = store.insert_fact(fid, cid, "X", "y", "Z", title="Test")

        # Push score up
        store.update_user_score(fact_id, 0.5)  # 1.0 → 1.5
        row = store.conn.execute("SELECT user_score FROM facts WHERE id=?", (fact_id,)).fetchone()
        assert row["user_score"] == 1.5

        # Decay toward 1.0
        decay_scores(store, decay_rate=0.5)
        row = store.conn.execute("SELECT user_score FROM facts WHERE id=?", (fact_id,)).fetchone()
        # score = 1.5 - 0.5*(1.5-1.0) = 1.5 - 0.25 = 1.25
        assert row["user_score"] == pytest.approx(1.25)

    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)
