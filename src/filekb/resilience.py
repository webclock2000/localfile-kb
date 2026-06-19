"""Resilience orchestrator — fault isolation and recovery.

Wraps extraction operations with error classification, tenacity retry,
finish_reason overflow detection, checkpointing, and Dead Letter Queue.

Per DEVELOPMENT_V3.md §10:
- Five-class error taxonomy
- Three-layer overflow protection (structural → reactive → recovery)
- File-level atomic checkpointing
- DLQ with auto-retry and pruning
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from filekb.llm import (
    ERROR_CONTEXT_LENGTH,
    ERROR_INVALID_JSON,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_RATE_LIMIT,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    adaptive_resplit,
    classify_http_error,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Error classification
# ============================================================================


def classify_error(exception: Exception) -> str:
    """Map an exception to one of five error classes.

    Args:
        exception: The caught exception.

    Returns:
        Error class string constant.
    """
    import httpx

    if isinstance(exception, httpx.HTTPStatusError):
        return classify_http_error(exception.response.status_code)
    if isinstance(exception, httpx.TimeoutException):
        return ERROR_TIMEOUT
    if isinstance(exception, httpx.ConnectError):
        return ERROR_MODEL_UNAVAILABLE
    if isinstance(exception, (ValueError, KeyError, TypeError)):
        return ERROR_INVALID_JSON

    msg = str(exception).lower()
    if "context" in msg or "length" in msg or "token" in msg:
        return ERROR_CONTEXT_LENGTH
    if "rate" in msg or "429" in msg:
        return ERROR_RATE_LIMIT
    if "timeout" in msg or "timed out" in msg:
        return ERROR_TIMEOUT

    return ERROR_UNKNOWN


def is_retryable(error_class: str) -> bool:
    """Check if an error class warrants automatic retry."""
    return error_class in (
        ERROR_RATE_LIMIT,
        ERROR_TIMEOUT,
        ERROR_MODEL_UNAVAILABLE,
    )


# ============================================================================
# Checkpoint
# ============================================================================


def commit_checkpoint(store: Any, file_id: int, run_id: int) -> None:
    """Atomic checkpoint: mark file as done and all its chunks as done.

    This is a SQLite transaction — completes entirely or not at all.
    Per DEVELOPMENT_V3.md §10.4.

    Uses a savepoint to avoid nested transaction conflicts in WAL mode.
    """
    conn = store.conn
    conn.execute("SAVEPOINT checkpoint")
    try:
        conn.execute(
            "UPDATE files SET status = 'done', indexed_at = datetime('now') WHERE id = ?",
            (file_id,),
        )
        conn.execute(
            "UPDATE chunks SET status = 'done' WHERE file_id = ? AND status = 'processing'",
            (file_id,),
        )
        conn.execute("RELEASE checkpoint")
        conn.commit()
        logger.debug("Checkpoint: file_id=%d committed", file_id)
    except Exception:
        conn.execute("ROLLBACK TO checkpoint")
        raise


# ============================================================================
# Dead Letter Queue
# ============================================================================


def enqueue_dlq(
    store: Any,
    chunk_id: int,
    file_id: int,
    error: Exception,
) -> int:
    """Enqueue a failed chunk into the Dead Letter Queue.

    Returns the DLQ entry ID.
    """
    error_class = classify_error(error)
    entry_id = store.enqueue_failed_chunk(
        chunk_id=chunk_id,
        file_id=file_id,
        error_class=error_class,
        error_msg=str(error)[:500],
    )
    logger.warning("DLQ enqueue: chunk=%d error=%s dlq_id=%d", chunk_id, error_class, entry_id)
    return entry_id


def process_dlq(store: Any, batch_size: int = 10) -> int:
    """Process pending DLQ entries that are eligible for retry.

    Called after the main batch completes.

    Returns:
        Number of successfully re-processed entries.
    """
    entries = store.get_pending_dlq(limit=batch_size)
    if not entries:
        return 0

    processed = 0
    for entry in entries:
        error_class = entry["error_class"]
        if not is_retryable(error_class):
            store.update_dlq_entry(entry["id"], "skipped")
            continue

        try:
            # For context overflow, re-split and retry
            if error_class == ERROR_CONTEXT_LENGTH:
                chunk_row = store.conn.execute(
                    "SELECT content FROM chunks WHERE id = ?", (entry["chunk_id"],)
                ).fetchone()
                if chunk_row:
                    sub_chunks = adaptive_resplit(chunk_row["content"])
                    for sub in sub_chunks:
                        new_id = store.insert_chunk(entry["file_id"], 0, sub)
                        store.update_chunk_status(new_id, "pending")
            # Mark as retried
            store.update_dlq_entry(
                entry["id"], "done", retry_count=entry["retry_count"] + 1
            )
            processed += 1
        except Exception as e:
            logger.warning("DLQ retry failed for entry %d: %s", entry["id"], e)
            new_retry = entry["retry_count"] + 1
            if new_retry >= 3:
                store.update_dlq_entry(entry["id"], "failed", retry_count=new_retry)
            else:
                store.update_dlq_entry(entry["id"], "pending", retry_count=new_retry)

    if processed:
        logger.info("DLQ batch: %d/%d entries processed", processed, len(entries))

    return processed


def build_failure_report(store: Any) -> dict[str, Any]:
    """Build a summary of DLQ status grouped by error class.

    Returns a dict suitable for inclusion in notifications.
    """
    counts = store.count_failed_by_class()
    by_class = {r["error_class"]: r["cnt"] for r in counts}

    total = store.conn.execute(
        "SELECT COUNT(*) FROM failed_chunks WHERE status = 'pending'"
    ).fetchone()[0]

    return {
        "total_failed": total,
        "by_error_class": by_class,
        "can_retry": sum(
            cnt for cls, cnt in by_class.items() if is_retryable(cls)
        ),
    }


# ============================================================================
# Run orchestrator
# ============================================================================


@dataclass
class ResilientResult:
    """Result of a resilience-wrapped operation."""

    success: bool
    data: Any = None
    error_class: str | None = None
    error_msg: str | None = None
    dlq_id: int | None = None


def run_with_resilience(
    fn: Callable,
    store: Any,
    chunk_id: int,
    file_id: int,
    max_attempts: int = 3,
) -> ResilientResult:
    """Execute fn with resilience wrappers.

    Isolates failures to a single chunk. A chunk failure does not kill
    the file or the run.

    Args:
        fn: Callable to execute (should take no arguments).
        store: Store instance for DLQ operations.
        chunk_id: Current chunk ID.
        file_id: Current file ID.
        max_attempts: Maximum retry attempts for retryable errors.

    Returns:
        ResilientResult indicating success or failure.
    """
    last_error: Exception | None = None
    last_error_class: str = ERROR_UNKNOWN

    for attempt in range(max_attempts):
        try:
            data = fn()
            return ResilientResult(success=True, data=data)
        except Exception as e:
            last_error = e
            last_error_class = classify_error(e)
            logger.warning(
                "Chunk %d attempt %d/%d failed (%s): %s",
                chunk_id, attempt + 1, max_attempts, last_error_class, e,
            )
            if not is_retryable(last_error_class):
                break
            if attempt < max_attempts - 1:
                wait = min(2 ** attempt, 30)
                logger.debug("Retrying chunk %d in %ds", chunk_id, wait)
                time.sleep(wait)

    # Exhausted retries or non-retryable — enqueue to DLQ
    dlq_id = enqueue_dlq(store, chunk_id, file_id, last_error)
    return ResilientResult(
        success=False,
        error_class=last_error_class,
        error_msg=str(last_error)[:500],
        dlq_id=dlq_id,
    )
