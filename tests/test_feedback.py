"""Tests for feedback scoring and preference learning."""

import pytest

from filekb.personalization import (
    _string_similarity,
    allocate_delta,
    apply_feedback,
)


class TestStringSimilarity:
    def test_identical(self):
        assert _string_similarity("Alice", "Alice") == 1.0

    def test_substring(self):
        assert _string_similarity("Alice", "Alice Smith") >= 0.8

    def test_different(self):
        assert _string_similarity("Alice", "Bob") < 0.5

    def test_case_insensitive(self):
        assert _string_similarity("ALICE", "alice") == 1.0

    def test_empty(self):
        assert _string_similarity("", "") == 0.0

    def test_one_empty(self):
        """Single empty string returns 0 similarity."""
        assert _string_similarity("a", "") == 0.0


class TestAllocateDelta:
    def test_relevance_mode(self):
        cited = [
            {"fact_id": 1, "similarity_score": 0.9},
            {"fact_id": 2, "similarity_score": 0.6},
            {"fact_id": 3, "similarity_score": 0.3},
        ]
        deltas = allocate_delta(cited, total_delta=0.3, mode="relevance")
        assert len(deltas) == 3
        # Higher similarity → larger delta
        assert deltas[1] > deltas[3]
        # Sum should approximate total_delta
        assert 0.29 <= sum(deltas.values()) <= 0.31

    def test_uniform_mode(self):
        cited = [
            {"fact_id": 1, "similarity_score": 0.9},
            {"fact_id": 2, "similarity_score": 0.6},
        ]
        deltas = allocate_delta(cited, total_delta=0.2, mode="uniform")
        assert len(deltas) == 2
        assert deltas[1] == pytest.approx(0.1)
        assert deltas[2] == pytest.approx(0.1)

    def test_empty_cited(self):
        assert allocate_delta([], 0.3) == {}

    def test_negative_delta(self):
        cited = [{"fact_id": 1, "similarity_score": 1.0}]
        deltas = allocate_delta(cited, total_delta=-0.1)
        assert deltas[1] == pytest.approx(-0.1)


class TestApplyFeedback:
    def test_positive_feedback(self, store_with_data):
        cited = [
            {"fact_id": 1, "similarity_score": 1.0},
        ]
        updated = apply_feedback(store_with_data, cited, "positive", delta=0.1)
        assert updated == 1
        row = store_with_data.conn.execute(
            "SELECT user_score FROM facts WHERE id = 1"
        ).fetchone()
        assert row["user_score"] > 1.0

    def test_negative_feedback(self, store_with_data):
        cited = [
            {"fact_id": 1, "similarity_score": 1.0},
        ]
        updated = apply_feedback(store_with_data, cited, "negative", delta=0.1)
        assert updated == 1
        row = store_with_data.conn.execute(
            "SELECT user_score FROM facts WHERE id = 1"
        ).fetchone()
        assert row["user_score"] < 1.0

    def test_invalid_type(self, store_with_data):
        with pytest.raises(ValueError):
            apply_feedback(store_with_data, [], "invalid")
