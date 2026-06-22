"""FastAPI REST API server for FileKB — multi knowledge-base support.

Each KB group gets its own SQLite DB, FAISS index, and graph.
All endpoints accept ?kb= 参数来选择知识库，默认为"默认"。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import filekb.extractor as extractor
import filekb.parser as parser
import filekb.splitter as splitter
import filekb.watcher as watcher
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
    """Group directories by their KB group. Include KBs from kb_meta even if empty."""
    groups: dict[str, list[dict]] = {}

    # Seed from kb_meta (KBs may exist without directories yet)
    for name in cfg.kb_meta:
        groups.setdefault(name, [])

    for d in cfg.directories:
        g = d.group or "默认"
        groups.setdefault(g, []).append({
            "path": d.path,
            "recursive": d.recursive,
            "exclude_patterns": d.exclude_patterns,
        })

    # Always ensure "默认" exists
    if "默认" not in groups:
        groups["默认"] = []

    return groups


def _repair_stale_runs(store: Store) -> None:
    """Mark any runs left in 'running' status as crashed.

    Daemon-thread crashes leave runs stuck at 'running' forever,
    which blocks new index requests (409 conflict)."""
    try:
        rows = store.conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status = 'running' AND finished_at IS NULL"
        ).fetchone()[0]
        if rows:
            store.conn.execute(
                "UPDATE runs SET status = 'crashed', finished_at = datetime('now') "
                "WHERE status = 'running' AND finished_at IS NULL"
            )
            store.conn.commit()
            logger.info("Repaired %d stale running run(s)", rows)
    except Exception:
        logger.exception("Failed to repair stale runs — DB may be locked or missing")


def _repair_stale_files(store: Store) -> None:
    """Reset files stuck in 'processing' back to 'pending'.

    If the daemon thread died mid-file, the file stays 'processing'
    forever and is skipped on subsequent runs.  This is the ONLY
    automatic repair — it recovers from genuine crashes.

    Files in 'failed', 'skipped', or 'dead' state are NOT reset here.
    'failed' files are retried by the index pipeline only if they still
    have retry_count < max_retries.  'skipped' (unsupported type) and
    'dead' (permanently failed) files are never auto-retried.

    To retry a specific failed/skipped file, use the re-index button
    in the Web UI or ``POST /files/{id}/reindex``.
    """
    try:
        rows = store.conn.execute(
            "SELECT COUNT(*) FROM files WHERE status = 'processing'"
        ).fetchone()[0]
        if rows:
            store.conn.execute(
                "UPDATE files SET status = 'pending' WHERE status = 'processing'"
            )
            store.conn.commit()
            logger.info("Reset %d processing file(s) back to pending (crash recovery)", rows)
    except Exception:
        logger.exception("Failed to repair stale files — DB may be locked or missing")


def _init_kb_state(app: FastAPI, kb_name: str) -> None:
    """Create per-KB Store / VectorStore / GraphStore for *kb_name* if missing."""
    if kb_name in app.state.kb_stores:
        return  # already initialized

    cfg = app.state.config
    db_base = str(Path(cfg.database.path).expanduser().parent)
    db_path = kb_name == "默认" and cfg.database.path or _kb_db_path(db_base, kb_name)
    faiss_dir = _kb_faiss_dir(db_base, kb_name)

    store = Store(db_path)

    # Clean up stale state from prior daemon-thread crashes
    _repair_stale_runs(store)
    _repair_stale_files(store)

    # Clear any stale stop signals from previous crashed runs
    try:
        store.clear_pending_stops()
    except Exception:
        pass
    vs = VectorStore(dimension=1024)
    if not vs.load(faiss_dir):
        # Backfill: compute embeddings if facts exist but FAISS is empty
        fact_count = store.get_fact_count()
        if fact_count > 0:
            logger.info(
                "KB '%s': no saved FAISS index, backfilling %d fact embeddings...",
                kb_name, fact_count,
            )
            _compute_embeddings_for_kb(store)
        vs.rebuild_from_sqlite(store)
        vs.save(faiss_dir)
        logger.info("KB '%s': FAISS saved to %s", kb_name, faiss_dir)

    gs = GraphStore()
    _rebuild_graph_for_kb(store, gs)

    app.state.kb_stores[kb_name] = store
    app.state.kb_vector_stores[kb_name] = vs
    app.state.kb_graph_stores[kb_name] = gs
    if kb_name not in app.state.kb_names:
        app.state.kb_names = sorted(app.state.kb_names + [kb_name])
    logger.info("KB '%s' state initialized", kb_name)


def _remove_kb_state(app: FastAPI, kb_name: str) -> None:
    """Close and remove per-KB state for *kb_name*, including on-disk data."""
    import os
    import shutil

    cfg = app.state.config
    db_base = str(Path(cfg.database.path).expanduser().parent)
    db_path = kb_name == "默认" and cfg.database.path or _kb_db_path(db_base, kb_name)
    faiss_dir = _kb_faiss_dir(db_base, kb_name)

    # 1. Close in-memory state
    for attr, label in [
        ("kb_stores", "store"),
        ("kb_vector_stores", "vector"),
        ("kb_graph_stores", "graph"),
    ]:
        d = getattr(app.state, attr, {})
        obj = d.pop(kb_name, None)
        if obj is not None and hasattr(obj, "close"):
            obj.close()
    if kb_name in app.state.kb_names:
        app.state.kb_names.remove(kb_name)

    # 2. Delete on-disk data (SQLite + WAL/SHM + FAISS index)
    for label, path in [
        ("DB", db_path),
        ("DB-WAL", db_path + "-wal"),
        ("DB-SHM", db_path + "-shm"),
    ]:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Failed to delete %s %s: %s", label, path, e)

    if os.path.isdir(faiss_dir):
        try:
            shutil.rmtree(faiss_dir)
        except OSError as e:
            logger.warning("Failed to delete FAISS dir %s: %s", faiss_dir, e)

    logger.info("KB '%s' state and data removed", kb_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize per-KB stores based on directory groups."""
    import threading

    # Global exception hook for daemon threads — last-resort catcher
    # for anything that slips past explicit except blocks.
    _original_hook = threading.excepthook
    def _filekb_excepthook(args):
        logger.critical(
            "Unhandled exception in threading (daemon thread may have died silently):\n"
            "  Thread: %s\n  Type: %s\n  Value: %s\n  Traceback:\n%s",
            args.thread.name if args.thread else "?",
            args.exc_type.__name__ if args.exc_type else "?",
            args.exc_value,
            args.exc_traceback,
        )
        # Still call original hook for stderr visibility
        _original_hook(args)
    threading.excepthook = _filekb_excepthook
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
    app.state.kb_stop_events: dict[str, Any] = {}  # KB name → threading.Event

    for kb_name in app.state.kb_names:
        _init_kb_state(app, kb_name)

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


def _compute_embeddings_for_kb(store: Store) -> int:
    """Compute and persist embeddings for all facts that lack them.

    Returns number of facts updated.
    """
    from filekb.embed import embed_fact

    batch_size = 100
    updated = 0
    total_without = 0

    while True:
        batch = store.get_facts_without_embeddings(limit=batch_size, offset=0)
        if not batch:
            break

        total_without += len(batch)

        for row in batch:
            fact_id = row["id"]
            subject = row["subject"] or ""
            predicate = row["predicate"] or ""
            obj = row["object"] or ""
            title = row["title"] or ""
            description = row["description"] or ""

            try:
                emb_bytes = embed_fact(subject, predicate, obj, title, description)
                store.update_fact_embedding(fact_id, emb_bytes)
                updated += 1
            except Exception as exc:
                logger.error("Embedding failed for fact %d: %s", fact_id, exc)

        store.conn.commit()
        logger.debug("Embeddings progress: %d/%d facts", updated, total_without)

    if updated:
        logger.info("Computed %d embeddings for KB", updated)
    return updated


# ============================================================================
# Request/Response models
# ============================================================================


class AskRequest(BaseModel):
    question: str
    top_k: int = Field(default=20, ge=1, le=100)
    kb: str = Field(default="默认")
    session_id: str | None = None   # 传递会话ID以支持多轮对话上下文


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
    files_processed: int = 0
    files_changed: int = 0
    files_skipped: int = 0
    files_deleted: int = 0
    facts_added: int = 0
    facts_removed: int = 0
    entity_proposals: int = 0
    entity_merged: int = 0
    entity_suspects: int = 0
    skip_reasons: dict[str, int] = Field(default_factory=dict)


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

    # Fetch conversation history for multi-turn context
    conversation_history: list[dict] | None = None
    if req.session_id:
        history = store.get_chat_history(kb, req.session_id)
        if history:
            conversation_history = [
                {"role": m["role"], "content": m["content"]}
                for m in history
            ]

    result = run_query(
        question=req.question, store=store, vector_store=vs, graph_store=gs,
        llm_client=request.app.state.llm_client, vector_top_k=req.top_k,
        fts_top_k=cfg.query.fts_top_k, vector_weight=cfg.query.vector_weight,
        graph_weight=cfg.query.graph_weight, fts_weight=cfg.query.fts_weight,
        user_score_weight=cfg.query.user_score_weight,
        max_context_chunks=cfg.query.max_context_chunks,
        max_context_facts=cfg.query.max_context_facts,
        answer_max_tokens=cfg.query.answer_max_tokens,
        conversation_history=conversation_history,
    )
    return AskResponse(answer=result.answer, sources=result.sources,
                       related_facts=result.related_facts, retrieval_count=result.retrieval_count)


# ============================================================================
# POST /ask/stream
# ============================================================================


@app.post("/ask/stream")
async def ask_stream_endpoint(req: AskRequest, request: Request):
    from filekb.extractor import _load_prompt
    from filekb.query import query as run_query

    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    vs = _kb_vs(request, kb)
    gs = _kb_gs(request, kb)
    cfg = request.app.state.config

    # Fetch conversation history for multi-turn context
    conversation_history: list[dict] | None = None
    if req.session_id:
        history = store.get_chat_history(kb, req.session_id)
        if history:
            conversation_history = [
                {"role": m["role"], "content": m["content"]}
                for m in history
            ]

    result = run_query(
        question=req.question, store=store, vector_store=vs, graph_store=gs,
        llm_client=request.app.state.llm_client, vector_top_k=req.top_k,
        answer_max_tokens=cfg.query.answer_max_tokens,
        conversation_history=conversation_history,
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

    messages = []
    if conversation_history:
        for m in conversation_history:
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "system", "content": sys_prompt})
    messages.append({"role": "user", "content": req.question})

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
# Index Pipeline
# ============================================================================


def _finalize_run(
    store: Store,
    vs: VectorStore,
    gs: GraphStore,
    kb_name: str,
    cfg: Config,
    files_changed: int,
    total_files: int,
    total_facts: int,
    files_skipped: int,
    run_id: int,
    start_ts: float,
) -> None:
    """Finalize an index run: update run record, then compute embeddings & rebuild indices.

    Run statistics are persisted FIRST (lightweight SQL, near-zero failure rate).
    Heavy operations (embeddings + FAISS/Graph rebuild) come after so that a crash
    here doesn't lose the file/fact counts — the next run will auto-catch-up via
    ``_compute_embeddings_for_kb``.
    """
    # ── 1. Persist run stats FIRST — lightweight, almost never fails ──
    store.update_run(
        run_id,
        files_total=total_files,
        files_changed=files_changed,
        facts_added=total_facts,
    )

    # ── 2. Compute & persist embeddings (catch-up handles orphan facts from prior crashes) ──
    logger.info("Computing embeddings for facts without them...")
    _compute_embeddings_for_kb(store)

    # ── 3. Rebuild search indices ──
    logger.info("Rebuilding FAISS index and knowledge graph...")
    vs.rebuild_from_sqlite(store)
    faiss_dir = _kb_faiss_dir(str(Path(cfg.database.path).expanduser().parent), kb_name)
    vs.save(faiss_dir)
    _rebuild_graph_for_kb(store, gs)
    logger.info("Indices rebuilt: %d vectors, %d nodes, %d edges",
                vs.size, gs.node_count, gs.edge_count)

    elapsed = time.time() - start_ts
    logger.info(
        "Index run %d complete: %d files (%d skipped, %d changed), %d facts, %.1fs",
        run_id, total_files, files_skipped, files_changed, total_facts, elapsed,
    )


def _run_index_pipeline(
    kb_name: str,
    store: Store,
    llm_client: LLMClient,
    vs: VectorStore,
    gs: GraphStore,
    cfg: Config,
    dlq_only: bool = False,
    stop_event: Any = None,  # threading.Event or None
    processing_file_tracker: dict[str, str] | None = None,  # mutable dict for status tracking
) -> dict[str, Any]:
    """Run the full indexing pipeline for a knowledge base.

    Orchestrates: scan → detect_changes → parse → chunk → extract →
    dedup → store → rebuild_indices.

    One file failure does not kill the entire run — errors are logged
    and the file is marked as failed, then processing continues.

    If stop_event is set during the file loop, the pipeline finishes the
    current file (to avoid a half-processed state), then exits gracefully.
    Unprocessed files remain in their current status and will be retried
    on the next run.

    processing_file_tracker is an optional mutable dict; the pipeline
    writes the basename of the file it is currently processing to
    tracker["current"] so the status endpoint can display it.
    """

    def _should_stop() -> bool:
        # Check in-memory event (fast path)
        if stop_event is not None and stop_event.is_set():
            return True
        # Check persistent DB flag (survives server restart)
        try:
            return store.is_stop_requested(run_id)
        except Exception:
            return False

    run_id = store.start_run()
    start_ts = time.time()
    total_files = 0
    total_facts = 0
    files_changed = 0
    files_skipped = 0
    skip_reasons: dict[str, int] = {}  # reason → count
    all_files_to_process: list[str] = []
    all_deleted: list[str] = []

    # ── Repair: reset stuck "processing" files to "pending" ──
    # This is also done at server startup (_init_kb_state), but a daemon
    # thread crash mid-session can leave files "processing" without a
    # server restart.  Always repair before scanning.
    try:
        rows = store.conn.execute(
            "SELECT COUNT(*) FROM files WHERE status = 'processing'"
        ).fetchone()[0]
        if rows:
            store.conn.execute(
                "UPDATE files SET status = 'pending' WHERE status = 'processing'"
            )
            store.conn.commit()
            logger.info("Repaired %d stuck processing file(s) → pending", rows)
    except Exception:
        logger.exception("Failed to repair stuck processing files")

    try:
        # ── DLQ-only mode: retry failed chunks, skip file scan ──
        if dlq_only:
            from filekb.resilience import process_dlq as run_dlq
            processed = run_dlq(store)
            store.finish_run(run_id, "completed")
            return {"run_id": run_id, "dlq_processed": processed}

        # ── 1. Collect directories for this KB ──
        kb_dirs = [d for d in cfg.directories if (d.group or "默认") == kb_name]
        if not kb_dirs:
            logger.info("KB '%s' has no configured directories — nothing to index", kb_name)
            store.finish_run(run_id, "completed")
            return {"run_id": run_id, "files_processed": 0, "facts_added": 0,
                    "files_skipped": 0, "skip_reasons": {},
                    "message": "没有配置监控目录，请在知识库管理中添加目录。"}

        # ── 2. Scan each directory and detect changes ──
        previous_hashes = watcher.load_previous_hashes(store)

        for dir_cfg in kb_dirs:
            dir_path = Path(dir_cfg.path).expanduser()
            if not dir_path.exists():
                logger.warning("Directory not found, skipping: %s", dir_path)
                continue

            exclude = set(dir_cfg.exclude_patterns) if dir_cfg.exclude_patterns else None
            current = watcher.scan_directory(dir_path, dir_cfg.recursive, exclude)

            # Filter to files within this directory
            dir_prefix = str(dir_path)
            current_in_dir = {p: h for p, h in current.items() if p.startswith(dir_prefix)}
            previous_in_dir = {p: h for p, h in previous_hashes.items() if p.startswith(dir_prefix)}

            changes = watcher.detect_changes(current_in_dir, previous_in_dir)
            all_files_to_process.extend(changes["added"] + changes["modified"])
            all_deleted.extend(changes["deleted"])

            logger.info(
                "KB '%s' dir '%s': +%d ~%d -%d =%d",
                kb_name, dir_path.name,
                len(changes["added"]), len(changes["modified"]),
                len(changes["deleted"]), len(changes["unchanged"]),
            )

        # ── Also include files that genuinely need (re)processing ──
        # "pending" files are new (never indexed) or were reset from
        # "processing" by crash recovery.  They must be picked up here
        # because upsert_file already wrote their SHA256 into the DB,
        # so detect_changes won't flag them as "added".
        #
        # "failed" files are retried ONLY if they still have retry budget
        # (retry_count < MAX_FILE_RETRIES).  Each retry increments the
        # counter; after the budget is exhausted the file is marked "dead".
        #
        # "skipped" (unsupported type, empty content, encrypted) and
        # "dead" (permanent failure) files are NEVER auto-retried.
        # They can only be re-indexed via the explicit re-index button
        # or API, which resets their status to "pending".
        MAX_FILE_RETRIES = 3

        retry_files: list[str] = []
        dead_files: list[str] = []

        # ── pending files: new or crash-recovered ──
        for row in store.get_files_by_status("pending"):
            p = row["path"]
            if Path(p).exists() and any(
                p.startswith(str(Path(d.path).expanduser()))
                for d in kb_dirs
            ):
                retry_files.append(p)

        # ── failed files: only if within retry budget ──
        for row in store.get_files_by_status("failed"):
            p = row["path"]
            if not Path(p).exists():
                continue
            if not any(p.startswith(str(Path(d.path).expanduser())) for d in kb_dirs):
                continue
            file_retry_count = row["retry_count"] if "retry_count" in row.keys() else 0
            if file_retry_count < MAX_FILE_RETRIES:
                retry_files.append(p)
                logger.info(
                    "Retrying failed file (attempt %d/%d): %s",
                    file_retry_count + 1, MAX_FILE_RETRIES, p,
                )
            else:
                # Exhausted retries — mark as dead
                store.mark_file_dead(
                    row["id"],
                    f"重试 {file_retry_count} 次后仍然失败: {row['error_msg'] or '未知错误'}",
                )
                dead_files.append(p)
                logger.warning("Marked as dead (retries exhausted): %s", p)

        if retry_files:
            logger.info(
                "Re-processing %d files (%d pending + %d failed within budget)",
                len(retry_files),
                sum(1 for f in retry_files
                    if any(r["path"] == f and r["status"] == "pending"
                           for r in store.get_files_by_status("pending"))),
                sum(1 for f in retry_files
                    if any(r["path"] == f and r["status"] == "failed"
                           for r in store.get_files_by_status("failed"))),
            )
            # Avoid duplicates with SHA256-detected changes
            existing = set(all_files_to_process)
            for p in retry_files:
                if p not in existing:
                    all_files_to_process.append(p)
                    existing.add(p)

        if dead_files:
            logger.info("%d file(s) marked dead (permanently skipped)", len(dead_files))

        if not all_files_to_process and not all_deleted:
            logger.info("KB '%s': no changes detected", kb_name)
            store.finish_run(run_id, "completed")
            return {"run_id": run_id, "files_processed": 0, "facts_added": 0,
                    "files_skipped": 0, "skip_reasons": {},
                    "message": "没有检测到文件变更。"}

        # ── 3. Process added/modified files ──
        for file_path_str in all_files_to_process:
            # ── Stop check: finish current file, then exit ──
            if _should_stop():
                logger.info(
                    "Stop requested — %d files done, %d facts. "
                    "Exiting pipeline gracefully.",
                    total_files, total_facts,
                )
                # file_id / file_facts are only defined after a file is
                # processed in this iteration.  When stop arrives before
                # the first file, just finalize with zero progress.
                try:
                    _fid = file_id      # noqa: F823
                    _facts = file_facts
                except NameError:
                    pass
                else:
                    store.mark_file_indexed(_fid)
                    total_files += 1
                    total_facts += _facts
                    files_changed += 1
                try:
                    _finalize_run(store, vs, gs, kb_name, cfg, files_changed,
                                 total_files, total_facts, files_skipped, run_id, start_ts)
                except BaseException:
                    logger.exception(
                        "Finalize failed during stop for KB '%s' — "
                        "run stats already persisted, status kept as cancelled",
                        kb_name,
                    )
                store.finish_run(run_id, "cancelled")
                return {
                    "run_id": run_id,
                    "files_processed": total_files,
                    "files_changed": files_changed,
                    "facts_added": total_facts,
                    "files_deleted": 0,
                    "files_skipped": files_skipped,
                    "skip_reasons": skip_reasons,
                    "status": "cancelled",
                    "message": "索引已停止 — %d 个文件已处理，剩余将在下次启动时继续。" % total_files,
                }

            try:
                file_path = Path(file_path_str)
                # Track current file for status display
                if processing_file_tracker is not None:
                    processing_file_tracker[kb_name] = file_path.name
                file_size = file_path.stat().st_size

                # Skip files > 50MB
                if file_size > 50 * 1024 * 1024:
                    logger.info("Skipping large file (%d MB): %s", file_size // 1024 // 1024, file_path_str)
                    files_skipped += 1
                    skip_reasons["文件过大 (>50MB)"] = skip_reasons.get("文件过大 (>50MB)", 0) + 1
                    continue

                # Skip unsupported files
                if not parser.is_supported(file_path_str):
                    suffix = file_path.suffix or "(无后缀)"
                    logger.info("Skipping unsupported type (%s): %s", suffix, file_path_str)
                    files_skipped += 1
                    reason = f"不支持的文件类型 ({suffix})"
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                    continue

                # Find or create directory record
                dir_record = store.conn.execute(
                    "SELECT id FROM directories WHERE path = ?", (str(file_path.parent),)
                ).fetchone()
                if dir_record:
                    dir_id = dir_record["id"]
                else:
                    dir_id = store.add_directory(str(file_path.parent))

                # Get current hash
                file_hash = watcher.compute_sha256(file_path)

                # Upsert file record (status = processing)
                file_id = store.upsert_file(
                    path=file_path_str,
                    sha256=file_hash,
                    directory_id=dir_id,
                    file_size=file_size,
                    mtime=file_path.stat().st_mtime,
                    status="processing",
                )

                # Clean up old facts from previous failed/skipped attempts
                store.soft_delete_facts_by_file(file_id)
                store.delete_chunks_by_file(file_id)

                # ── Parse file ──
                try:
                    text = parser.parse_file(file_path)
                except ValueError as parse_err:
                    # Encrypted PDF, unsupported format with clear message
                    error_msg = str(parse_err)
                    store.update_file_status(file_id, "skipped", error_msg)
                    files_skipped += 1
                    skip_reasons[error_msg] = skip_reasons.get(error_msg, 0) + 1
                    logger.info("Skipping %s: %s", file_path.name, error_msg)
                    continue
                if not text or not text.strip():
                    store.update_file_status(file_id, "skipped", "文件为空或无法解析")
                    files_skipped += 1
                    skip_reasons["文件为空或无法解析"] = skip_reasons.get("文件为空或无法解析", 0) + 1
                    continue

                # ── Language detection ──
                is_chinese = splitter.detect_chinese(text)

                # ── Chunk text ──
                chunks = splitter.chunk_text(
                    text,
                    max_chars=cfg.extraction.max_chars_per_chunk,
                    overlap_chars=cfg.extraction.overlap_chars,
                )

                logger.debug("File %s: %d chars → %d chunks", file_path.name, len(text), len(chunks))

                file_facts = 0

                # ── Process each chunk ──
                for idx, chunk_text in enumerate(chunks):
                    try:
                        chunk_id = store.insert_chunk(file_id, idx, chunk_text)

                        # Extract facts via LLM
                        result = extractor.extract_facts(
                            client=llm_client,
                            chunk_text=chunk_text,
                            chunk_id=chunk_id,
                            rounds=cfg.extraction.rounds,
                            confidence_threshold=cfg.extraction.confidence_threshold,
                            extra_body={"enable_thinking": False},
                        )

                        if result.error:
                            store.update_chunk_status(chunk_id, "failed")
                            store.enqueue_failed_chunk(
                                file_id, chunk_id, result.error,
                                "INVALID_JSON" if "parse" in result.error.lower() else "EXTRACTION_ERROR",
                            )
                            continue

                        if result.truncated:
                            logger.warning(
                                "Chunk %d of %s may be truncated (finish_reason=length)",
                                idx, file_path.name,
                            )

                        # ── Insert facts ──
                        for fact in result.facts:
                            fact.source_file = file_path_str
                            fact.chunk_id = chunk_id
                            store.insert_fact(
                                file_id=file_id,
                                chunk_id=chunk_id,
                                subject=fact.subject,
                                predicate=fact.predicate,
                                object=fact.object,
                                title=fact.title,
                                description=fact.description,
                                evidence_span=fact.evidence_span,
                                confidence=fact.confidence,
                                tags=fact.tags,
                            )
                            file_facts += 1

                        store.update_chunk_status(chunk_id, "done")

                    except Exception as chunk_err:
                        logger.error(
                            "Chunk %d of %s failed: %s", idx, file_path.name, chunk_err
                        )
                        try:
                            store.enqueue_failed_chunk(
                                file_id, chunk_id if 'chunk_id' in dir() else 0,
                                str(chunk_err), "UNKNOWN",
                            )
                        except Exception:
                            pass

                # ── Mark file as indexed ──
                store.mark_file_indexed(file_id)
                total_files += 1
                total_facts += file_facts
                files_changed += 1

                logger.info(
                    "File done: %s (%d facts from %d chunks)",
                    file_path.name, file_facts, len(chunks),
                )

            except BaseException as file_err:
                logger.error("Failed to process file %s: %s", file_path_str, file_err)
                try:
                    if 'file_id' in dir():
                        # Increment retry count
                        new_count = store.increment_retry_count(file_id)
                        if new_count >= MAX_FILE_RETRIES:
                            store.mark_file_dead(
                                file_id,
                                f"重试 {new_count} 次后仍然失败: {file_err}",
                            )
                            logger.warning(
                                "File marked dead after %d retries: %s",
                                new_count, file_path_str,
                            )
                        else:
                            store.update_file_status(
                                file_id, "failed", str(file_err),
                            )
                except BaseException:
                    pass

        # ── 4. Process deleted files (soft-delete) ──
        for deleted_path in all_deleted:
            try:
                file_record = store.get_file_by_path(deleted_path)
                if file_record:
                    store.soft_delete_file(file_record["id"])
                    store.delete_chunks_by_file(file_record["id"])
                    files_changed += 1
                    logger.info("Soft-deleted: %s", deleted_path)
            except Exception as del_err:
                logger.error("Failed to soft-delete %s: %s", deleted_path, del_err)

        # ── 5-7. Finalize: embeddings + rebuild ──
        _finalize_run(store, vs, gs, kb_name, cfg, files_changed,
                     total_files, total_facts, files_skipped, run_id, start_ts)

        # ── 8. Entity resolution (if enabled) ──
        entity_proposals = 0
        entity_merged = 0
        if cfg.personalization.analyzer.run_after_index and gs.node_count > 1:
            from filekb.personalization import (
                apply_merges,
                generate_proposals,
                validate_with_llm,
            )
            logger.info("Running entity resolution...")
            try:
                proposals = generate_proposals(
                    store,
                    gs,
                    similarity_threshold=cfg.personalization.entity.similarity_threshold,
                )
                if proposals:
                    validated = validate_with_llm(
                        proposals,
                        llm_client,
                        auto_threshold=cfg.personalization.entity.auto_approve_threshold,
                    )
                    entity_merged = apply_merges(store, gs, validated)
                    entity_proposals = len(validated)
                    logger.info(
                        "Entity resolution done: %d proposals, %d merged",
                        entity_proposals, entity_merged,
                    )
            except Exception as e:
                logger.exception("Entity resolution failed (non-fatal): %s", e)

        # ── 9. Entity QA — suspect detection (zero LLM cost) ──
        entity_suspects = 0
        if gs.node_count > 0:
            from filekb.personalization import generate_suspect_proposals
            logger.info("Running entity quality assessment...")
            try:
                suspects = generate_suspect_proposals(store, gs)
                entity_suspects = len(suspects)
                logger.info("Entity QA done: %d suspects found", entity_suspects)
            except Exception as e:
                logger.exception("Entity QA failed (non-fatal): %s", e)

        # ── 10. DLQ maintenance — prune old entries ──
        try:
            pruned = store.prune_dlq(days=cfg.resilience.dlq.prune_days)
            if pruned > 0:
                logger.info("DLQ pruned: %d entries older than %d days",
                           pruned, cfg.resilience.dlq.prune_days)
        except Exception as e:
            logger.exception("DLQ pruning failed (non-fatal): %s", e)

        # ── Mark run as completed AFTER all steps (including entity resolution which calls LLM) ──
        store.finish_run(run_id, "completed")

        return {
            "run_id": run_id,
            "files_processed": total_files,
            "files_changed": files_changed,
            "facts_added": total_facts,
            "files_deleted": len(all_deleted),
            "files_skipped": files_skipped,
            "skip_reasons": skip_reasons,
            "entity_proposals": entity_proposals,
            "entity_merged": entity_merged,
            "entity_suspects": entity_suspects,
        }

    except BaseException:
        logger.exception("Index pipeline crashed for KB '%s'", kb_name)
        try:
            _finalize_run(store, vs, gs, kb_name, cfg, files_changed,
                         total_files, total_facts, files_skipped, run_id, start_ts)
            store.finish_run(run_id, "crashed")
        except BaseException:
            try:
                store.update_run(run_id, files_total=total_files, files_changed=files_changed,
                               facts_added=total_facts)
                store.finish_run(run_id, "crashed")
            except BaseException:
                pass
        raise


# ============================================================================
# POST /index
# ============================================================================


@app.post("/index", response_model=IndexResponse)
def index_endpoint(req: IndexRequest, request: Request):
    """Run the indexing pipeline for a knowledge base.

    Scans configured directories, detects new/modified/deleted files,
    parses content, extracts entities & relations via LLM, and rebuilds
    the FAISS vector index and knowledge graph.

    Set dlq=true to only retry previously failed chunks without scanning
    files again.

    The pipeline now runs in a background thread.  The endpoint returns
    immediately with the run_id.  Poll /status to track progress.
    Use POST /index/stop to request cancellation.
    """
    import threading

    kb = _resolve_kb(request, req.kb)

    # Track which file is currently being processed (for status display)
    # Clear previous tracker for this KB
    if not hasattr(request.app.state, "processing_files"):
        request.app.state.processing_files: dict[str, str] = {}
    request.app.state.processing_files[kb] = ""  # will be updated by pipeline

    # Reject if already running for this KB
    existing = request.app.state.kb_stop_events.get(kb)
    if existing is not None:
        # An Event exists and is NOT set → pipeline still running
        if isinstance(existing, threading.Event) and not existing.is_set():
            raise HTTPException(409, f"知识库「{kb}」正在索引中，请等待完成或先停止。")
        # Event IS set → pipeline is stopping. Clean up the stale event.
        if isinstance(existing, threading.Event) and existing.is_set():
            request.app.state.kb_stop_events.pop(kb, None)

    store = _kb_store(request, kb)
    vs = _kb_vs(request, kb)
    gs = _kb_gs(request, kb)
    cfg = request.app.state.config

    # Clear any stale stop signals from previous crashed runs
    try:
        store.clear_pending_stops()
    except Exception:
        pass

    stop_event = threading.Event()
    request.app.state.kb_stop_events[kb] = stop_event

    def _run_in_background():
        try:
            _run_index_pipeline(
                kb_name=kb,
                store=store,
                llm_client=request.app.state.llm_client,
                vs=vs,
                gs=gs,
                cfg=cfg,
                dlq_only=req.dlq,
                stop_event=stop_event,
                processing_file_tracker=request.app.state.processing_files,
            )
        except BaseException:
            # BaseException catches SystemExit, KeyboardInterrupt, and all
            # Exception subclasses — ensures the daemon thread never dies
            # silently. Also catches C-extension-level errors that bypass
            # the normal exception hierarchy.
            logger.exception("Index background thread crashed for KB '%s'", kb)
            try:
                store.finish_run(store.get_last_run()["id"], "crashed")
            except Exception:
                pass
        finally:
            # Clean up: remove stop event so next run can start
            request.app.state.kb_stop_events.pop(kb, None)
            if kb in request.app.state.processing_files:
                request.app.state.processing_files.pop(kb, None)

    t = threading.Thread(target=_run_in_background, daemon=True)
    t.start()

    # Get the run_id from the just-started run
    last_run = store.get_last_run()
    run_id = last_run["id"] if last_run else 0

    return IndexResponse(
        run_id=run_id,
        status=f"started: 索引任务已启动，后台执行中。",
    )


# ============================================================================
# POST /index/stop — request cancellation of a running index
# ============================================================================


@app.post("/index/stop")
def index_stop_endpoint(request: Request, kb: str = Query(default="默认")):
    """Request the indexing pipeline to stop gracefully.

    The pipeline will finish the current file, then exit.
    Unprocessed files remain in their current status (pending/processing)
    and will be retried on the next run.
    """
    kb = _resolve_kb(request, kb)
    stop_event = request.app.state.kb_stop_events.get(kb)
    if stop_event is None:
        return {"status": "idle", "message": f"「{kb}」当前没有运行中的索引任务。"}

    # Set in-memory event
    stop_event.set()

    # Persist to DB so it survives server restarts
    try:
        store = _kb_store(request, kb)
        last_run = store.get_last_run()
        if last_run and last_run["status"] == "running":
            store.set_stop_requested(last_run["id"])
            logger.info("Stop signal persisted to DB for run %d (KB: %s)", last_run["id"], kb)
    except Exception:
        logger.exception("Failed to persist stop signal to DB")

    return {"status": "stopping", "message": f"已请求停止「{kb}」的索引——当前文件处理完毕后将安全退出。"}


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
    processing_files: dict[str, str] = getattr(
        request.app.state, "processing_files", {}
    )
    current_file = processing_files.get(kb, "")

    return {
        "kb": kb,
        "kb_list": request.app.state.kb_names,
        "files_total": store.get_file_count(),
        "facts_total": store.get_fact_count(),
        "health": health,
        "current_processing_file": current_file,
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
# GET /files — file list with stats
# ============================================================================


@app.get("/files")
def files_endpoint(
    request: Request,
    kb: str = Query(default="默认"),
    status: str | None = Query(default=None, description="Filter: pending|processing|done|failed|skipped"),
    search: str | None = Query(default=None, description="Fuzzy match file path"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    zero_facts: bool = Query(default=False, description="Show only files with 0 facts"),
):
    """List files in a knowledge base with indexing status and fact counts."""
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)
    rows, total = store.list_files(status=status, search=search, limit=limit, offset=offset, zero_facts=zero_facts)

    # Filter out files that no longer exist on disk (cheap os.path.exists check)
    rows = store.stale_check(rows)

    return {
        "kb": kb,
        "total": total,
        "limit": limit,
        "offset": offset,
        "files": [
            {
                "id": r["id"],
                "path": r["path"],
                "status": r["status"],
                "error_msg": r["error_msg"],
                "file_size": r["file_size"],
                "mtime": r["mtime"],
                "indexed_at": r["indexed_at"],
                "chunk_count": r["chunk_count"],
                "fact_count": r["fact_count"],
            }
            for r in rows
        ],
    }


@app.get("/files/{file_id}/facts")
def file_facts_endpoint(
    file_id: int,
    request: Request,
    kb: str = Query(default="默认"),
):
    """Get all facts extracted from a specific file."""
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)
    file_row = store.get_file_by_id(file_id)
    if not file_row:
        raise HTTPException(404, f"文件 ID {file_id} 不存在")
    facts = store.get_facts_by_file(file_id)
    return {
        "file_id": file_id,
        "path": file_row["path"],
        "status": file_row["status"],
        "facts": [
            {
                "id": f["id"],
                "subject": f["subject"],
                "predicate": f["predicate"],
                "object": f["object"],
                "confidence": f["confidence"],
                "user_score": f["user_score"],
            }
            for f in facts
        ],
    }


# ============================================================================
# POST /files/{file_id}/reindex — single-file reindex
# ============================================================================


@app.post("/files/{file_id}/reindex")
def reindex_single_file(file_id: int, request: Request, kb: str = Query(default="默认")):
    """Re-index a single file — reset, re-parse, re-extract, update indices."""
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)
    vs = _kb_vs(request, kb)
    gs = _kb_gs(request, kb)
    cfg = request.app.state.config

    file_row = store.get_file_by_id(file_id)
    if not file_row:
        raise HTTPException(404, f"文件 ID {file_id} 不存在")

    file_path_str = file_row["path"]
    file_path = Path(file_path_str)
    if not file_path.exists():
        raise HTTPException(404, f"文件不存在: {file_path_str}")

    logger.info("Single-file reindex requested: %s (id=%d)", file_path.name, file_id)

    try:
        # Reset file state — explicit re-index resets retry counter
        store.update_file_status(file_id, "processing")
        store.reset_retry_count(file_id)
        store.soft_delete_facts_by_file(file_id)
        store.delete_chunks_by_file(file_id)  # Fix 2: prevent chunk leak

        # Check file size
        file_size = file_path.stat().st_size
        if file_size > 50 * 1024 * 1024:
            store.update_file_status(file_id, "skipped", "文件过大 (>50MB)")
            return {"file_id": file_id, "status": "skipped", "reason": "文件过大 (>50MB)"}

        # Check support
        if not parser.is_supported(file_path_str):
            suffix = file_path.suffix or "(无后缀)"
            store.update_file_status(file_id, "skipped", f"不支持的文件类型 ({suffix})")
            return {"file_id": file_id, "status": "skipped", "reason": f"不支持的文件类型 ({suffix})"}

        # Parse
        try:
            text = parser.parse_file(file_path)
        except ValueError as parse_err:
            error_msg = str(parse_err)
            store.update_file_status(file_id, "skipped", error_msg)
            return {"file_id": file_id, "status": "skipped", "reason": error_msg}
        if not text or not text.strip():
            store.update_file_status(file_id, "skipped", "文件为空或无法解析")
            return {"file_id": file_id, "status": "skipped", "reason": "文件为空或无法解析"}

        # Chunk
        is_chinese = splitter.detect_chinese(text)
        chunks = splitter.chunk_text(
            text,
            max_chars=cfg.extraction.max_chars_per_chunk,
            overlap_chars=cfg.extraction.overlap_chars,
        )

        file_facts = 0
        for idx, chunk_text in enumerate(chunks):
            try:
                chunk_id = store.insert_chunk(file_id, idx, chunk_text)
                result = extractor.extract_facts(
                    client=request.app.state.llm_client,
                    chunk_text=chunk_text,
                    chunk_id=chunk_id,
                    rounds=cfg.extraction.rounds,
                    confidence_threshold=cfg.extraction.confidence_threshold,
                    extra_body={"enable_thinking": False},
                )
                if result.error:
                    store.update_chunk_status(chunk_id, "failed")
                    store.enqueue_failed_chunk(
                        file_id, chunk_id, result.error,
                        "INVALID_JSON" if "parse" in result.error.lower() else "EXTRACTION_ERROR",
                    )
                    continue
                for fact in result.facts:
                    fact.source_file = file_path_str
                    fact.chunk_id = chunk_id
                    store.insert_fact(
                        file_id=file_id, chunk_id=chunk_id,
                        subject=fact.subject, predicate=fact.predicate,
                        object=fact.object, title=fact.title,
                        description=fact.description,
                        evidence_span=fact.evidence_span,
                        confidence=fact.confidence, tags=fact.tags,
                    )
                    file_facts += 1
                store.update_chunk_status(chunk_id, "done")
            except Exception as chunk_err:
                logger.error("Chunk %d of %s failed: %s", idx, file_path.name, chunk_err)
                try:
                    store.enqueue_failed_chunk(
                        file_id, chunk_id if 'chunk_id' in dir() else 0,
                        str(chunk_err), "UNKNOWN",
                    )
                except Exception:
                    pass

        store.mark_file_indexed(file_id)

        # Compute embeddings for new facts then rebuild indices
        _compute_embeddings_for_kb(store)
        vs.rebuild_from_sqlite(store)
        faiss_dir = _kb_faiss_dir(str(Path(cfg.database.path).expanduser().parent), kb)
        vs.save(faiss_dir)
        _rebuild_graph_for_kb(store, gs)

        # Run entity QA on the updated graph
        entity_suspects = 0
        if gs.node_count > 0:
            from filekb.personalization import generate_suspect_proposals
            try:
                suspects = generate_suspect_proposals(store, gs)
                entity_suspects = len(suspects)
            except Exception as e:
                logger.exception("Entity QA failed during single-file reindex: %s", e)

        logger.info("Single-file reindex done: %s → %d facts", file_path.name, file_facts)
        return {
            "file_id": file_id,
            "path": file_path_str,
            "status": "done",
            "facts_added": file_facts,
            "chunks": len(chunks),
        }

    except Exception as e:
        logger.error("Single-file reindex failed: %s — %s", file_path.name, e)
        store.update_file_status(file_id, "failed", str(e)[:500])
        raise HTTPException(500, f"重索引失败: {e}")


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
def entity_proposals_endpoint(
    request: Request,
    status: str = Query(default="proposed"),
    proposal_type: str = Query(default="merge", description="merge | suspect"),
    kb: str = Query(default="默认"),
):
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)
    if status != "proposed":
        rows = store.get_pending_proposals(proposal_type=proposal_type, status=status)
    else:
        rows = store.get_pending_proposals(proposal_type=proposal_type)
    return {
        "proposals": [
            {
                "id": r["id"],
                "entity_a": r["entity_a"],
                "entity_b": r["entity_b"],
                "proposed_name": r["proposed_name"],
                "confidence": r["confidence"],
                "status": r["status"],
                "proposal_type": r["proposal_type"],
                "llm_response": r["llm_response"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


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
# Suspect entity review endpoints
# ============================================================================


class SuspectScanRequest(BaseModel):
    kb: str = Field(default="默认")


class EntityRenameRequest(BaseModel):
    entity_name: str
    new_name: str
    proposal_id: int | None = None  # mark proposal as done too
    kb: str = Field(default="默认")


class EntityDeleteRequest(BaseModel):
    entity_name: str
    proposal_id: int | None = None
    kb: str = Field(default="默认")


@app.post("/entities/suspects/scan")
def suspect_scan_endpoint(req: SuspectScanRequest, request: Request):
    """Run entity QA detection and queue suspects for review."""
    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    gs = _kb_gs(request, kb)

    from filekb.personalization import generate_suspect_proposals
    suspects = generate_suspect_proposals(store, gs)

    return {
        "status": "ok",
        "entities_scanned": gs.node_count,
        "suspects_found": len(suspects),
        "suspects": [
            {
                "entity": s["entity"],
                "flags": s["flags"],
                "gibberish_score": s["gibberish_score"],
                "reason": s["reason"],
            }
            for s in suspects
        ],
    }


@app.post("/entities/rename")
def entity_rename_endpoint(req: EntityRenameRequest, request: Request):
    """Rename an entity across all facts, update graph, and resolve proposal."""
    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    gs = _kb_gs(request, kb)

    if not req.new_name or not req.new_name.strip():
        raise HTTPException(400, "新名称不能为空")

    updated = store.rename_entity(req.entity_name, req.new_name.strip())

    # Update graph: merge old entity into new
    if req.entity_name in gs.graph and req.new_name != req.entity_name:
        gs.merge_entities(req.entity_name, req.new_name.strip())

    # Mark proposal as resolved if given
    if req.proposal_id is not None:
        store.update_proposal_status(req.proposal_id, "approved")

    # Insert protection record for new name — so it won't be re-flagged
    if req.new_name != req.entity_name:
        store.insert_proposal(
            entity_a=req.new_name.strip(),
            entity_b="",
            confidence=1.0,
            status="approved",
            proposal_type="suspect",
            llm_response=json.dumps({
                "reason": f"用户从「{req.entity_name}」重命名确认",
                "flags": [],
                "gibberish_score": 0,
            }, ensure_ascii=False),
        )

    return {"status": "ok", "facts_updated": updated}


@app.post("/entities/delete")
def entity_delete_endpoint(req: EntityDeleteRequest, request: Request):
    """Soft-delete all facts for an entity and remove from graph."""
    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    gs = _kb_gs(request, kb)

    deleted = store.delete_entity_facts(req.entity_name)

    # Remove from graph
    if req.entity_name in gs.graph:
        gs.graph.remove_node(req.entity_name)

    # Mark proposal as resolved if given
    if req.proposal_id is not None:
        store.update_proposal_status(req.proposal_id, "deleted")

    return {"status": "ok", "facts_deleted": deleted}


@app.post("/entities/suspects/{proposal_id}/ignore")
def suspect_ignore_endpoint(proposal_id: int, request: Request, kb: str = Query(default="默认")):
    """Confirm entity as valid — mark proposal as rejected (meaning: reviewed, no action needed)."""
    kb = _resolve_kb(request, kb)
    _kb_store(request, kb).update_proposal_status(proposal_id, "rejected")
    return {"status": "ok"}


@app.post("/entities/suspects/{proposal_id}/revert")
def suspect_revert_endpoint(proposal_id: int, request: Request, kb: str = Query(default="默认")):
    """Revert a previously-resolved suspect proposal back to 'proposed' status for re-review."""
    kb = _resolve_kb(request, kb)
    _kb_store(request, kb).update_proposal_status(proposal_id, "proposed")
    return {"status": "ok"}


# ============================================================================
# Entity management — list & batch operations
# ============================================================================


class EntityBatchRequest(BaseModel):
    kb: str = Field(default="默认")
    operations: list[dict]  # [{"action": "rename", "entity": "...", "new_name": "..."}, {"action": "delete", "entity": "..."}]


@app.get("/entities/list")
def entity_list_endpoint(
    request: Request,
    kb: str = Query(default="默认"),
    search: str | None = Query(default=None, description="Filter by entity name substring"),
    sort_by: str = Query(default="count", description="count | name | degree"),
    order: str = Query(default="desc", description="asc | desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    suspect_only: bool = Query(default=False),
    merge_proposal_only: bool = Query(default=False),
):
    """List all entities with occurrence counts and metadata."""
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)

    entities, total = store.list_entities_with_stats(
        search=search,
        sort_by=sort_by,
        order=order,
        page=page,
        page_size=page_size,
        suspect_only=suspect_only,
        merge_proposal_only=merge_proposal_only,
    )

    return {
        "entities": entities,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/entities/batch")
def entity_batch_endpoint(req: EntityBatchRequest, request: Request):
    """Execute batch entity rename/delete operations and rebuild graph.

    All operations run in a single transaction. After completion, the
    knowledge graph is rebuilt from the updated facts table.
    """
    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    gs = _kb_gs(request, kb)

    if not req.operations:
        raise HTTPException(400, "operations list cannot be empty")

    logger.info("Batch entity ops for KB '%s': %d operations", kb, len(req.operations))

    # Execute batch operations
    results = store.batch_entity_operations(req.operations)

    # Rebuild graph from updated facts
    facts = store.conn.execute(
        "SELECT id, subject, predicate, object, confidence FROM facts WHERE status = 'active'"
    ).fetchall()
    gs.rebuild_from_facts([dict(f) for f in facts])

    # Re-run suspect detection to clear resolved proposals
    entity_suspects = 0
    try:
        from filekb.personalization import generate_suspect_proposals
        suspects = generate_suspect_proposals(store, gs)
        entity_suspects = len(suspects)
    except Exception as e:
        logger.warning("Suspect re-scan after batch ops failed: %s", e)

    success_count = sum(1 for r in results if r.get("success"))
    logger.info(
        "Batch entity ops done: %d/%d succeeded, graph rebuilt (%d nodes, %d edges), %d suspects found",
        success_count, len(results),
        gs.node_count, gs.edge_count, entity_suspects,
    )

    return {
        "status": "ok",
        "results": results,
        "graph_rebuilt": True,
        "graph_nodes": gs.node_count,
        "graph_edges": gs.edge_count,
        "suspects_found": entity_suspects,
    }


# ============================================================================
# GET /dlq — Dead Letter Queue status + entries
# ============================================================================


@app.get("/dlq")
def dlq_status_endpoint(request: Request, kb: str = Query(default="默认"), limit: int = Query(default=50, ge=1, le=200)):
    """Return DLQ statistics and detailed entries for a knowledge base."""
    store = _kb_store(request, kb)
    error_counts = store.count_failed_by_class()
    by_class = {r["error_class"]: r["cnt"] for r in error_counts}
    retryable_classes = {"RATE_LIMIT", "TIMEOUT", "MODEL_UNAVAILABLE", "CONTEXT_LENGTH_EXCEEDED", "RATE_LIMIT/TIMEOUT"}
    retryable = sum(cnt for cls, cnt in by_class.items() if cls in retryable_classes)

    entries = store.get_all_dlq(status="pending", limit=limit)
    entry_list = []
    for e in entries:
        entry_list.append({
            "id": e["id"],
            "chunk_id": e["chunk_id"],
            "file_id": e["file_id"],
            "file_path": e["file_path"] or "(已删除的文件)",
            "error_class": e["error_class"],
            "error_msg": e["error_msg"][:200],
            "retry_count": e["retry_count"],
            "created_at": e["created_at"],
        })

    return {
        "kb": kb,
        "total_pending": sum(by_class.values()),
        "by_error_class": by_class,
        "retryable": retryable,
        "entries": entry_list,
    }


# ============================================================================
# POST /dlq/retry — retry DLQ entries
# ============================================================================


class DLQRetryRequest(BaseModel):
    entry_id: int | None = None  # single entry; if None, retry all retryable
    kb: str = "默认"


@app.post("/dlq/retry")
def dlq_retry_endpoint(req: DLQRetryRequest, request: Request):
    """Retry one or all retryable DLQ entries."""
    store = _kb_store(request, req.kb)
    if req.entry_id is not None:
        ok = store.retry_single_dlq(req.entry_id)
        if not ok:
            raise HTTPException(404, f"DLQ entry {req.entry_id} not found or already done")
        # Trigger immediate retry via resilience
        from filekb.resilience import process_dlq as run_dlq
        processed = run_dlq(store, batch_size=1)
        return {"status": "ok", "retried": 1 if processed > 0 else 0}
    else:
        from filekb.resilience import process_dlq as run_dlq
        processed = run_dlq(store, batch_size=50)
        return {"status": "ok", "retried": processed}


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
        "kb_names": sorted(set(
            list(cfg.kb_meta.keys()) + [d.group or "默认" for d in cfg.directories] + ["默认"]
        )),
        "kb_meta": {name: meta.model_dump() for name, meta in cfg.kb_meta.items()},
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
            from filekb.config import DirectoryConfig, KBInfo
            group = req.data.get("group", "默认")
            cfg.directories.append(DirectoryConfig(
                path=req.data["path"], group=group,
                recursive=req.data.get("recursive", True),
                exclude_patterns=req.data.get("exclude_patterns", [".git", "__pycache__", ".DS_Store"]),
            ))
            # Auto-create KB metadata for new group names
            if group not in cfg.kb_meta:
                cfg.kb_meta[group] = KBInfo(description="")
        elif action == "remove":
            target = req.data.get("path", "")
            cfg.directories = [d for d in cfg.directories if d.path != target]
    elif req.section == "knowledge_bases":
        action = req.data.get("action", "")
        if action == "create":
            from filekb.config import KBInfo
            name = req.data.get("name", "").strip()
            if not name:
                raise HTTPException(400, "KB name is required")
            if name in cfg.kb_meta:
                raise HTTPException(409, f"KB '{name}' already exists")
            cfg.kb_meta[name] = KBInfo(description=req.data.get("description", ""))

            # Initialize per-KB state immediately (not just on restart)
            _init_kb_state(request.app, name)

        elif action == "rename":
            old_name = req.data.get("old_name", "")
            new_name = req.data.get("new_name", "").strip()
            if not old_name or not new_name:
                raise HTTPException(400, "old_name and new_name are required")
            if old_name not in cfg.kb_meta and old_name != "默认":
                pass  # 允许裸 group（仅存在于目录中但不在 kb_meta）
            if new_name in cfg.kb_meta:
                raise HTTPException(409, f"KB '{new_name}' already exists")
            # Update kb_meta
            if old_name in cfg.kb_meta:
                meta = cfg.kb_meta.pop(old_name)
                cfg.kb_meta[new_name] = meta
            # Update all directories with the old group
            for d in cfg.directories:
                if d.group == old_name:
                    d.group = new_name

            # Transfer per-KB state
            for state_dict in [
                request.app.state.kb_stores,
                request.app.state.kb_vector_stores,
                request.app.state.kb_graph_stores,
            ]:
                if old_name in state_dict:
                    state_dict[new_name] = state_dict.pop(old_name)
            # Ensure new name has state (fallback if old didn't exist)
            _init_kb_state(request.app, new_name)
            # Update kb_names list
            if old_name in request.app.state.kb_names:
                request.app.state.kb_names = sorted(
                    [new_name if n == old_name else n for n in request.app.state.kb_names]
                )

        elif action == "delete":
            name = req.data.get("name", "")
            if not name:
                raise HTTPException(400, "KB name is required")
            if name == "默认":
                raise HTTPException(400, "不能删除默认知识库")
            # Remove all directories in this group
            cfg.directories = [d for d in cfg.directories if d.group != name]
            # Remove kb_meta entry
            cfg.kb_meta.pop(name, None)

            # Clean up per-KB state
            _remove_kb_state(request.app, name)

        elif action == "clear":
            name = req.data.get("name", "")
            if not name:
                raise HTTPException(400, "KB name is required")
            if name not in request.app.state.kb_stores:
                raise HTTPException(404, f"知识库 '{name}' 不存在")

            store = request.app.state.kb_stores[name]
            vs = request.app.state.kb_vector_stores[name]
            gs = request.app.state.kb_graph_stores[name]

            counts = store.clear_all_data()
            vs.rebuild_from_sqlite(store)
            _rebuild_graph_for_kb(store, gs)

            logger.info("KB '%s' data cleared — %d rows removed across %d tables",
                        name, sum(counts.values()), len(counts))
            return {
                "status": "ok",
                "section": req.section,
                "action": "clear",
                "kb": name,
                "rows_deleted": sum(counts.values()),
                "tables_cleared": len(counts),
            }

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg.model_dump(), f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        logger.warning("Failed to persist config: %s", e)

    return {"status": "ok", "section": req.section}


# ============================================================================
# Chat history persistence
# ============================================================================


class ChatHistoryEntry(BaseModel):
    id: int | None = None
    role: str
    content: str
    sources: list[dict[str, Any]] = []
    related_facts: list[dict[str, Any]] = []
    feedback_given: str | None = None
    created_at: str | None = None


class ChatHistoryRequest(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str
    sources: list[dict[str, Any]] = []
    related_facts: list[dict[str, Any]] = []
    kb: str = Field(default="默认")


class ChatSessionInfo(BaseModel):
    session_id: str
    created_at: str
    turns: int


@app.get("/chat/sessions")
def chat_sessions(request: Request, kb: str = Query(default="默认")):
    """List all chat sessions for a KB, newest first."""
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)
    sessions = store.get_chat_sessions(kb)
    return {"sessions": sessions}


@app.get("/chat/history")
def chat_history(
    request: Request,
    session_id: str = Query(..., description="Session ID"),
    kb: str = Query(default="默认"),
):
    """Get all messages for a chat session."""
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)
    messages = store.get_chat_history(kb, session_id)

    # Parse JSON fields for the frontend
    import json
    result: list[dict[str, Any]] = []
    for m in messages:
        entry: dict[str, Any] = {
            "id": m["id"],
            "role": m["role"],
            "content": m["content"],
            "feedback_given": m.get("feedback_given"),
            "created_at": m.get("created_at"),
        }
        try:
            entry["sources"] = json.loads(m.get("sources", "[]"))
        except (json.JSONDecodeError, TypeError):
            entry["sources"] = []
        try:
            entry["related_facts"] = json.loads(m.get("related_facts", "[]"))
        except (json.JSONDecodeError, TypeError):
            entry["related_facts"] = []
        result.append(entry)

    return {"messages": result}


@app.post("/chat/save")
def chat_save(req: ChatHistoryRequest, request: Request):
    """Persist a single chat turn."""
    import json
    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)

    # Generate or reuse session_id from query param
    session_id = request.query_params.get("session_id", "")
    if not session_id:
        import uuid
        session_id = uuid.uuid4().hex[:12]

    row_id = store.save_chat_message(
        kb_name=kb,
        session_id=session_id,
        role=req.role,
        content=req.content,
        sources=json.dumps(req.sources, ensure_ascii=False),
        related_facts=json.dumps(req.related_facts, ensure_ascii=False),
    )
    return {"status": "ok", "id": row_id, "session_id": session_id}


@app.delete("/chat/sessions/{session_id}")
def chat_delete_session(
    session_id: str,
    request: Request,
    kb: str = Query(default="默认"),
):
    """Delete a chat session and all its messages."""
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)
    deleted = store.delete_chat_session(kb, session_id)
    return {"status": "ok", "deleted": deleted}
