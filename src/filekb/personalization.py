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
    from filekb.entity_qa import (
        _is_numeric_dominant,
        _looks_like_code,
        compute_gibberish_score,
    )

    all_entities = graph_store.all_entities()
    if len(all_entities) < 2:
        return []

    # ── Pre-filter: skip entities that are clearly garbage ──
    # This prevents numeric values, codes, and gibberish from generating
    # meaningless merge proposals (e.g. "0.50万" ↔ "1.00万元").
    skip_entities: set[str] = set()
    for name in all_entities:
        # Skip purely numeric/value entities (medical readings, currency, etc.)
        if _is_numeric_dominant(name):
            skip_entities.add(name)
            continue
        # Skip code-like entities (bank card numbers, IDs, etc.)
        if _looks_like_code(name):
            skip_entities.add(name)
            continue
        # Skip high-gibberish entities (entropy anomalies, repeated n-grams)
        if compute_gibberish_score(name) >= 0.5:
            skip_entities.add(name)
            continue

    entities = [e for e in all_entities if e not in skip_entities]
    if len(entities) < 2:
        logger.info(
            "Pre-filter skipped %d/%d entities, too few remaining for merge proposals",
            len(skip_entities), len(all_entities),
        )
        return []

    if skip_entities:
        logger.info(
            "Pre-filter skipped %d suspect entities (numeric/code/gibberish), "
            "%d remaining for merge proposal generation",
            len(skip_entities), len(entities),
        )

    # Embed remaining entity names
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
    """Validate merge proposals with LLM judgment — batched.

    Instead of calling the LLM once per proposal (100+ calls for a full run),
    all borderline proposals are sent in a single request.  High-similarity
    pairs (>= auto_threshold) skip LLM entirely and are auto-approved.

    Args:
        proposals: Merge candidate proposals.
        llm_client: LLMClient instance.
        auto_threshold: Auto-approve threshold (similarity score).

    Returns:
        Proposals with added 'llm_judgment', 'canonical_name', 'status' fields.
    """
    import json

    # ── Split: auto-approved vs. need LLM validation ──
    auto: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []

    for prop in proposals:
        if prop["similarity"] >= auto_threshold:
            prop["status"] = "auto_approved"
            prop["canonical_name"] = max(prop["entity_a"], prop["entity_b"], key=len)
            prop["llm_judgment"] = "auto"
            auto.append(prop)
        else:
            batch.append(prop)

    if not batch:
        return auto

    # ── Batch LLM validation (one call for all borderline proposals) ──
    try:
        pairs_text = ""
        for idx, prop in enumerate(batch):
            pairs_text += (
                f"{idx}: A='{prop['entity_a']}' B='{prop['entity_b']}' "
                f"sim={prop['similarity']:.3f}\n"
            )

        system_prompt = (
            "You are an entity resolution assistant. For each numbered pair below, "
            "determine whether the two entity names refer to the same real-world thing. "
            "Respond with a JSON object containing a 'results' array, one entry per pair:\n"
            '{"results": [{"id": <number>, "same": true/false, '
            '"canonical_name": "preferred name", "reasoning": "..."}, ...]}\n'
            "Only mark 'same':true when highly confident the entities are identical. "
            "Different numbers, different units, different currencies → NOT same."
        )
        response = llm_client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": pairs_text},
            ],
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        judgment = json.loads(response.content)

        # Normalize: LLM may return {"results": [...]} or [...] or plain object
        results: list[dict] = []
        if isinstance(judgment, list):
            results = judgment
        elif isinstance(judgment, dict):
            results = judgment.get("results", judgment.get("pairs", []))
            if not isinstance(results, list):
                # Single-object response (old behavior) — wrap
                results = [judgment]
        else:
            raise ValueError(f"Unexpected LLM response format: {type(judgment)}")

        # Index results by id
        by_id: dict[int, dict] = {}
        for r in results:
            if isinstance(r, dict):
                by_id[r.get("id", -1)] = r

        for idx, prop in enumerate(batch):
            r = by_id.get(idx, {})
            prop["llm_judgment"] = r.get("same", False)
            prop["canonical_name"] = r.get(
                "canonical_name",
                max(prop["entity_a"], prop["entity_b"], key=len),
            )
            prop["status"] = "proposed" if r.get("same") else "rejected"

        logger.info(
            "Batch LLM validated %d proposals in 1 call: %d same, %d not-same",
            len(batch),
            sum(1 for p in batch if p.get("status") == "proposed"),
            sum(1 for p in batch if p.get("status") == "rejected"),
        )

    except Exception as e:
        logger.warning(
            "Batch LLM validation failed for %d proposals (%s) — "
            "falling back to proposed status for all",
            len(batch), e,
        )
        for prop in batch:
            prop["status"] = "proposed"
            prop["canonical_name"] = max(prop["entity_a"], prop["entity_b"], key=len)

    return auto + batch


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
# Entity quality assessment — suspect entity detection
# ============================================================================


def generate_suspect_proposals(
    store: Store,
    graph_store: Any,
    *,
    ocr_file_patterns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run entity QA detection rules and queue suspects for human review.

    This is a pure-local pipeline (zero LLM cost) that flags entities
    likely to be OCR errors, gibberish, or other low-quality data.

    Detection rules (see entity_qa.py for details):
    - Character count anomalies
    - Gibberish score (entropy, Unicode ranges, repeated n-grams)
    - Jieba word validity (Chinese)
    - Graph isolation (degree=0)
    - OCR-only source

    Flagged entities are inserted into entity_proposals table with
    proposal_type='suspect' for UI review.

    Args:
        store: Store instance.
        graph_store: GraphStore instance.
        ocr_file_patterns: If provided, file paths matching these patterns
            are treated as OCR-sourced. If None, auto-detection is used.

    Returns:
        List of suspect check results (those queued for review).
    """
    from filekb.entity_qa import check_entity, compute_gibberish_score

    entities = graph_store.all_entities()
    if not entities:
        logger.info("No entities to scan for quality issues")
        return []

    # Build entity → sources mapping from facts table
    entity_sources: dict[str, list[str]] = {}
    rows = store.conn.execute(
        """SELECT subject, object, f.path AS source
           FROM facts fa
           JOIN files f ON fa.file_id = f.id
           WHERE fa.status = 'active'"""
    ).fetchall()
    for row in rows:
        for entity_key in (row["subject"], row["object"]):
            if entity_key not in entity_sources:
                entity_sources[entity_key] = []
            src = row["source"]
            if src and src not in entity_sources[entity_key]:
                entity_sources[entity_key].append(src)

    # Identify OCR-recovered files
    ocr_files: set[str] = set()
    if ocr_file_patterns:
        # User-specified patterns
        for src_list in entity_sources.values():
            for src in src_list:
                if any(pattern in src for pattern in ocr_file_patterns):
                    ocr_files.add(src)
    else:
        # Auto-detect: files that went through OCR recovery
        # (marked with _ocr suffix or logged as OCR recovery)
        ocr_rows = store.conn.execute(
            "SELECT path FROM files WHERE error_msg LIKE '%OCR%'"
        ).fetchall()
        ocr_files = {r["path"] for r in ocr_rows}

    # Build graph degree map
    graph_degrees: dict[str, int] = {}
    for entity in entities:
        graph_degrees[entity] = graph_store.graph.degree(entity) if hasattr(graph_store, 'graph') else 0

    # Run detection
    from filekb.entity_qa import scan_entities
    suspects = scan_entities(
        entities,
        graph_degrees=graph_degrees,
        entity_sources=entity_sources,
        ocr_files=ocr_files,
    )

    # Insert suspect proposals into DB
    queued = 0
    for suspect in suspects:
        # Check if already proposed for this entity
        existing = store.conn.execute(
            """SELECT id FROM entity_proposals
               WHERE entity_a = ? AND proposal_type = 'suspect'""",
            (suspect["entity"],),
        ).fetchone()
        if existing:
            continue

        store.insert_proposal(
            entity_a=suspect["entity"],
            entity_b="",  # suspect proposals have no pair
            confidence=1.0 - suspect["gibberish_score"],
            proposed_name=None,
            llm_response=json.dumps(suspect, ensure_ascii=False),
            status="proposed",
            proposal_type="suspect",
        )
        queued += 1

    logger.info("Entity QA: %d entities scanned, %d suspects found, %d new proposals queued",
                len(entities), len(suspects), queued)

    return suspects


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
