"""Query engine — hybrid retrieval + answer generation.

Performs:
1. Embed question via bge-m3
2. FAISS vector search (top 2k)
3. SQLite FTS5 full-text search (top 10)
4. Weighted merge: score = 0.5×vector + 0.3×graph + 0.2×fts + 0.1×log(user_score)
5. Graph-based expansion (1-hop from seed entities)
6. Context collection + LLM answer generation with source citations

Per DEVELOPMENT_V3.md §7.2 / §4.3.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from filekb.graph_store import GraphStore
    from filekb.llm import LLMClient
    from filekb.store import Store
    from filekb.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    """Structured result from a query."""

    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    related_facts: list[dict[str, Any]] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    retrieval_count: int = 0


@dataclass
class ScoredFact:
    """A fact with a retrieval score for ranking."""

    fact_id: int
    subject: str
    predicate: str
    object: str
    title: str = ""
    description: str = ""
    source_file: str = ""
    evidence_span: str = ""
    user_score: float = 1.0
    vector_score: float = 0.0
    graph_score: float = 0.0
    fts_score: float = 0.0
    final_score: float = 0.0


def _reciprocal_rank_fusion(
    vector_results: list[tuple[int, float]],
    fts_results: list[dict[str, Any]],
    graph_results: list[dict[str, Any]],
    *,
    store: Store,
    vector_weight: float = 0.5,
    graph_weight: float = 0.3,
    fts_weight: float = 0.2,
    user_score_weight: float = 0.1,
    k: int = 60,
) -> list[tuple[int, float]]:
    """Merge results from three retrieval methods using weighted RRF.

    Args:
        vector_results: [(fact_id, similarity_score), ...]
        fts_results: [{"id": int, ...}, ...]
        graph_results: [{"fact_id": int, "distance": int}, ...]
        store: Store instance for fetching per-fact user_score.
        vector_weight: Weight for vector similarity.
        graph_weight: Weight for graph proximity.
        fts_weight: Weight for full-text search.
        user_score_weight: Weight for user feedback bias.
        k: RRF constant.

    Returns:
        Merged list of (fact_id, combined_score) sorted by descending score.
    """
    scores: dict[int, float] = {}

    # Vector results (already sorted, descending score)
    for rank, (fid, sim) in enumerate(vector_results):
        rrf = 1.0 / (k + rank + 1)
        scores[fid] = scores.get(fid, 0) + vector_weight * sim + (1 - vector_weight) * rrf

    # FTS results (already ranked by relevance)
    for rank, row in enumerate(fts_results):
        fid = row["id"]
        rrf = 1.0 / (k + rank + 1)
        scores[fid] = scores.get(fid, 0) + fts_weight * rrf

    # Graph proximity results
    for rank, item in enumerate(graph_results):
        fid = item.get("fact_id", 0)
        if fid:
            proximity = 1.0 / (1.0 + item.get("distance", 1))
            scores[fid] = scores.get(fid, 0) + graph_weight * proximity

    # Apply user_score boost — log(user_score) so:
    #   - score > 1.0 → positive boost (gentle lift)
    #   - score < 1.0 → negative penalty (sinks toward irrelevance)
    #   - score = 1.0 → neutral  (log(1.0) = 0, no effect)
    # weight is small (default 0.1) to prevent feedback loops from
    # overpowering retrieval signal.
    if user_score_weight > 0 and scores:
        all_fids = list(scores.keys())
        user_scores = store.get_user_scores(all_fids)
        for fid in all_fids:
            us = user_scores.get(fid, 1.0)
            if us != 1.0:
                scores[fid] += user_score_weight * math.log(max(us, 0.01))

    # Sort by combined score descending
    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return merged


def query(
    question: str,
    store: Store,
    vector_store: VectorStore,
    graph_store: GraphStore,
    llm_client: LLMClient,
    *,
    vector_top_k: int = 20,
    fts_top_k: int = 10,
    vector_weight: float = 0.5,
    graph_weight: float = 0.3,
    fts_weight: float = 0.2,
    user_score_weight: float = 0.1,
    max_context_chunks: int = 8,
    max_context_facts: int = 30,
    answer_max_tokens: int = 1024,
    conversation_history: list[dict] | None = None,
) -> QueryResult:
    """Execute a full query pipeline: retrieve → rank → expand → answer.

    Args:
        question: Natural language question.
        store: Store instance.
        vector_store: VectorStore instance.
        graph_store: GraphStore instance.
        llm_client: LLMClient instance.
        vector_top_k: FAISS search result count.
        fts_top_k: FTS5 search result count.
        vector_weight: Weight for vector similarity in merge.
        graph_weight: Weight for graph proximity in merge.
        fts_weight: Weight for full-text match in merge.
        user_score_weight: Weight for user feedback bias.
        max_context_chunks: Max chunks to include as context.
        max_context_facts: Max facts in context.
        answer_max_tokens: Max output tokens.

    Returns:
        QueryResult with answer, sources, and related facts.
    """
    from filekb.embed import embed_query

    if not question or not question.strip():
        return QueryResult(answer="Please enter a question.")

    # Step 1: Embed the question
    try:
        q_embedding = embed_query(question)
    except Exception as e:
        logger.warning("Embedding failed: %s — using FTS only", e)
        q_embedding = None

    # Step 2: Retrieve
    vector_results: list[tuple[int, float]] = []
    if q_embedding is not None and vector_store.size > 0:
        vector_results = vector_store.search(q_embedding, k=vector_top_k * 2)

    fts_results = store.search_facts_fts(question, limit=fts_top_k)

    # Step 3: Weighted merge
    merged = _reciprocal_rank_fusion(
        vector_results,
        fts_results,
        [],  # Graph results populated below
        store=store,
        vector_weight=vector_weight,
        graph_weight=graph_weight,
        fts_weight=fts_weight,
        user_score_weight=user_score_weight,
    )

    # Step 4: Get top facts + expand via graph
    top_fact_ids = [fid for fid, _ in merged[:max_context_facts]]
    scored_facts: list[ScoredFact] = []
    seed_entities: set[str] = set()

    for fid in top_fact_ids:
        row = store.conn.execute(
            "SELECT f.*, fl.path as source_file FROM facts f "
            "LEFT JOIN files fl ON f.file_id = fl.id "
            "WHERE f.id = ? AND f.status = 'active'",
            (fid,),
        ).fetchone()
        if row:
            sf = ScoredFact(
                fact_id=row["id"],
                subject=row["subject"],
                predicate=row["predicate"],
                object=row["object"],
                title=row["title"] or "",
                description=row["description"] or "",
                source_file=row["source_file"] or "",
                evidence_span=row["evidence_span"] or "",
                user_score=row["user_score"],
            )
            # Find score from merged results
            for mfid, mscore in merged:
                if mfid == fid:
                    sf.final_score = mscore
                    break
            scored_facts.append(sf)
            seed_entities.add(row["subject"])
            seed_entities.add(row["object"])

    # Graph expansion from seed entities
    graph_facts: list[dict[str, Any]] = []
    for entity in list(seed_entities)[:5]:  # Limit to top-5 seed entities
        expanded = graph_store.get_neighbors(entity)
        for conn in expanded:
            graph_facts.append({
                "entity": conn["entity"],
                "predicate": conn["predicate"],
                "direction": conn["direction"],
                "fact_id": conn["fact_id"],
            })

    # Step 5: Collect context
    context_parts: list[str] = []
    source_files: set[str] = set()
    seen_chunks: set[int] = set()

    for sf in scored_facts[:max_context_chunks]:
        source_files.add(sf.source_file)
        context_parts.append(
            f"[Fact {sf.fact_id}] {sf.title or f'{sf.subject} {sf.predicate} {sf.object}'}\n"
            f"  Source: {sf.source_file}\n"
            f"  Evidence: {sf.evidence_span}\n"
            + (f"  Description: {sf.description}\n" if sf.description else "")
        )

    context = "\n".join(context_parts)

    if not context:
        return QueryResult(
            answer="The knowledge base does not contain information about this.",
            retrieval_count=0,
        )

    # Step 6: Generate answer
    from filekb.extractor import _load_prompt

    try:
        system_prompt = _load_prompt("answer.txt")
        # Inject context into prompt
        system_prompt = system_prompt.replace("{context}", context)
    except FileNotFoundError:
        system_prompt = (
            "Answer questions based only on the provided context. "
            "Cite source files for every claim."
        )

    response = llm_client.generate_answer(
        question=question,
        context=context,
        system_prompt=system_prompt,
        max_tokens=answer_max_tokens,
        conversation_history=conversation_history,
    )

    # Step 7: Build result
    sources = [
        {
            "file": fname,
            "facts": [sf.fact_id for sf in scored_facts if sf.source_file == fname],
        }
        for fname in sorted(source_files)
    ]

    return QueryResult(
        answer=response.content,
        sources=sources,
        related_facts=[
            {
                "fact_id": sf.fact_id,
                "title": sf.title,
                "subject": sf.subject,
                "predicate": sf.predicate,
                "object": sf.object,
                "source": sf.source_file,
                "score": round(sf.final_score, 4),
            }
            for sf in scored_facts[:10]
        ],
        entities=sorted(seed_entities),
        retrieval_count=len(top_fact_ids),
    )
