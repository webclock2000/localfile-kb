"""Semantic deduplication using embedding cosine similarity.

Compares fact embeddings to identify near-duplicate facts
and merges them, keeping the highest-confidence version.

Per DEVELOPMENT_V3.md §7.2:
- Embedding-based cosine similarity (threshold: 0.90)
- In-memory accumulation + vectorized batch comparison
- Dedup within a single extraction batch (not cross-batch)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from filekb.extractor import Fact

logger = logging.getLogger(__name__)


def compute_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    """Compute cosine similarity between two normalized vectors."""
    return float(np.dot(vec_a, vec_b))


def batch_similarity(embeddings: np.ndarray) -> np.ndarray:
    """Compute pairwise cosine similarity matrix for a set of normalized embeddings.

    Args:
        embeddings: (N, D) array of L2-normalized vectors.

    Returns:
        (N, N) similarity matrix.
    """
    return embeddings @ embeddings.T


def dedup_facts(
    facts: list[Fact],
    embeddings: np.ndarray,
    threshold: float = 0.90,
) -> tuple[list[Fact], np.ndarray]:
    """Remove near-duplicate facts based on embedding similarity.

    When two facts have cosine similarity > threshold, the one with
    higher confidence is kept. If confidence is equal, the first one wins.

    Args:
        facts: List of facts (with embeddings as separate array).
        embeddings: (N, D) array of L2-normalized fact embeddings.
        threshold: Cosine similarity threshold (0.0-1.0).

    Returns:
        (deduped_facts, deduped_embeddings).
    """
    n = len(facts)
    if n <= 1:
        return facts, embeddings

    sim_matrix = batch_similarity(embeddings)
    kept = set(range(n))
    removed: set[int] = set()

    for i in range(n):
        if i in removed:
            continue
        for j in range(i + 1, n):
            if j in removed:
                continue
            if sim_matrix[i, j] > threshold:
                # Keep the one with higher confidence
                if facts[i].confidence >= facts[j].confidence:
                    removed.add(j)
                    logger.debug(
                        "Dedup: '%s' absorbed '%s' (sim=%.3f)",
                        facts[i].title or facts[i].subject,
                        facts[j].title or facts[j].subject,
                        sim_matrix[i, j],
                    )
                else:
                    removed.add(i)
                    logger.debug(
                        "Dedup: '%s' absorbed '%s' (sim=%.3f)",
                        facts[j].title or facts[j].subject,
                        facts[i].title or facts[i].subject,
                        sim_matrix[i, j],
                    )
                    break  # i is removed, stop comparing against it

    kept_indices = sorted(kept - removed)
    deduped_facts = [facts[i] for i in kept_indices]
    deduped_embeddings = embeddings[kept_indices]

    if removed:
        logger.info("Dedup: %d/%d facts removed as near-duplicates", len(removed), n)

    return deduped_facts, deduped_embeddings


def inline_dedup(
    facts: list[Fact],
    threshold: float = 0.90,
) -> list[Fact]:
    """Dedup facts without embeddings — use title/subject/object overlap.

    This is a fast pre-filter before embedding-based dedup.
    Removes facts with identical (subject, predicate, object) triples.

    Args:
        facts: Facts to deduplicate.
        threshold: Not used in this function (for API compatibility).

    Returns:
        Deduplicated facts.
    """
    seen: dict[str, Fact] = {}
    for fact in facts:
        key = f"{fact.subject}|{fact.predicate}|{fact.object}"
        if key not in seen or fact.confidence > seen[key].confidence:
            seen[key] = fact

    if len(seen) < len(facts):
        logger.info(
            "Inline dedup: %d/%d facts removed as exact duplicates",
            len(facts) - len(seen),
            len(facts),
        )

    return list(seen.values())
