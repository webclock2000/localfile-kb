"""CLI — Click command group for FileKB.

Provides command-line equivalents to all Web UI capabilities:
    filekb index --watch      Scan directories and index changed files
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


@click.group()
@click.version_option(package_name="filekb")
def main():
    """FileKB — File-driven personal knowledge base.

    Monitor local directories, auto-extract structured knowledge,
    build a queryable knowledge graph, and ask natural language
    questions with full source provenance.
    """
    pass


# ============================================================================
# index
# ============================================================================


@main.command()
@click.option("--watch", is_flag=True, help="Scan directories and index changed files")
@click.option("--file", "single_file", type=click.Path(exists=True), help="Index a single file (hot path)")
@click.option("--dlq", is_flag=True, help="Retry failed chunks in the Dead Letter Queue")
@click.option("--rebuild", is_flag=True, help="Full rebuild of all indices")
def index(watch: bool, single_file: str | None, dlq: bool, rebuild: bool):
    """Index local files into the knowledge base."""
    from filekb.config import load_config

    cfg = load_config()

    if dlq:
        _run_dlq(cfg)
    elif single_file:
        click.echo(f"Hot-path indexing not yet implemented: {single_file}")
    elif watch or rebuild:
        click.echo("Index run starting...")
        _run_full_index(cfg)
    else:
        click.echo("Use --watch, --file, --dlq, or --rebuild")


def _run_full_index(cfg) -> None:
    """Execute a full index pipeline."""
    from filekb.parser import is_supported, parse_file
    from filekb.splitter import chunk_text
    from filekb.store import Store
    from filekb.watcher import detect_changes, load_previous_hashes, scan_directory

    store = Store(cfg.database.path)
    run_id = store.start_run()
    total_processed = 0

    try:
        for dir_cfg in cfg.directories:
            click.echo(f"Scanning: {dir_cfg.path}")
            current = scan_directory(
                Path(dir_cfg.path),
                recursive=dir_cfg.recursive,
                exclude_patterns=set(dir_cfg.exclude_patterns),
            )
            previous = load_previous_hashes(store)
            changes = detect_changes(current, previous)

            click.echo(
                f"  Added: {len(changes['added'])}, "
                f"Modified: {len(changes['modified'])}, "
                f"Deleted: {len(changes['deleted'])}, "
                f"Unchanged: {len(changes['unchanged'])}"
            )

            # Process added/modified files
            for fpath in changes["added"] + changes["modified"]:
                if not is_supported(fpath):
                    store.upsert_file(fpath, current.get(fpath, ""), 0, status="skipped")
                    continue

                try:
                    text = parse_file(fpath)
                except Exception as e:
                    click.echo(f"  SKIP {fpath}: {e}", err=True)
                    store.upsert_file(fpath, current.get(fpath, ""), 0, status="skipped")
                    continue

                chunks = chunk_text(
                    text,
                    max_chars=cfg.extraction.max_chars_per_chunk,
                    overlap_chars=cfg.extraction.overlap_chars,
                )
                click.echo(f"  {fpath}: {len(chunks)} chunks")
                total_processed += 1

            # Mark deleted files
            for fpath in changes["deleted"]:
                row = store.get_file_by_path(fpath)
                if row:
                    store.soft_delete_file(row["id"])

        store.update_run(run_id, files_total=total_processed, files_changed=total_processed)
        store.finish_run(run_id, "completed")
        click.echo(f"Index complete. {total_processed} files processed.")

    except Exception as e:
        store.finish_run(run_id, "crashed")
        click.echo(f"Index run crashed: {e}", err=True)
        raise
    finally:
        store.close()


def _run_dlq(cfg) -> None:
    """Process the Dead Letter Queue."""
    from filekb.resilience import build_failure_report, process_dlq
    from filekb.store import Store

    store = Store(cfg.database.path)
    try:
        processed = process_dlq(store, batch_size=10)
        report = build_failure_report(store)
        click.echo(f"DLQ processed: {processed} entries retried")
        if report["total_failed"] > 0:
            click.echo(f"Remaining DLQ entries: {report}")
        else:
            click.echo("DLQ is empty")
    finally:
        store.close()


# ============================================================================
# ask
# ============================================================================


@main.command()
@click.argument("question")
@click.option("--top-k", default=20, help="Number of facts to retrieve")
def ask(question: str, top_k: int):
    """Ask a natural language question."""
    from filekb.config import load_config
    from filekb.embed import configure as embed_configure
    from filekb.graph_store import GraphStore
    from filekb.llm import LLMClient
    from filekb.query import query
    from filekb.store import Store
    from filekb.vector_store import VectorStore

    cfg = load_config()

    store = Store(cfg.database.path)
    embed_configure(
        model_name=cfg.embedding.model,
        device=cfg.embedding.device,
    )
    llm = LLMClient(
        base_url=cfg.llm.base_url,
        model=cfg.llm.model,
        timeout=cfg.llm.timeout,
    )
    vs = VectorStore()
    faiss_dir = str(Path(cfg.database.path).parent / "faiss")
    vs.load(faiss_dir)
    gs = GraphStore()

    try:
        result = query(
            question=question,
            store=store,
            vector_store=vs,
            graph_store=gs,
            llm_client=llm,
            vector_top_k=top_k,
        )

        click.echo(result.answer)
        click.echo(f"\n---\nSources ({len(result.sources)}):")
        for src in result.sources:
            click.echo(f"  {src['file']}")
    finally:
        store.close()
        llm.close()


# ============================================================================
# graph
# ============================================================================


@main.command()
@click.argument("entity")
@click.option("--hop", default=1, help="Number of BFS hops")
def graph(entity: str, hop: int):
    """Explore the knowledge graph around an entity."""
    from filekb.config import load_config
    from filekb.graph_store import GraphStore
    from filekb.store import Store

    cfg = load_config()
    store = Store(cfg.database.path)
    gs = GraphStore()

    try:
        result = gs.expand(entity, hops=hop)
        click.echo(f"Entity: {entity}")
        click.echo(f"Nodes ({len(result['nodes'])}):")
        for n in result["nodes"][:20]:
            click.echo(f"  {n['name']} (degree={n['degree']})")
        click.echo(f"Edges ({len(result['edges'])}):")
        for e in result["edges"][:20]:
            click.echo(f"  {e['source']} --[{e['predicate']}]--> {e['target']}")
    finally:
        store.close()


# ============================================================================
# status
# ============================================================================


@main.command()
@click.option("--skipped", is_flag=True, help="List skipped files")
def status(skipped: bool):
    """Show indexing status overview."""
    from filekb.config import load_config
    from filekb.store import Store

    cfg = load_config()
    store = Store(cfg.database.path)

    try:
        total_files = store.get_file_count()
        total_facts = store.get_fact_count()
        last_run = store.get_last_run()

        click.echo(f"Files indexed: {total_files}")
        click.echo(f"Facts extracted: {total_facts}")

        if last_run:
            click.echo(
                f"Last run: {last_run['status']} "
                f"(processed {last_run['files_changed']} files, "
                f"added {last_run['facts_added']} facts) "
                f"at {last_run.get('finished_at') or last_run['started_at']}"
            )

        if skipped:
            skipped_files = store.get_files_by_status("skipped")
            if skipped_files:
                click.echo(f"\nSkipped files ({len(skipped_files)}):")
                for f in skipped_files:
                    click.echo(f"  {f['path']} — {f.get('error_msg', 'no reason')}")
            else:
                click.echo("No skipped files.")
    finally:
        store.close()


# ============================================================================
# ui
# ============================================================================


@main.command()
@click.option("--port", default=8501, help="Streamlit port")
def ui(port: int):
    """Launch the Streamlit Web UI."""
    import subprocess

    ui_dir = Path(__file__).parent / "ui"
    click.echo(f"Starting FileKB UI on http://localhost:{port}")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(ui_dir / "chat.py"),
         "--server.port", str(port)],
    )


# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    main()
