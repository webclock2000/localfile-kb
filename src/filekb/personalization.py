"""Personalization — feedback scoring, entity resolution, and preference learning.

Three subsystems per DEVELOPMENT_V3.md §11:

1. Feedback scoring — adapted from RAGFlow PR #12689
   - allocate_delta(): distribute feedback across cited facts by relevance
   - apply_feedback(): update user_score in SQLite
   - Weekly decay toward 1.0

2. Entity resolution — adapted from sift-kg three-stage workflow
   - generate_proposals(): FAISS pre-filter + embedding similarity
   - validate_with_llm(): LLM judgment (yes/no/uncertain)
   - apply_merges(): SQL rewiring + graph rebuild

3. Preference learning — from query logs
   - analyze_query_logs(): extract topic/entity preferences
   - boost_score(): apply preference boost at query time
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from filekb.llm import LLMClient
    from filekb.store import Store

logger = logging.getLogger(__name__)


# ============================================================================
# Feedback scoring (adapted from RAGFlow PR #12689)
# ============================================================================


def allocate_delta(
    cited_facts: list[dict[str, Any]],
    total_delta: float,
    mode: str = "relevance",
) -> dict[int, float]:
    """Allocate a feedback score delta across cited facts.

    Adapted from RAGFlow's _allocate_deltas_relevance():
    splits the delta budget across cited facts proportional to their
    retrieval relevance (similarity score).

    Args:
        cited_facts: List of {fact_id, similarity_score, ...} dicts.
        total_delta: Total score change (positive for upvote, negative for downvote).
        mode: 'relevance' (proportional) or 'uniform' (equal split).

    Returns:
        {fact_id: delta} mapping.
    """
    if not cited_facts:
        return {}

    if mode == "uniform":
        per_fact = total_delta / len(cited_facts)
        return {f["fact_id"]: per_fact for f in cited_facts}

    # Relevance mode: distribute by retrieval signal
    signals = [f.get("similarity_score", 1.0) for f in cited_facts]
    total_signal = sum(signals) or 1.0

    deltas: dict[int, float] = {}
    remaining = abs(total_delta)
    sign = 1 if total_delta > 0 else -1

    for i, fact in enumerate(cited_facts):
        if i == len(cited_facts) - 1:
            # Last fact gets the remainder to avoid floating-point drift
            deltas[fact["fact_id"]] = sign * remaining
        else:
            share = round(abs(total_delta) * signals[i] / total_signal, 4)
            remaining -= share
            deltas[fact["fact_id"]] = sign * share

    return deltas


def apply_feedback(
    store: Store,
    answer_sources: list[dict[str, Any]],
    feedback_type: str,
    delta: float = 0.1,
    reason: str | None = None,
) -> int:
    """Apply user feedback to fact user_scores.

    Args:
        store: Store instance.
        answer_sources: Source facts from the answer, each with fact_id.
        feedback_type: 'positive' or 'negative'.
        delta: Score change magnitude per fact.
        reason: Optional user-provided reason.

    Returns:
        Number of facts updated.
    """
    if feedback_type not in ("positive", "negative"):
        raise ValueError(f"Invalid feedback_type: {feedback_type}")

    sign = 1 if feedback_type == "positive" else -1
    total_delta = sign * delta

    deltas = allocate_delta(answer_sources, total_delta, mode="relevance")
    updated = 0

    for fact_id, d in deltas.items():
        store.update_user_score(fact_id, d)
        store.record_feedback(fact_id, feedback_type, reason)
        updated += 1

    logger.info("Feedback applied: %s → %d facts", feedback_type, updated)
    return updated


def decay_scores(store: Store, decay_rate: float = 0.01) -> int:
    """Apply weekly decay to all user_scores, pulling them toward 1.0.

    Should be called periodically (e.g., weekly via launchd).
    Returns count of updated rows.
    """
    return store.apply_score_decay(decay_rate)


# ============================================================================
# Entity resolution (adapted from sift-kg)
# ============================================================================


def generate_proposals(
    store: Store,
    graph_store: Any,
    *,
    similarity_threshold: float = 0.80,
    max_candidates: int = 100,
) -> list[dict[str, Any]]:
    """Generate entity merge candidates using embedding similarity.

    Adapted from sift-kg's 'sift resolve' stage. Uses FAISS to pre-filter
    the top-10 nearest neighbors for each entity, then applies LLM
    validation for pairs above the similarity threshold.

    Args:
        store: Store instance.
        graph_store: GraphStore instance.
        similarity_threshold: Embedding cosine similarity threshold.
        max_candidates: Maximum candidate pairs to return.

    Returns:
        List of proposal dicts {entity_a, entity_b, similarity, ...}.
    """
    from filekb.embed import embed_batch

    entities = graph_store.all_entities()
    if len(entities) < 2:
        return []

    # Embed all entity names
    embeddings = embed_batch(entities)

    # Compute pairwise similarities (limited to avoid O(n²))
    proposals: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for i, entity_a in enumerate(entities):
        # Only compare with entities that start similarly (fast pre-filter)
        for j, entity_b in enumerate(entities):
            if i >= j:
                continue
            if (entity_a, entity_b) in seen_pairs:
                continue
            seen_pairs.add((entity_a, entity_b))

            # Quick string similarity pre-filter
            if _string_similarity(entity_a, entity_b) < 0.4:
                continue

            # Cosine similarity of embeddings
            sim = float(np.dot(embeddings[i], embeddings[j]))
            if sim >= similarity_threshold:
                proposals.append({
                    "entity_a": entity_a,
                    "entity_b": entity_b,
                    "similarity": round(sim, 4),
                })

            if len(proposals) >= max_candidates:
                break
        if len(proposals) >= max_candidates:
            break

    # Sort by similarity descending
    proposals.sort(key=lambda p: p["similarity"], reverse=True)

    logger.info("Generated %d merge proposals from %d entities", len(proposals), len(entities))
    return proposals


def _string_similarity(a: str, b: str) -> float:
    """Fast string-level similarity for pre-filtering."""
    if not a or not b:
        return 0.0
    a_lower = a.lower()
    b_lower = b.lower()
    if a_lower == b_lower:
        return 1.0
    if a_lower in b_lower or b_lower in a_lower:
        return 0.85
    # Character overlap ratio
    set_a = set(a_lower)
    set_b = set(b_lower)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    return len(intersection) / max(len(set_a), len(set_b))


def validate_with_llm(
    proposals: list[dict[str, Any]],
    llm_client: LLMClient,
    auto_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """Validate merge proposals with LLM judgment.

    Adapted from sift-kg's 'sift resolve' LLM validation stage.
    Proposals with similarity >= auto_threshold are auto-approved.

    Args:
        proposals: Merge candidate proposals.
        llm_client: LLMClient instance.
        auto_threshold: Auto-approve threshold (similarity score).

    Returns:
        Proposals with added 'llm_judgment', 'canonical_name', 'status' fields.
    """
    validated = []
    for prop in proposals:
        # Auto-approve high-similarity pairs
        if prop["similarity"] >= auto_threshold:
            prop["status"] = "auto_approved"
            prop["canonical_name"] = max(prop["entity_a"], prop["entity_b"], key=len)
            prop["llm_judgment"] = "auto"
            validated.append(prop)
            continue

        # LLM validation for borderline cases
        try:
            system_prompt = (
                "You are an entity resolution assistant. Your job is to determine "
                "whether two entity names refer to the same real-world thing. "
                "Respond with a JSON object: "
                '{"same": true/false, "canonical_name": "preferred name", "reasoning": "..."}'
            )
            response = llm_client.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": (
                        f"Entity A: '{prop['entity_a']}'\nEntity B: '{prop['entity_b']}'\n"
                        f"Embedding similarity: {prop['similarity']:.3f}\n\n"
                        "Are these the same entity?"
                    )},
                ],
                max_tokens=256,
            )
            judgment = json.loads(response.content)
            prop["llm_judgment"] = judgment.get("same", False)
            prop["canonical_name"] = judgment.get(
                "canonical_name",
                max(prop["entity_a"], prop["entity_b"], key=len),
            )
            prop["status"] = "proposed" if judgment.get("same") else "rejected"
        except Exception as e:
            logger.warning("LLM entity validation failed for '%s'/'%s': %s",
                           prop["entity_a"], prop["entity_b"], e)
            prop["status"] = "proposed"
            prop["canonical_name"] = max(prop["entity_a"], prop["entity_b"], key=len)
        validated.append(prop)

    return validated


def apply_merges(
    store: Store,
    graph_store: Any,
    proposals: list[dict[str, Any]],
) -> int:
    """Execute confirmed entity merges.

    - auto_approved (similarity >= 0.85): merge immediately in graph + SQLite,
      store as status='approved'.
    - proposed (LLM confirms, lower confidence): store as status='proposed' in
      entity_proposals table for human review — do NOT auto-merge.
    - rejected: skip entirely.

    Args:
        store: Store instance.
        graph_store: GraphStore instance.
        proposals: Validated proposals with status 'auto_approved', 'proposed', or 'rejected'.

    Returns:
        Number of auto-merges applied.
    """
    merged = 0
    for prop in proposals:
        status = prop.get("status", "rejected")

        if status == "rejected":
            continue

        if status == "auto_approved":
            # Store as approved + execute merge immediately
            store.insert_proposal(
                entity_a=prop["entity_a"],
                entity_b=prop["entity_b"],
                confidence=prop["similarity"],
                proposed_name=prop.get("canonical_name"),
                llm_response=json.dumps(prop.get("llm_judgment", {})),
                status="approved",
            )
            from_entity = prop["entity_a"]
            to_entity = prop.get("canonical_name", prop["entity_b"])
            if from_entity and to_entity and from_entity != to_entity:
                edges_rewired = graph_store.merge_entities(from_entity, to_entity)
                store.conn.execute(
                    "UPDATE facts SET subject = ? WHERE subject = ?",
                    (to_entity, from_entity),
                )
                store.conn.execute(
                    "UPDATE facts SET object = ? WHERE object = ?",
                    (to_entity, from_entity),
                )
                store.conn.commit()
                merged += 1
                logger.info(
                    "Auto-merged '%s' → '%s': %d edges rewired",
                    from_entity, to_entity, edges_rewired,
                )

        elif status == "proposed":
            # Store for human review only — do NOT merge
            store.insert_proposal(
                entity_a=prop["entity_a"],
                entity_b=prop["entity_b"],
                confidence=prop["similarity"],
                proposed_name=prop.get("canonical_name"),
                llm_response=json.dumps(prop.get("llm_judgment", {})),
                status="proposed",
            )
            logger.info(
                "Proposal queued for review: '%s' ↔ '%s' (%.2f)",
                prop["entity_a"], prop["entity_b"], prop["similarity"],
            )

    return merged


# ============================================================================
# Preference learning
# ============================================================================


def analyze_query_logs(
    query_history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Analyze query history for preference signals.

    Extracts:
    - preferred_topics: frequently queried topic keywords
    - frequent_entities: entities that appear in many queries
    - answer_verbosity: preferred answer length

    Args:
        query_history: List of {question, timestamp, ...} dicts.

    Returns:
        Preference analysis result.
    """
    if not query_history:
        return {}

    # Topic extraction (simple keyword-based)
    topic_counter: Counter = Counter()
    entity_counter: Counter = Counter()

    for entry in query_history:
        question = entry.get("question", "")
        words = question.lower().split()
        topic_counter.update(words[:10])  # First 10 words tend to be topic-bearing

    return {
        "preferred_topics": [w for w, _ in topic_counter.most_common(10)],
        "preference_confidence": min(
            1.0, len(query_history) / 50
        ),  # Requires ~50 queries for confidence
    }


def boost_score(
    fact: dict[str, Any],
    preferences: dict[str, Any],
) -> float:
    """Compute preference-based score boost for a fact.

    Args:
        fact: Fact dict with subject, predicate, object keys.
        preferences: Preference analysis result from analyze_query_logs().

    Returns:
        Boost multiplier (>= 1.0).
    """
    boost = 1.0

    preferred_topics = set(preferences.get("preferred_topics", []))
    if not preferred_topics:
        return boost

    fact_text = f"{fact.get('subject', '')} {fact.get('predicate', '')} {fact.get('object', '')}"
    fact_words = set(fact_text.lower().split())

    overlap = fact_words & preferred_topics
    if overlap:
        boost += 0.05 * len(overlap)

    return min(boost, 2.0)
