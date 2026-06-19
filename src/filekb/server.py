"""FastAPI REST API server for FileKB — multi knowledge-base support.

Each KB group gets its own SQLite DB, FAISS index, and graph.
All endpoints accept ?kb= 参数来选择知识库，默认为"默认"。
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

# Per-KB state
KBState = dict[str, Any]


def _kb_db_path(base_dir: str, kb_name: str) -> str:
    """Derive database path for a knowledge base."""
    safe = kb_name.replace("/", "_").replace(" ", "_")
    return str(Path(base_dir) / f"{safe}.db")


def _kb_faiss_dir(base_dir: str, kb_name: str) -> str:
    safe = kb_name.replace("/", "_").replace(" ", "_")
    return str(Path(base_dir) / "faiss" / safe)


def _collect_kb_groups(cfg: Config) -> dict[str, list[dict]]:
    """Group directories by their KB group."""
    groups: dict[str, list[dict]] = {}
    for d in cfg.directories:
        g = d.group or "默认"
        groups.setdefault(g, []).append({
            "path": d.path,
            "recursive": d.recursive,
            "exclude_patterns": d.exclude_patterns,
        })
    if not groups:
        groups["默认"] = []
    return groups


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize per-KB stores based on directory groups."""
    cfg = load_config()
    app.state.config = cfg

    embed_configure(
        backend=cfg.embedding.backend,
        model_name=cfg.embedding.model,
        omxl_url=cfg.embedding.omxl_url,
        device=cfg.embedding.device,
        normalize=cfg.embedding.normalize,
    )
    app.state.llm_client = LLMClient(
        base_url=cfg.llm.base_url, model=cfg.llm.model,
        api_key=cfg.llm.api_key, timeout=cfg.llm.timeout,
        max_retries=cfg.llm.max_retries,
    )

    # Per-KB state
    kb_groups = _collect_kb_groups(cfg)
    app.state.kb_names = sorted(kb_groups.keys())
    app.state.kb_stores: dict[str, Store] = {}
    app.state.kb_vector_stores: dict[str, VectorStore] = {}
    app.state.kb_graph_stores: dict[str, GraphStore] = {}

    db_base = str(Path(cfg.database.path).expanduser().parent)

    for kb_name in app.state.kb_names:
        db_path = kb_name == "默认" and cfg.database.path or _kb_db_path(db_base, kb_name)
        store = Store(db_path)
        vs = VectorStore(dimension=1024)
        faiss_dir = _kb_faiss_dir(db_base, kb_name)
        if not vs.load(faiss_dir):
            vs.rebuild_from_sqlite(store)
        gs = GraphStore()
        _rebuild_graph_for_kb(store, gs)

        app.state.kb_stores[kb_name] = store
        app.state.kb_vector_stores[kb_name] = vs
        app.state.kb_graph_stores[kb_name] = gs

    logger.info("FileKB server started: %d KB(s) — %s",
                len(app.state.kb_names), ", ".join(app.state.kb_names))
    logger.info("API: http://localhost:9494")

    yield

    # Shutdown
    for store in app.state.kb_stores.values():
        store.close()
    app.state.llm_client.close()


app = FastAPI(
    title="FileKB API",
    version="0.1.0",
    description="File-driven personal knowledge base API",
    lifespan=lifespan,
)


def _rebuild_graph_for_kb(store: Store, gs: GraphStore):
    rows = store.conn.execute(
        "SELECT id, subject, predicate, object, confidence FROM facts WHERE status='active'"
    ).fetchall()
    gs.rebuild_from_facts([dict(r) for r in rows])
    logger.debug("Graph rebuilt: %d nodes, %d edges", gs.node_count, gs.edge_count)


# ============================================================================
# Request/Response models
# ============================================================================


class AskRequest(BaseModel):
    question: str
    top_k: int = Field(default=20, ge=1, le=100)
    kb: str = Field(default="默认")


class AskResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]] = []
    related_facts: list[dict[str, Any]] = []
    retrieval_count: int = 0


class IndexRequest(BaseModel):
    paths: list[str] | None = None
    dlq: bool = False
    kb: str = Field(default="默认")


class IndexResponse(BaseModel):
    run_id: int
    status: str


class FeedbackRequest(BaseModel):
    answer_id: str | None = None
    type: str = Field(..., pattern="^(positive|negative)$")
    reason: str | None = None
    cited_facts: list[dict[str, Any]] = []
    kb: str = Field(default="默认")


class EntityMergeRequest(BaseModel):
    proposal_id: int
    canonical_name: str | None = None
    kb: str = Field(default="默认")


class SettingsUpdateRequest(BaseModel):
    section: str
    data: dict


# ============================================================================
# Helpers
# ============================================================================


def _resolve_kb(request: Request, kb_override: str | None = None) -> str:
    """Resolve KB name: request param > model field > default."""
    if kb_override and kb_override != "默认":
        return kb_override
    # Try to get from query param
    kb = request.query_params.get("kb", "默认")
    if kb not in request.app.state.kb_stores:
        raise HTTPException(404, f"知识库 '{kb}' 不存在。可用: {request.app.state.kb_names}")
    return kb


def _kb_store(request: Request, kb: str) -> Store:
    if kb not in request.app.state.kb_stores:
        raise HTTPException(404, f"知识库 '{kb}' 不存在。可用: {request.app.state.kb_names}")
    return request.app.state.kb_stores[kb]

def _kb_vs(request: Request, kb: str) -> VectorStore:
    if kb not in request.app.state.kb_vector_stores:
        raise HTTPException(404, f"知识库 '{kb}' 不存在。")
    return request.app.state.kb_vector_stores[kb]

def _kb_gs(request: Request, kb: str) -> GraphStore:
    if kb not in request.app.state.kb_graph_stores:
        raise HTTPException(404, f"知识库 '{kb}' 不存在。")
    return request.app.state.kb_graph_stores[kb]


# ============================================================================
# POST /ask
# ============================================================================


@app.post("/ask", response_model=AskResponse)
def ask_endpoint(req: AskRequest, request: Request):
    from filekb.query import query as run_query

    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    vs = _kb_vs(request, kb)
    gs = _kb_gs(request, kb)
    cfg = request.app.state.config

    result = run_query(
        question=req.question, store=store, vector_store=vs, graph_store=gs,
        llm_client=request.app.state.llm_client, vector_top_k=req.top_k,
        fts_top_k=cfg.query.fts_top_k, vector_weight=cfg.query.vector_weight,
        graph_weight=cfg.query.graph_weight, fts_weight=cfg.query.fts_weight,
        user_score_weight=cfg.query.user_score_weight,
        max_context_chunks=cfg.query.max_context_chunks,
        max_context_facts=cfg.query.max_context_facts,
        answer_max_tokens=cfg.query.answer_max_tokens,
    )
    return AskResponse(answer=result.answer, sources=result.sources,
                       related_facts=result.related_facts, retrieval_count=result.retrieval_count)


# ============================================================================
# POST /ask/stream
# ============================================================================


@app.post("/ask/stream")
async def ask_stream_endpoint(req: AskRequest, request: Request):
    from filekb.query import query as run_query
    from filekb.extractor import _load_prompt

    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    vs = _kb_vs(request, kb)
    gs = _kb_gs(request, kb)
    cfg = request.app.state.config

    result = run_query(
        question=req.question, store=store, vector_store=vs, graph_store=gs,
        llm_client=request.app.state.llm_client, vector_top_k=req.top_k,
        answer_max_tokens=cfg.query.answer_max_tokens,
    )

    ctx_parts = []
    for sf in result.related_facts:
        title = sf.get("title", "") or "{} {} {}".format(
            sf.get("subject", "?"), sf.get("predicate", "?"), sf.get("object", "?"))
        ctx_parts.append("[来源: {}] {}".format(sf.get("source", "?"), title))
    context_text = "\n".join(ctx_parts) if ctx_parts else "无相关上下文"

    try:
        sys_prompt = _load_prompt("answer.txt").replace("{context}", context_text)
    except FileNotFoundError:
        sys_prompt = "基于提供的上下文回答问题，为每个声明引用源文件。"

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": req.question},
    ]

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            for token_text, _finish in request.app.state.llm_client.chat_stream(
                messages, max_tokens=cfg.query.answer_max_tokens,
            ):
                if token_text:
                    yield f"event: token\ndata: {json.dumps({'token': token_text})}\n\n"
            yield f"event: done\ndata: {json.dumps({'sources': result.sources, 'related_facts': result.related_facts})}\n\n"
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
    kb: str = Query(default="默认"),
):
    kb = _resolve_kb(request, kb)
    gs = _kb_gs(request, kb)
    # Fuzzy search first
    matches = gs.search_entities(entity, limit=10)
    if not matches:
        return {"nodes": [], "edges": []}
    # Pick the best match (shortest name that contains the query)
    best = min(matches, key=len)
    return gs.expand(best, hops=hop)


# ============================================================================
# POST /index
# ============================================================================


@app.post("/index", response_model=IndexResponse)
def index_endpoint(req: IndexRequest, request: Request):
    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    run_id = store.start_run()

    if req.dlq:
        from filekb.resilience import process_dlq as run_dlq
        processed = run_dlq(store)
        store.finish_run(run_id, "completed")
        return IndexResponse(run_id=run_id, status=f"dlq_processed_{processed}")

    store.finish_run(run_id, "completed")
    return IndexResponse(run_id=run_id, status="completed")


# ============================================================================
# GET /status
# ============================================================================


@app.get("/status")
def status_endpoint(request: Request, kb: str = Query(default="默认")):
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)
    vs = _kb_vs(request, kb)
    gs = _kb_gs(request, kb)

    health = {
        "llm_server": request.app.state.llm_client.health_check()["status"],
        "sqlite": {
            "path": str(store.db_path),
            "size_mb": round(os.path.getsize(store.db_path) / 1024 / 1024, 2)
            if os.path.exists(store.db_path) else 0,
        },
        "faiss": {"vectors": vs.size, "dimension": vs.dimension},
        "graph": {"nodes": gs.node_count, "edges": gs.edge_count},
    }
    last_run = store.get_last_run()

    return {
        "kb": kb,
        "kb_list": request.app.state.kb_names,
        "files_total": store.get_file_count(),
        "facts_total": store.get_fact_count(),
        "health": health,
        "last_run": (
            {"id": last_run["id"], "status": last_run["status"],
             "files_total": last_run["files_total"], "files_changed": last_run["files_changed"],
             "facts_added": last_run["facts_added"], "started_at": last_run["started_at"],
             "finished_at": last_run["finished_at"] if last_run["finished_at"] else None}
            if last_run else None
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
    kb: str = Query(default="默认"),
):
    kb = _resolve_kb(request, kb)
    rows = _kb_store(request, kb).get_facts_by_entity(entity, limit=limit)
    return {"facts": [{"id": r["id"], "subject": r["subject"], "predicate": r["predicate"],
                       "object": r["object"], "title": r["title"],
                       "confidence": r["confidence"], "user_score": r["user_score"]} for r in rows]}


# ============================================================================
# POST /feedback
# ============================================================================


@app.post("/feedback")
def feedback_endpoint(req: FeedbackRequest, request: Request):
    kb = _resolve_kb(request, req.kb)
    from filekb.personalization import apply_feedback
    cfg_delta = request.app.state.config.personalization.feedback.delta
    updated = apply_feedback(_kb_store(request, kb), req.cited_facts, req.type, delta=cfg_delta, reason=req.reason)
    return {"status": "ok", "facts_updated": updated}


# ============================================================================
# GET /entities/proposals
# ============================================================================


@app.get("/entities/proposals")
def entity_proposals_endpoint(request: Request, status: str = Query(default="proposed"), kb: str = Query(default="默认")):
    kb = _resolve_kb(request, kb)
    rows = _kb_store(request, kb).get_pending_proposals()
    return {"proposals": [{"id": r["id"], "entity_a": r["entity_a"], "entity_b": r["entity_b"],
                           "proposed_name": r["proposed_name"], "confidence": r["confidence"],
                           "status": r["status"]} for r in rows]}


# ============================================================================
# POST /entities/merge
# ============================================================================


@app.post("/entities/merge")
def entity_merge_endpoint(req: EntityMergeRequest, request: Request):
    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    gs = _kb_gs(request, kb)

    proposal = store.conn.execute("SELECT * FROM entity_proposals WHERE id=?", (req.proposal_id,)).fetchone()
    if not proposal:
        raise HTTPException(404, f"Proposal {req.proposal_id} not found")

    from_entity = proposal["entity_a"]
    to_entity = req.canonical_name or proposal["proposed_name"] or proposal["entity_b"]
    gs.merge_entities(from_entity, to_entity)
    store.update_proposal_status(req.proposal_id, "approved")
    store.conn.execute("UPDATE facts SET subject=? WHERE subject=?", (to_entity, from_entity))
    store.conn.execute("UPDATE facts SET object=? WHERE object=?", (to_entity, from_entity))
    store.conn.commit()
    return {"status": "ok", "merged_count": 1}


# ============================================================================
# POST /entities/reject
# ============================================================================


@app.post("/entities/reject")
def entity_reject_endpoint(req: EntityMergeRequest, request: Request):
    kb = _resolve_kb(request, req.kb)
    _kb_store(request, kb).update_proposal_status(req.proposal_id, "rejected")
    return {"status": "ok"}


# ============================================================================
# GET /logs
# ============================================================================


@app.get("/logs")
def logs_endpoint(request: Request, lines: int = Query(default=50, ge=1, le=500), level: str = Query(default="DEBUG")):
    cfg = request.app.state.config
    log_path = Path(cfg.logging.file).expanduser()
    if not log_path.exists():
        return {"lines": [], "message": "Log file not found"}

    level_rank = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
    min_rank = level_rank.get(level.upper(), 10)
    log_entries: list[dict[str, str]] = []

    with open(log_path, encoding="utf-8") as f:
        all_lines = f.readlines()
    for line in all_lines[-lines:]:
        entry_level = "INFO"
        for lvl in ["ERROR", "WARNING", "INFO", "DEBUG"]:
            if f"[{lvl}]" in line or f"[{lvl:7s}]" in line:
                entry_level = lvl; break
        if level_rank.get(entry_level, 0) >= min_rank:
            log_entries.append({"text": line.rstrip(), "level": entry_level})
    return {"lines": log_entries, "total": len(log_entries)}


# ============================================================================
# GET /settings
# ============================================================================


@app.get("/settings")
def settings_get(request: Request):
    cfg = request.app.state.config
    return {
        "llm": {"base_url": cfg.llm.base_url, "model": cfg.llm.model,
                "timeout": cfg.llm.timeout, "context_window": cfg.llm.context_window},
        "embedding": {"backend": cfg.embedding.backend, "model": cfg.embedding.model,
                      "omxl_url": cfg.embedding.omxl_url},
        "ocr": {"enabled": cfg.ocr.enabled, "languages": cfg.ocr.languages,
                "min_confidence": cfg.ocr.min_confidence},
        "directories": [{"path": d.path, "group": d.group, "recursive": d.recursive,
                         "exclude_patterns": d.exclude_patterns} for d in cfg.directories],
        "extraction": {"rounds": cfg.extraction.rounds, "max_chars_per_chunk": cfg.extraction.max_chars_per_chunk},
        "kb_names": request.app.state.kb_names,
    }


@app.post("/settings")
def settings_update(req: SettingsUpdateRequest, request: Request):
    import yaml
    cfg = request.app.state.config
    config_path = Path(cfg.database.path).expanduser().parent / "config.yaml"

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
                path=req.data["path"], group=req.data.get("group", "默认"),
                recursive=req.data.get("recursive", True),
                exclude_patterns=req.data.get("exclude_patterns", [".git", "__pycache__", ".DS_Store"]),
            ))
        elif action == "remove":
            target = req.data.get("path", "")
            cfg.directories = [d for d in cfg.directories if d.path != target]

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg.model_dump(), f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        logger.warning("Failed to persist config: %s", e)

    return {"status": "ok", "section": req.section}
