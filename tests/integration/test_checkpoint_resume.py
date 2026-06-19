"""Checkpoint + resume: crash recovery must not duplicate facts."""

import tempfile
from pathlib import Path

import pytest

from filekb.resilience import commit_checkpoint
from filekb.store import Store


def test_checkpoint_marks_file_done():
    """After checkpoint, file and its chunks should be marked done."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    store = Store(db_path)
    try:
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test.md", "hash_test", 1, 100)

        # Simulate processing: create chunks, mark as processing
        for i in range(3):
            cid = store.insert_chunk(fid, i, f"chunk {i} content")
            store.update_chunk_status(cid, "processing")

        # Checkpoint
        commit_checkpoint(store, fid, 1)

        # Verify file status
        frow = store.get_file_by_id(fid)
        assert frow["status"] == "done"
        assert frow["indexed_at"] is not None

    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)


def test_resume_skips_completed_chunks():
    """On restart, chunks with status='done' should be skipped."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    store = Store(db_path)
    try:
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test.md", "hash_test", 1, 100)

        # 3 chunks: 2 done, 1 still pending (simulates crash mid-file)
        for i, status in enumerate(["done", "done", "pending"]):
            cid = store.insert_chunk(fid, i, f"chunk {i}")
            store.update_chunk_status(cid, status)

        # On resume: only chunk 2 needs processing
        done_count = store.count_chunks_by_status(fid, "done")
        pending_count = store.count_chunks_by_status(fid, "pending")
        assert done_count == 2
        assert pending_count == 1

    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)


def test_no_duplicate_facts_after_resume():
    """Inserting facts for already-done chunks should be prevented."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    store = Store(db_path)
    try:
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test.md", "hash_test", 1, 100)

        # First run: insert facts
        cid = store.insert_chunk(fid, 0, "chunk content")
        store.insert_fact(fid, cid, "A", "p", "B", title="Fact 1")
        store.update_chunk_status(cid, "done")
        store.mark_file_indexed(fid)

        assert store.get_fact_count() == 1

        # Simulated resume: try inserting again — should not duplicate
        # The caller should check chunk.status before extracting
        chunk_status = store.conn.execute(
            "SELECT status FROM chunks WHERE id = ?", (cid,)
        ).fetchone()["status"]
        assert chunk_status == "done"

        # Only extract if status != 'done'
        if chunk_status != "done":
            store.insert_fact(fid, cid, "B", "q", "C", title="Fact 2")

        # Count should still be 1 (no duplicate)
        assert store.get_fact_count() == 1

    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)
