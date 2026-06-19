"""Tests for semantic deduplication (dedup.py)."""

import numpy as np
import pytest

from filekb.dedup import compute_similarity, dedup_facts, inline_dedup
from filekb.extractor import Fact


class TestInlineDedup:
    def test_removes_exact_duplicate(self):
        f1 = Fact(subject="A", predicate="p", object="B", confidence=70, title="t1")
        f2 = Fact(subject="A", predicate="p", object="B", confidence=90, title="t2")
        result = inline_dedup([f1, f2])
        assert len(result) == 1
        assert result[0].confidence == 90

    def test_keeps_unique_facts(self):
        f1 = Fact(subject="A", predicate="p", object="B")
        f2 = Fact(subject="X", predicate="q", object="Y")
        result = inline_dedup([f1, f2])
        assert len(result) == 2

    def test_empty_list(self):
        assert inline_dedup([]) == []


class TestComputeSimilarity:
    def test_identical_vectors(self):
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        sim = compute_similarity(v, v)
        assert pytest.approx(sim) == 1.0

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0], dtype=np.float32)
        sim = compute_similarity(a, b)
        assert pytest.approx(sim) == 0.0


class TestDedupFacts:
    def test_dedup_by_similarity(self):
        facts = [
            Fact(subject="Alice", predicate="works_at", object="Acme", confidence=85),
            Fact(subject="Alice", predicate="employed_by", object="Acme", confidence=70),
        ]
        # Create embeddings: fact 0 and 1 are very similar
        embeddings = np.array([
            [1.0, 0.1, 0.0],
            [0.99, 0.12, 0.0],
        ], dtype=np.float32)

        deduped, deduped_emb = dedup_facts(facts, embeddings, threshold=0.90)
        assert len(deduped) == 1
        assert deduped[0].confidence == 85

    def test_below_threshold_kept(self):
        facts = [
            Fact(subject="Alice", predicate="works_at", object="Acme", confidence=85),
            Fact(subject="Bob", predicate="works_at", object="Other", confidence=70),
        ]
        embeddings = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float32)

        deduped, _ = dedup_facts(facts, embeddings, threshold=0.90)
        assert len(deduped) == 2
