"""CLI — Click command group for FileKB.

Provides command-line equivalents to all Web UI capabilities:
    filekb index              Scan directories and index changed files
    filekb ask "question"     Single Q&A
    filekb graph ENTITY       Graph exploration
    filekb status             Indexing status overview
    filekb ui                 Launch Streamlit Web UI

See DEVELOPMENT_V3.md §6.5 for full CLI specification.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

# Default API port — CLI talks directly to the FastAPI backend
API_BASE = "http://localhost:9494"


def _get_default_kb(config_path: str | None = None) -> str:
    """Resolve the default KB name from the server, fallback to '默认'."""
    import json

    import requests
    try:
        resp = requests.get(f"{API_BASE}/settings", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("kb_names", ["默认"])[0]
    except Exception:
        pass
    return "默认"


@click.group()
@click.version_option(package_name="filekb")
@click.option("--kb", default=None, help="Knowledge base name (default: first available KB)")
@click.pass_context
def main(ctx: click.Context, kb: str | None):
    """FileKB — File-driven personal knowledge base.

    Monitor local directories, auto-extract structured knowledge,
    build a queryable knowledge graph, and ask natural language
    questions with full source provenance.
    """
    ctx.ensure_object(dict)
    ctx.obj["kb"] = kb or _get_default_kb()


# ============================================================================
# index
# ============================================================================


@main.command()
@click.option("--watch", is_flag=True, help="[PLACEHOLDER] Continuous watch mode — not yet implemented")
@click.option("--file", "single_file", type=click.Path(exists=True), help="Index a single file (hot path)")
@click.option("--dlq", is_flag=True, help="Retry failed chunks in the Dead Letter Queue")
@click.option("--rebuild", is_flag=True, help="Full rebuild of all indices")
@click.pass_context
def index(ctx: click.Context, watch: bool, single_file: str | None, dlq: bool, rebuild: bool):
    """Index local files into the knowledge base."""
    kb = ctx.obj["kb"]

    if watch:
        click.echo("Watch mode is not yet implemented. "
                   "Use manual 'filekb index' to run a single scan.")
        return

    if dlq:
        _run_dlq_via_api(kb)
    elif single_file:
        _run_single_file(kb, single_file)
    elif rebuild:
        _run_full_index_via_api(kb)
    else:
        _run_full_index_via_api(kb)


def _run_full_index_via_api(kb: str) -> None:
    """Trigger a full index scan via the FastAPI backend."""
    import requests

    click.echo(f"Triggering full index for KB: {kb}")
    try:
        resp = requests.post(
            f"{API_BASE}/index",
            json={"kb": kb},
            timeout=600,
        )
        if resp.status_code == 200:
            data = resp.json()
            click.echo(
                f"Index complete. "
                f"Files: {data.get('files_processed', '?')} processed, "
                f"{data.get('files_skipped', 0)} skipped, "
                f"{data.get('files_deleted', 0)} deleted. "
                f"Facts: {data.get('facts_added', '?')} added. "
                f"Entities: {data.get('entity_proposals', 0)} proposals, "
                f"{data.get('entity_merged', 0)} merged, "
                f"{data.get('entity_suspects', 0)} suspects."
            )
        else:
            click.echo(f"Index failed: {resp.status_code} — {resp.text}", err=True)
    except requests.exceptions.Timeout:
        click.echo("Index job submitted (server still working). Check progress via 'filekb status'.")
    except requests.exceptions.ConnectionError:
        click.echo("Cannot connect to FileKB server. Start it first: filekb ui", err=True)


def _run_single_file(kb: str, file_path: str) -> None:
    """Index a single file by uploading it through the API.

    The server's /files/{id}/reindex endpoint works for already-tracked files.
    For brand-new files, we trigger a full index targeted at that file's directory.
    """
    import os

    import requests

    abs_path = os.path.abspath(file_path)
    click.echo(f"Indexing single file: {abs_path}")

    # 1. Find which directory config covers this file
    try:
        resp = requests.get(f"{API_BASE}/settings", timeout=5)
        if resp.status_code != 200:
            click.echo("Cannot read config from server.", err=True)
            return
        cfg = resp.json()
    except requests.exceptions.ConnectionError:
        click.echo("Cannot connect to FileKB server. Start it first: filekb ui", err=True)
        return

    # 2. Check if file is already tracked
    try:
        resp = requests.get(
            f"{API_BASE}/files",
            params={"kb": kb, "search": abs_path, "limit": 5},
            timeout=10,
        )
        if resp.status_code == 200:
            files = resp.json().get("files", [])
            for f in files:
                if f["path"] == abs_path:
                    # Already tracked — trigger reindex
                    click.echo(f"File already tracked (id={f['id']}, status={f['status']}). Re-indexing...")
                    resp2 = requests.post(
                        f"{API_BASE}/files/{f['id']}/reindex",
                        params={"kb": kb},
                        timeout=300,
                    )
                    if resp2.status_code == 200:
                        data = resp2.json()
                        click.echo(
                            f"Re-indexed: {data.get('facts_added', 0)} facts added, "
                            f"{data.get('facts_removed', 0)} removed."
                        )
                    else:
                        click.echo(f"Re-index failed: {resp2.status_code}", err=True)
                    return
    except Exception:
        pass

    # 3. File not yet tracked — add its directory then index
    parent_dir = os.path.dirname(abs_path)
    click.echo(f"Adding directory to KB '{kb}': {parent_dir}")
    try:
        requests.post(
            f"{API_BASE}/settings",
            json={
                "section": "directories",
                "data": {
                    "action": "add",
                    "path": parent_dir,
                    "group": kb,
                    "recursive": False,
                    "exclude_patterns": [],
                },
            },
            timeout=10,
        )
    except Exception as e:
        click.echo(f"Failed to add directory: {e}", err=True)
        return

    # Now trigger a full index (the new directory will be picked up)
    _run_full_index_via_api(kb)


def _run_dlq_via_api(kb: str) -> None:
    """Trigger DLQ retry via the FastAPI backend."""
    import requests

    click.echo(f"Triggering DLQ retry for KB: {kb}")
    try:
        resp = requests.post(
            f"{API_BASE}/dlq/retry",
            json={"kb": kb},
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            click.echo(f"DLQ retried: {data.get('retried', 0)} entries")
        else:
            click.echo(f"DLQ retry failed: {resp.status_code}", err=True)
    except requests.exceptions.ConnectionError:
        click.echo("Cannot connect to FileKB server. Start it first: filekb ui", err=True)


# ============================================================================
# ask
# ============================================================================


@main.command()
@click.argument("question")
@click.option("--top-k", default=20, help="Number of facts to retrieve")
@click.pass_context
def ask(ctx: click.Context, question: str, top_k: int):
    """Ask a natural language question."""
    import requests

    kb = ctx.obj["kb"]
    click.echo(f"🔍 [{kb}] {question}\n")
    try:
        resp = requests.post(
            f"{API_BASE}/ask",
            json={"question": question, "kb": kb, "top_k": top_k},
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            click.echo(data.get("answer", "No answer."))
            sources = data.get("sources", [])
            if sources:
                click.echo(f"\n--- 参考来源 ({len(sources)}) ---")
                for src in sources:
                    click.echo(f"  📄 {src.get('file', '?')}")
        else:
            click.echo(f"API error: {resp.status_code} — {resp.text}", err=True)
    except requests.exceptions.ConnectionError:
        click.echo("Cannot connect to FileKB server. Start it first: filekb ui", err=True)
    except requests.exceptions.Timeout:
        click.echo("Request timed out. The server may still be processing.", err=True)


# ============================================================================
# graph
# ============================================================================


@main.command()
@click.argument("entity")
@click.option("--hop", default=1, help="Number of BFS hops")
@click.pass_context
def graph(ctx: click.Context, entity: str, hop: int):
    """Explore the knowledge graph around an entity."""
    import requests

    kb = ctx.obj["kb"]
    click.echo(f"🔗 [{kb}] Exploring '{entity}' (hop={hop})\n")
    try:
        resp = requests.get(
            f"{API_BASE}/graph",
            params={"entity": entity, "hop": hop, "kb": kb},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            nodes = data.get("nodes", [])
            edges = data.get("edges", [])
            click.echo(f"Nodes ({len(nodes)}):")
            for n in nodes[:20]:
                click.echo(f"  🏷️  {n['name']} (degree={n.get('degree', 0)})")
            click.echo(f"\nEdges ({len(edges)}):")
            for e in edges[:20]:
                click.echo(f"  {e['source']} --[{e.get('predicate', '?')}]--> {e['target']}")
            if len(edges) > 20:
                click.echo(f"  ... and {len(edges) - 20} more edges")
        else:
            click.echo(f"API error: {resp.status_code}", err=True)
    except requests.exceptions.ConnectionError:
        click.echo("Cannot connect to FileKB server. Start it first: filekb ui", err=True)


# ============================================================================
# status
# ============================================================================


@main.command()
@click.option("--skipped", is_flag=True, help="List skipped files")
@click.pass_context
def status(ctx: click.Context, skipped: bool):
    """Show indexing status overview."""
    import requests

    kb = ctx.obj["kb"]
    try:
        resp = requests.get(f"{API_BASE}/status", params={"kb": kb}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            health = data.get("health", {})
            click.echo(f"📊 [{kb}] Status")
            click.echo(f"  Files total:   {data.get('files_total', 0)}")
            click.echo(f"  Facts total:   {data.get('facts_total', 0)}")
            click.echo(f"  Entities:      {health.get('graph', {}).get('nodes', 0)}")
            click.echo(f"  Vectors:       {health.get('faiss', {}).get('vectors', 0)}")
            click.echo(f"  LLM server:    {'✅ 在线' if health.get('llm_server') == 'ok' else '❌ 离线'}")
            last = data.get("last_run")
            if last:
                click.echo(
                    f"\n  Last index: {last.get('status', '?')} "
                    f"(processed {last.get('files_changed', 0)} files, "
                    f"added {last.get('facts_added', 0)} facts)"
                )
            if skipped:
                # Fetch skipped files via the files endpoint
                resp2 = requests.get(
                    f"{API_BASE}/files",
                    params={"kb": kb, "status": "skipped", "limit": 200},
                    timeout=10,
                )
                if resp2.status_code == 200:
                    skip_files = resp2.json().get("files", [])
                    if skip_files:
                        click.echo(f"\n  Skipped files ({len(skip_files)}):")
                        for f in skip_files:
                            msg = f.get("error_msg", "no reason")[:80]
                            click.echo(f"    ❌ {f['path']} — {msg}")
                    else:
                        click.echo("  No skipped files.")
        else:
            click.echo(f"API error: {resp.status_code}", err=True)
    except requests.exceptions.ConnectionError:
        click.echo("Cannot connect to FileKB server. Start it first: filekb ui", err=True)


# ============================================================================
# ui
# ============================================================================


@main.command()
@click.option("--port", default=8501, help="Streamlit port")
def ui(port: int):
    """Launch the Streamlit Web UI + FastAPI backend."""
    import subprocess
    import time

    ui_dir = Path(__file__).parent / "ui"
    click.echo(f"Starting FastAPI backend on http://localhost:9494 ...")
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "filekb.server:app",
         "--host", "127.0.0.1", "--port", "9494"],
    )
    time.sleep(1.5)
    click.echo(f"Starting FileKB UI on http://localhost:{port}")
    try:
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", str(ui_dir / "app.py"),
             "--server.port", str(port)],
        )
    finally:
        server_proc.terminate()
        server_proc.wait()


# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    main()
