"""Tests for error classifier and resilience orchestration."""


import httpx

from filekb.llm import (
    ERROR_INVALID_JSON,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_RATE_LIMIT,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
)
from filekb.resilience import (
    ResilientResult,
    build_failure_report,
    classify_error,
    commit_checkpoint,
    enqueue_dlq,
    is_retryable,
)


class TestClassifyError:
    def test_rate_limit(self):
        resp = type("r", (), {"status_code": 429})()
        err = httpx.HTTPStatusError("429", request=None, response=resp)
        assert classify_error(err) == ERROR_RATE_LIMIT

    def test_server_error(self):
        resp = type("r", (), {"status_code": 503})()
        err = httpx.HTTPStatusError("503", request=None, response=resp)
        assert classify_error(err) == ERROR_MODEL_UNAVAILABLE

    def test_timeout(self):
        err = httpx.TimeoutException("timed out")
        assert classify_error(err) == ERROR_TIMEOUT

    def test_connect_error(self):
        err = httpx.ConnectError("connection refused")
        assert classify_error(err) == ERROR_MODEL_UNAVAILABLE

    def test_invalid_json(self):
        err = ValueError("Invalid JSON")
        assert classify_error(err) == ERROR_INVALID_JSON

    def test_unknown(self):
        assert classify_error(RuntimeError("something weird")) == ERROR_UNKNOWN


class TestIsRetryable:
    def test_rate_limit_retryable(self):
        assert is_retryable(ERROR_RATE_LIMIT) is True

    def test_timeout_retryable(self):
        assert is_retryable(ERROR_TIMEOUT) is True

    def test_invalid_json_not_retryable(self):
        assert is_retryable(ERROR_INVALID_JSON) is False

    def test_unknown_not_retryable(self):
        assert is_retryable(ERROR_UNKNOWN) is False


class TestDLQ:
    def test_enqueue(self, store):
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test/doc.md", "abc123", 1)
        store.insert_chunk(fid, 0, "test")
        dlq_id = enqueue_dlq(store, 1, fid, httpx.TimeoutException("timeout"))
        assert dlq_id > 0
        entries = store.get_pending_dlq()
        assert len(entries) >= 1

    def test_build_report(self, store):
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test/doc.md", "abc123", 1)
        store.insert_chunk(fid, 0, "test")
        store.enqueue_failed_chunk(1, fid, "RATE_LIMIT", "429")
        store.enqueue_failed_chunk(1, fid, "TIMEOUT", "timeout")
        report = build_failure_report(store)
        assert report["total_failed"] >= 1
        assert isinstance(report["by_error_class"], dict)


class TestCheckpoint:
    def test_commit_checkpoint(self, store):
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test/doc.md", "abc123", 1)
        cid = store.insert_chunk(fid, 0, "chunk content")

        commit_checkpoint(store, fid, 1)

        frow = store.get_file_by_id(fid)
        assert frow["status"] == "done"
        crow = store.conn.execute(
            "SELECT status FROM chunks WHERE id = ?", (cid,)
        ).fetchone()
        assert crow is not None


class TestResilientResult:
    def test_success(self):
        result = ResilientResult(success=True, data={"facts": []})
        assert result.success is True
        assert result.error_class is None

    def test_failure(self):
        result = ResilientResult(
            success=False,
            error_class="RATE_LIMIT",
            error_msg="429 Too Many Requests",
        )
        assert result.success is False
        assert result.error_class == "RATE_LIMIT"
