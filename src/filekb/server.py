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
import filekb.watcher as watcher
import filekb.parser as parser
import filekb.splitter as splitter
import filekb.extractor as extractor
import filekb.dedup as dedup

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


def _init_kb_state(app: FastAPI, kb_name: str) -> None:
    """Create per-KB Store / VectorStore / GraphStore for *kb_name* if missing."""
    if kb_name in app.state.kb_stores:
        return  # already initialized

    cfg = app.state.config
    db_base = str(Path(cfg.database.path).expanduser().parent)
    db_path = kb_name == "默认" and cfg.database.path or _kb_db_path(db_base, kb_name)
    faiss_dir = _kb_faiss_dir(db_base, kb_name)

    store = Store(db_path)
    vs = VectorStore(dimension=1024)
    if not vs.load(faiss_dir):
        vs.rebuild_from_sqlite(store)
    gs = GraphStore()
    _rebuild_graph_for_kb(store, gs)

    app.state.kb_stores[kb_name] = store
    app.state.kb_vector_stores[kb_name] = vs
    app.state.kb_graph_stores[kb_name] = gs
    if kb_name not in app.state.kb_names:
        app.state.kb_names = sorted(app.state.kb_names + [kb_name])
    logger.info("KB '%s' state initialized", kb_name)


def _remove_kb_state(app: FastAPI, kb_name: str) -> None:
    """Close and remove per-KB state for *kb_name*."""
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
    logger.info("KB '%s' state removed", kb_name)


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
# Index Pipeline
# ============================================================================


def _run_index_pipeline(
    kb_name: str,
    store: Store,
    llm_client: LLMClient,
    vs: VectorStore,
    gs: GraphStore,
    cfg: Config,
    dlq_only: bool = False,
) -> dict[str, Any]:
    """Run the full indexing pipeline for a knowledge base.

    Orchestrates: scan → detect_changes → parse → chunk → extract →
    dedup → store → rebuild_indices.

    One file failure does not kill the entire run — errors are logged
    and the file is marked as failed, then processing continues.
    """
    run_id = store.start_run()
    total_files = 0
    total_facts = 0
    files_changed = 0
    files_skipped = 0
    skip_reasons: dict[str, int] = {}  # reason → count
    all_files_to_process: list[str] = []
    all_deleted: list[str] = []

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

        # ── Also retry previously failed/skipped/pending files ──
        # "pending" files have their SHA256 in the DB (from upsert_file),
        # so detect_changes won't flag them as "new".  We must explicitly
        # re-process them or they'll be stuck forever.
        retry_files: list[str] = []
        for status in ("failed", "skipped", "pending"):
            for row in store.get_files_by_status(status):
                p = row["path"]
                if Path(p).exists() and any(
                    p.startswith(str(Path(d.path).expanduser()))
                    for d in kb_dirs
                ):
                    retry_files.append(p)
        if retry_files:
            logger.info("Re-processing %d previously failed/skipped files", len(retry_files))
            # Avoid duplicates
            existing = set(all_files_to_process)
            for p in retry_files:
                if p not in existing:
                    all_files_to_process.append(p)
                    existing.add(p)

        if not all_files_to_process and not all_deleted:
            logger.info("KB '%s': no changes detected", kb_name)
            store.finish_run(run_id, "completed")
            return {"run_id": run_id, "files_processed": 0, "facts_added": 0,
                    "files_skipped": 0, "skip_reasons": {},
                    "message": "没有检测到文件变更。"}

        # ── 3. Process added/modified files ──
        for file_path_str in all_files_to_process:
            try:
                file_path = Path(file_path_str)
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

                # ── Parse file ──
                text = parser.parse_file(file_path)
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

            except Exception as file_err:
                logger.error("Failed to process file %s: %s", file_path_str, file_err)
                try:
                    if 'file_id' in dir():
                        store.update_file_status(file_id, "failed", str(file_err))
                except Exception:
                    pass

        # ── 4. Process deleted files (soft-delete) ──
        for deleted_path in all_deleted:
            try:
                file_record = store.get_file_by_path(deleted_path)
                if file_record:
                    store.soft_delete_file(file_record["id"])
                    files_changed += 1
                    logger.info("Soft-deleted: %s", deleted_path)
            except Exception as del_err:
                logger.error("Failed to soft-delete %s: %s", deleted_path, del_err)

        # ── 5. Rebuild search indices ──
        if files_changed > 0:
            logger.info("Rebuilding FAISS index and knowledge graph...")
            vs.rebuild_from_sqlite(store)
            _rebuild_graph_for_kb(store, gs)
            logger.info("Indices rebuilt: %d vectors, %d nodes, %d edges",
                        vs.size, gs.node_count, gs.edge_count)

        # ── 6. Finalize run ──
        store.update_run(
            run_id,
            files_total=total_files,
            files_changed=files_changed,
            facts_added=total_facts,
        )
        store.finish_run(run_id, "completed")

        logger.info(
            "Index run %d complete: %d files (%d skipped), %d facts",
            run_id, total_files, files_skipped, total_facts,
        )

        # ── 7. Entity resolution (if enabled) ──
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
        }

    except Exception as e:
        logger.exception("Index pipeline crashed for KB '%s'", kb_name)
        try:
            store.update_run(run_id, files_total=total_files, files_changed=files_changed,
                           facts_added=total_facts)
            store.finish_run(run_id, "crashed")
        except Exception:
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
    """
    kb = _resolve_kb(request, req.kb)
    store = _kb_store(request, kb)
    vs = _kb_vs(request, kb)
    gs = _kb_gs(request, kb)
    cfg = request.app.state.config

    result = _run_index_pipeline(
        kb_name=kb,
        store=store,
        llm_client=request.app.state.llm_client,
        vs=vs,
        gs=gs,
        cfg=cfg,
        dlq_only=req.dlq,
    )

    return IndexResponse(
        run_id=result["run_id"],
        status=f"completed: {result.get('files_processed', 0)} files, "
               f"{result.get('facts_added', 0)} facts"
        if "files_processed" in result
        else result.get("message", result.get("status", "completed")),
    )


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
):
    """List files in a knowledge base with indexing status and fact counts."""
    kb = _resolve_kb(request, kb)
    store = _kb_store(request, kb)
    rows, total = store.list_files(status=status, search=search, limit=limit, offset=offset)
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
        # Reset file state
        store.update_file_status(file_id, "processing")
        store.soft_delete_facts_by_file(file_id)

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
        text = parser.parse_file(file_path)
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

        # Rebuild indices
        vs.rebuild_from_sqlite(store)
        _rebuild_graph_for_kb(store, gs)

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
