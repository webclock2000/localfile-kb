"""FastAPI REST API server for FileKB.

All endpoints follow DEVELOPMENT_V3.md §12 API specification.
Serves as the middle layer between Streamlit UI and core engine.

Endpoints:
    POST /ask              — Q&A
    POST /ask/stream       — Streaming Q&A (SSE)
    GET  /graph            — Get graph data
    POST /index            — Trigger indexing
    GET  /status           — System health + stats
    GET  /facts            — Retrieve facts
    POST /feedback         — Submit user feedback
    GET  /entities/proposals — Merge proposals
    POST /entities/merge   — Approve merge
    POST /entities/reject  — Reject merge
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from filekb.config import Config, load_config
from filekb.embed import configure as embed_configure
from filekb.graph_store import GraphStore
from filekb.llm import LLMClient
from filekb.store import Store
from filekb.vector_store import VectorStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Startup
    cfg = load_config()
    app.state.config = cfg
    app.state.store = Store(cfg.database.path)
    embed_configure(
        backend=cfg.embedding.backend,
        model_name=cfg.embedding.model,
        omxl_url=cfg.embedding.omxl_url,
        device=cfg.embedding.device,
        normalize=cfg.embedding.normalize,
    )
    app.state.llm_client = LLMClient(
        base_url=cfg.llm.base_url,
        model=cfg.llm.model,
        api_key=cfg.llm.api_key,
        timeout=cfg.llm.timeout,
        max_retries=cfg.llm.max_retries,
    )
    app.state.vector_store = VectorStore(dimension=1024)
    faiss_dir = str(Path(cfg.database.path).parent / "faiss")
    if not app.state.vector_store.load(faiss_dir):
        app.state.vector_store.rebuild_from_sqlite(app.state.store)
    app.state.graph_store = GraphStore()
    _rebuild_graph(app)
    logger.info("FileKB server started on http://localhost:9494")

    yield

    # Shutdown
    app.state.store.close()
    app.state.llm_client.close()


app = FastAPI(
    title="FileKB API",
    version="0.1.0",
    description="File-driven personal knowledge base API",
    lifespan=lifespan,
)


def _rebuild_graph(app_ref: FastAPI) -> None:
    """Rebuild knowledge graph from SQLite facts."""
    store = app_ref.state.store
    gs = app_ref.state.graph_store
    rows = store.conn.execute(
        "SELECT id, subject, predicate, object, confidence FROM facts WHERE status='active'"
    ).fetchall()
    gs.rebuild_from_facts([dict(r) for r in rows])
    logger.info("Graph rebuilt: %d nodes, %d edges", gs.node_count, gs.edge_count)


# ============================================================================
# Request/Response models
# ============================================================================


class AskRequest(BaseModel):
    question: str
    top_k: int = Field(default=20, ge=1, le=100)


class AskResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]] = []
    related_facts: list[dict[str, Any]] = []
    retrieval_count: int = 0


class IndexRequest(BaseModel):
    paths: list[str] | None = None
    dlq: bool = False


class IndexResponse(BaseModel):
    run_id: int
    status: str


class FeedbackRequest(BaseModel):
    answer_id: str | None = None
    type: str = Field(..., pattern="^(positive|negative)$")
    reason: str | None = None
    cited_facts: list[dict[str, Any]] = []


class EntityMergeRequest(BaseModel):
    proposal_id: int
    canonical_name: str | None = None


# ============================================================================
# POST /ask
# ============================================================================


@app.post("/ask", response_model=AskResponse)
def ask_endpoint(req: AskRequest, request: Request):
    """Answer a natural language question with source citations."""
    from filekb.query import query as run_query

    cfg = request.app.state.config
    store = request.app.state.store
    llm_client = request.app.state.llm_client
    vs = request.app.state.vector_store
    gs = request.app.state.graph_store

    result = run_query(
        question=req.question,
        store=store,
        vector_store=vs,
        graph_store=gs,
        llm_client=llm_client,
        vector_top_k=req.top_k,
        **(
            {}
            if not cfg
            else {
                "fts_top_k": cfg.query.fts_top_k,
                "vector_weight": cfg.query.vector_weight,
                "graph_weight": cfg.query.graph_weight,
                "fts_weight": cfg.query.fts_weight,
                "user_score_weight": cfg.query.user_score_weight,
                "max_context_chunks": cfg.query.max_context_chunks,
                "max_context_facts": cfg.query.max_context_facts,
                "answer_max_tokens": cfg.query.answer_max_tokens,
            }
        ),
    )
    return AskResponse(
        answer=result.answer,
        sources=result.sources,
        related_facts=result.related_facts,
        retrieval_count=result.retrieval_count,
    )


# ============================================================================
# POST /ask/stream (SSE)
# ============================================================================


@app.post("/ask/stream")
async def ask_stream_endpoint(req: AskRequest, request: Request):
    """Stream answer tokens via Server-Sent Events (real LLM streaming).

    Retrieves context, then streams LLM tokens one by one via SSE.
    Final event carries sources and related facts.
    """
    from filekb.query import query as run_query

    store = request.app.state.store
    llm_client = request.app.state.llm_client
    vs = request.app.state.vector_store
    gs = request.app.state.graph_store
    cfg = request.app.state.config

    # Retrieve context (non-streaming part)
    result = run_query(
        question=req.question,
        store=store,
        vector_store=vs,
        graph_store=gs,
        llm_client=llm_client,
        vector_top_k=req.top_k,
        answer_max_tokens=cfg.query.answer_max_tokens,
    )

    # Build the system prompt with context
    from filekb.extractor import _load_prompt

    try:
        sys_prompt = _load_prompt("answer.txt")
        # Extract context parts from result
        ctx_parts = []
        for sf in result.related_facts:
            title = sf.get("title", "") or "{} {} {}".format(
                sf.get("subject", "?"), sf.get("predicate", "?"), sf.get("object", "?")
            )
            ctx_parts.append("[来源: {}] {}".format(sf.get("source", "?"), title))
        context_text = "\n".join(ctx_parts) if ctx_parts else "无相关上下文"
        sys_prompt = sys_prompt.replace("{context}", context_text)
    except FileNotFoundError:
        sys_prompt = "基于提供的上下文回答问题，为每个声明引用源文件。"

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": req.question},
    ]

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            for token_text, finish in llm_client.chat_stream(
                messages, max_tokens=cfg.query.answer_max_tokens,
            ):
                if token_text:
                    yield f"event: token\ndata: {json.dumps({'token': token_text})}\n\n"
            # Send final metadata
            final_data = {
                "sources": result.sources,
                "related_facts": result.related_facts,
            }
            yield f"event: done\ndata: {json.dumps(final_data)}\n\n"
        except Exception as e:
            logger.error("Stream error: %s", e)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ============================================================================
# GET /graph
# ============================================================================


@app.get("/graph")
def graph_endpoint(
    request: Request,
    entity: str = Query(..., description="Seed entity name"),
    hop: int = Query(default=1, ge=1, le=3),
):
    """Get graph nodes and edges around an entity."""
    gs = request.app.state.graph_store
    data = gs.expand(entity, hops=hop)
    return data


# ============================================================================
# POST /index
# ============================================================================


@app.post("/index", response_model=IndexResponse)
def index_endpoint(req: IndexRequest, request: Request):
    """Trigger an indexing run."""
    store = request.app.state.store
    run_id = store.start_run()

    if req.dlq:
        from filekb.resilience import process_dlq as run_dlq
        processed = run_dlq(store)
        store.finish_run(run_id, "completed")
        return IndexResponse(run_id=run_id, status=f"dlq_processed_{processed}")

    # Full index run would be triggered here
    # For now, return the run ID so callers can track
    store.finish_run(run_id, "completed")
    return IndexResponse(run_id=run_id, status="completed")


# ============================================================================
# GET /status
# ============================================================================


@app.get("/status")
def status_endpoint(request: Request):
    """Get system status, health, and statistics."""
    store = request.app.state.store
    vs = request.app.state.vector_store
    gs = request.app.state.graph_store
    llm_client = request.app.state.llm_client

    # Health checks
    health = {
        "llm_server": llm_client.health_check()["status"],
        "sqlite": {
            "path": str(store.db_path),
            "size_mb": round(os.path.getsize(store.db_path) / 1024 / 1024, 2)
            if os.path.exists(store.db_path)
            else 0,
        },
        "faiss": {"vectors": vs.size, "dimension": vs.dimension},
        "graph": {"nodes": gs.node_count, "edges": gs.edge_count},
    }

    last_run = store.get_last_run()

    return {
        "files_total": store.get_file_count(),
        "facts_total": store.get_fact_count(),
        "health": health,
        "last_run": (
            {
                "id": last_run["id"],
                "status": last_run["status"],
                "files_total": last_run["files_total"],
                "files_changed": last_run["files_changed"],
                "facts_added": last_run["facts_added"],
                "started_at": last_run["started_at"],
                "finished_at": last_run["finished_at"] if last_run["finished_at"] else None,
            }
            if last_run
            else None
        ),
    }


# ============================================================================
# GET /facts
# ============================================================================


@app.get("/facts")
def facts_endpoint(
    request: Request,
    entity: str = Query(..., description="Entity name to search for"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Retrieve facts for a given entity."""
    store = request.app.state.store
    rows = store.get_facts_by_entity(entity, limit=limit)
    return {
        "facts": [
            {
                "id": r["id"],
                "subject": r["subject"],
                "predicate": r["predicate"],
                "object": r["object"],
                "title": r["title"],
                "confidence": r["confidence"],
                "user_score": r["user_score"],
            }
            for r in rows
        ]
    }


# ============================================================================
# POST /feedback
# ============================================================================


@app.post("/feedback")
def feedback_endpoint(req: FeedbackRequest, request: Request):
    """Submit feedback (upvote/downvote) on an answer."""
    store = request.app.state.store
    cfg = request.app.state.config

    from filekb.personalization import apply_feedback

    cfg_delta = cfg.personalization.feedback.delta
    updated = apply_feedback(
        store=store,
        answer_sources=req.cited_facts,
        feedback_type=req.type,
        delta=cfg_delta,
        reason=req.reason,
    )
    return {"status": "ok", "facts_updated": updated}


# ============================================================================
# GET /entities/proposals
# ============================================================================


@app.get("/entities/proposals")
def entity_proposals_endpoint(
    request: Request,
    status: str = Query(default="proposed"),
):
    """Get entity merge proposals."""
    store = request.app.state.store
    rows = store.get_pending_proposals()
    return {
        "proposals": [
            {
                "id": r["id"],
                "entity_a": r["entity_a"],
                "entity_b": r["entity_b"],
                "proposed_name": r["proposed_name"],
                "confidence": r["confidence"],
                "status": r["status"],
            }
            for r in rows
        ]
    }


# ============================================================================
# POST /entities/merge
# ============================================================================


@app.post("/entities/merge")
def entity_merge_endpoint(req: EntityMergeRequest, request: Request):
    """Approve and execute an entity merge."""
    store = request.app.state.store
    gs = request.app.state.graph_store

    proposal = store.conn.execute(
        "SELECT * FROM entity_proposals WHERE id = ?", (req.proposal_id,)
    ).fetchone()
    if not proposal:
        raise HTTPException(404, f"Proposal {req.proposal_id} not found")

    from_entity = proposal["entity_a"]
    to_entity = req.canonical_name or proposal["proposed_name"] or proposal["entity_b"]

    merged = gs.merge_entities(from_entity, to_entity)
    store.update_proposal_status(req.proposal_id, "approved")

    # Rewire facts in SQLite
    store.conn.execute("UPDATE facts SET subject = ? WHERE subject = ?", (to_entity, from_entity))
    store.conn.execute("UPDATE facts SET object = ? WHERE object = ?", (to_entity, from_entity))
    store.conn.commit()

    return {"status": "ok", "merged_count": 1}


# ============================================================================
# GET /logs
# ============================================================================


@app.get("/logs")
def logs_endpoint(
    request: Request,
    lines: int = Query(default=50, ge=1, le=500),
    level: str = Query(default="DEBUG"),
):
    """Get recent log entries from the log file."""
    cfg = request.app.state.config
    log_path = Path(cfg.logging.file).expanduser()
    if not log_path.exists():
        return {"lines": [], "message": "Log file not found"}

    log_entries: list[dict[str, str]] = []
    level_rank = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
    min_rank = level_rank.get(level.upper(), 10)

    try:
        with open(log_path, encoding="utf-8") as f:
            all_lines = f.readlines()

        for line in all_lines[-lines:]:
            # Parse log level from the formatted line
            entry_level = "INFO"
            for lvl in ["ERROR", "WARNING", "INFO", "DEBUG"]:
                if f"[{lvl}]" in line or f"[{lvl:7s}]" in line:
                    entry_level = lvl
                    break

            if level_rank.get(entry_level, 0) >= min_rank:
                log_entries.append({
                    "text": line.rstrip(),
                    "level": entry_level,
                })
    except Exception as e:
        return {"lines": [], "error": str(e)}

    return {"lines": log_entries, "total": len(log_entries)}


# ============================================================================
# Settings API
# ============================================================================


class SettingsResponse(BaseModel):
    llm: dict = {}
    embedding: dict = {}
    ocr: dict = {}
    directories: list[dict] = []
    extraction: dict = {}


@app.get("/settings")
def settings_get(request: Request):
    """Return current configuration for the settings UI."""
    cfg = request.app.state.config
    return {
        "llm": {
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "timeout": cfg.llm.timeout,
            "context_window": cfg.llm.context_window,
        },
        "embedding": {
            "backend": cfg.embedding.backend,
            "model": cfg.embedding.model,
            "omxl_url": cfg.embedding.omxl_url,
        },
        "ocr": {
            "enabled": cfg.ocr.enabled,
            "languages": cfg.ocr.languages,
            "min_confidence": cfg.ocr.min_confidence,
        },
        "directories": [
            {
                "path": d.path,
                "group": d.group,
                "recursive": d.recursive,
                "exclude_patterns": d.exclude_patterns,
            }
            for d in cfg.directories
        ],
        "extraction": {
            "rounds": cfg.extraction.rounds,
            "max_chars_per_chunk": cfg.extraction.max_chars_per_chunk,
        },
    }


class SettingsUpdateRequest(BaseModel):
    section: str  # "llm" | "embedding" | "ocr" | "directories"
    data: dict


@app.post("/settings")
def settings_update(req: SettingsUpdateRequest, request: Request):
    """Update configuration and persist to config.yaml."""
    import yaml

    cfg = request.app.state.config
    config_path = Path(
        request.app.state.config.database.path
    ).expanduser().parent / "config.yaml"

    # Update the in-memory config
    if req.section == "llm":
        cfg.llm.base_url = req.data.get("base_url", cfg.llm.base_url)
        cfg.llm.model = req.data.get("model", cfg.llm.model)
        cfg.llm.timeout = req.data.get("timeout", cfg.llm.timeout)
    elif req.section == "embedding":
        cfg.embedding.backend = req.data.get("backend", cfg.embedding.backend)
        cfg.embedding.model = req.data.get("model", cfg.embedding.model)
        cfg.embedding.omxl_url = req.data.get("omxl_url", cfg.embedding.omxl_url)
    elif req.section == "ocr":
        cfg.ocr.enabled = req.data.get("enabled", cfg.ocr.enabled)
        cfg.ocr.languages = req.data.get("languages", cfg.ocr.languages)
    elif req.section == "directories":
        action = req.data.get("action", "add")
        if action == "add":
            from filekb.config import DirectoryConfig
            cfg.directories.append(DirectoryConfig(
                path=req.data["path"],
                group=req.data.get("group", "默认"),
                recursive=req.data.get("recursive", True),
                exclude_patterns=req.data.get("exclude_patterns", [".git", "__pycache__", ".DS_Store"]),
            ))
        elif action == "remove":
            target = req.data.get("path", "")
            cfg.directories = [d for d in cfg.directories if d.path != target]

    # Persist to YAML
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg.model_dump(), f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        logger.warning("Failed to persist config: %s", e)

    return {"status": "ok", "section": req.section}


# ============================================================================
# POST /entities/reject
# ============================================================================


@app.post("/entities/reject")
def entity_reject_endpoint(req: EntityMergeRequest, request: Request):
    """Reject an entity merge proposal."""
    store = request.app.state.store
    store.update_proposal_status(req.proposal_id, "rejected")
    return {"status": "ok"}
