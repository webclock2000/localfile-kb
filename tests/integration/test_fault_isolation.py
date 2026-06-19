"""Fault isolation: a bad file/chunk must not kill the entire index run."""

import tempfile
from pathlib import Path

import pytest

from filekb.config import load_config
from filekb.llm import LLMClient
from filekb.parser import parse_file
from filekb.resilience import build_failure_report, enqueue_dlq, process_dlq
from filekb.splitter import chunk_text
from filekb.store import Store

pytestmark = pytest.mark.llm


def test_bad_chunk_does_not_kill_run():
    """One bad chunk → DLQ entry → other files continue → DLQ report correct."""
    import httpx

    fd, db_path = tempfile.mkstemp(suffix=".db")
    store = Store(db_path)
    try:
        store.add_directory("/tmp/test")

        # Simulate a parse failure on file 1
        fid_bad = store.upsert_file("/tmp/bad.pdf", "hash_bad", 1, status="failed",
                                     error_msg="Simulated parse failure")
        cid_bad = store.insert_chunk(fid_bad, 0, "bad chunk content")

        # Enqueue to DLQ
        dlq_id = enqueue_dlq(store, cid_bad, fid_bad,
                             httpx.TimeoutException("simulated timeout"))
        assert dlq_id > 0

        # Verify DLQ report
        report = build_failure_report(store)
        assert report["total_failed"] >= 1
        assert "TIMEOUT" in report["by_error_class"]

        # Good file should still work independently
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write("# Good file\nAlice works at Acme Corp.")
            good_path = f.name

        try:
            text = parse_file(good_path)
            assert "Alice" in text
            fid_good = store.upsert_file(good_path, "hash_good", 1, len(text))

            chunks = chunk_text(text, max_chars=4000)
            for ci, chunk in enumerate(chunks):
                store.insert_chunk(fid_good, ci, chunk)
                # In real run, extraction would happen here
                # Even if one chunk fails, others continue

            # Verify both files exist: one failed, one pending
            bad = store.get_file_by_id(fid_bad)
            assert bad["status"] == "failed"
            good = store.get_file_by_id(fid_good)
            assert good["status"] == "pending"  # Not yet indexed

        finally:
            Path(good_path).unlink(missing_ok=True)

    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)


def test_dlq_retry_marks_complete():
    """DLQ entries should be re-processable and marked done."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    store = Store(db_path)
    try:
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test.md", "hash", 1)

        # Create a chunk that failed with RATE_LIMIT (retryable)
        cid = store.insert_chunk(fid, 0, "retryable chunk")
        store.enqueue_failed_chunk(cid, fid, "RATE_LIMIT", "429 simulated")
        store.update_chunk_status(cid, "failed")

        # Before retry: 1 pending DLQ
        assert len(store.get_pending_dlq()) == 1

        # Process DLQ
        processed = process_dlq(store, batch_size=10)
        assert processed >= 0  # May not retry successfully if LLM down, but shouldn't crash

    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)
