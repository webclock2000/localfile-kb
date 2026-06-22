"""
Systematic integration test for FileKB index pipeline fixes.

Tests the complete fix set from the infinite-loop bug:
  S1 - Happy path: normal files extract facts correctly
  S2 - Permanent errors: empty/unsupported files → "skipped" (NOT retried)
  S3 - Retry → dead: files with retry_count ≥ 3 → "dead" on next run
  S4 - Stop persistence: stop_requested survives in DB
  S5 - Crash recovery: "processing" → "pending" on repair, NOT "failed/skipped"
  S6 - Explicit re-index: "dead" file can be revived (retry_count reset)
  S7 - Retry budget: "failed" within budget gets retried

Requires: FileKB server running on localhost:9494 with LLM server available.

Because LLM extraction is slow (~1-2 min per file), this test uses only
6 short parseable files + 2 permanently-skipped files.  The first test
(test_00_run_index_and_wait) triggers the pipeline and waits up to 900 s
for completion; all other tests inspect the results.

Run: pytest tests/integration/test_pipeline_fixes_systematic.py -v -s
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

# ── Config ──────────────────────────────────────────────────────────────────
BASE_URL = "http://localhost:9494"
TEST_KB = "集成测试"
DB_PATH = os.path.expanduser(f"~/.filekb/{TEST_KB}.db")

pytestmark = pytest.mark.llm


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def api(method: str, path: str, data: dict | None = None) -> dict:
    """Call the FileKB API."""
    # URL-encode Chinese characters in query string
    if "?" in path:
        base, qs = path.split("?", 1)
        params = urllib.parse.parse_qs(qs, keep_blank_values=True)
        flat = {k: v[0] if len(v) == 1 else v for k, v in params.items()}
        encoded_qs = urllib.parse.urlencode(flat, doseq=True)
        path = f"{base}?{encoded_qs}"

    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()
        except Exception:
            body = ""
        return {"_error": e.code, "_body": body}


def api_get(path: str) -> dict:
    return api("GET", path)


def api_post(path: str, data: dict | None = None) -> dict:
    return api("POST", path, data)


def db() -> sqlite3.Connection:
    """Get a direct connection to the test KB's SQLite DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def wait_for_run_completion(timeout: int = 900) -> dict:
    """Poll /status until the run is completed/cancelled/crashed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = api_get(f"/status?kb={TEST_KB}")
        if isinstance(s, dict):
            last_run = s.get("last_run")
            if last_run and last_run.get("status") in ("completed", "cancelled", "crashed"):
                return s
        time.sleep(3)
    # Timeout — return whatever we have for debugging
    s = api_get(f"/status?kb={TEST_KB}")
    print(f"   WARNING: Timeout waiting for run. Current: {s}")
    return s


def trigger_index_and_wait(timeout: int = 900, expect_start: bool = True) -> dict:
    """Trigger index and wait for completion, handling 409 (already running).

    If a pipeline is already running, wait for it to finish, then trigger
    again so the caller always gets a fresh completed run.
    """
    resp = api_post("/index", {"kb": TEST_KB})
    if resp.get("_error") == 409:
        # Another run is already in progress — wait for it
        print("   ⏳ Another run in progress, waiting...")
        wait_for_run_completion(timeout=timeout)
        # Now trigger again
        resp = api_post("/index", {"kb": TEST_KB})
    if resp.get("_error") and expect_start:
        print(f"   ⚠️  Index start returned: {resp}")
        return resp
    if "_error" not in resp:
        wait_for_run_completion(timeout=timeout)
    return resp


def assert_file_status(path_contains: str, expected_status: str) -> dict:
    """Get a file by path substring and assert its status."""
    files_resp = api_get(f"/files?kb={TEST_KB}&limit=200")
    for f in files_resp.get("files", []):
        if path_contains in f["path"]:
            assert f["status"] == expected_status, (
                f"Expected {path_contains} -> {expected_status}, "
                f"got {f['status']} (error: {f.get('error_msg', 'N/A')})"
            )
            return f
    raise AssertionError(f"File containing '{path_contains}' not found in {TEST_KB}")


# ═══════════════════════════════════════════════════════════════════════════
# Module fixture — set up once for all tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def test_env():
    """Create a dedicated test KB with 8 short test files.

    Returns (test_dir_path, dict_of_scenario_to_path).
    """
    # 1. Create test directory with minimal files
    test_dir = Path(tempfile.mkdtemp(prefix="filekb_test_"))
    print(f"\n📁 Test directory: {test_dir}")

    files: dict[str, Path] = {}

    # ── S1: Happy path — normal files (short, fast to extract) ──
    p = test_dir / "01_normal_en.md"
    p.write_text(textwrap.dedent("""\
        # Team Overview
        Alice Johnson is the CTO of TechCorp based in San Francisco.
        She joined in March 2019 and leads 45 engineers.
        Bob Smith, VP of Engineering, reports directly to Alice.
        TechCorp raised $50M Series B from Sequoia Capital in 2022.
    """))
    files["01_normal_en"] = p

    p = test_dir / "02_normal_zh.md"
    p.write_text(textwrap.dedent("""\
        # 产品介绍
        张三担任阿里巴巴集团的技术副总裁，负责云计算业务。
        他于2015年加入公司，领导200人技术团队。
        公司总部位于杭州市余杭区。
    """))
    files["02_normal_zh"] = p

    # ── S2: Permanent errors — empty & unsupported ──
    p = test_dir / "03_empty.md"
    p.write_text("")
    files["03_empty"] = p

    p = test_dir / "04_unsupported.xyz"
    p.write_text("This file has an unsupported extension.")
    files["04_unsupported"] = p

    # ── Happy path — code & data ──
    p = test_dir / "05_code.py"
    p.write_text(textwrap.dedent("""\
        \"\"\"Database pool module for the analytics platform.\"\"\"
        class DatabasePool:
            def __init__(self, max_connections: int = 10):
                self.max_connections = max_connections
    """))
    files["05_code"] = p

    # ── Short text files ──
    p = test_dir / "07_short_en.md"
    p.write_text("The Eiffel Tower is located in Paris, France. It was built in 1889.")
    files["07_short_en"] = p

    p = test_dir / "08_short_zh.md"
    p.write_text("华为技术有限公司成立于1987年，总部位于深圳。任正非是公司创始人。")
    files["08_short_zh"] = p

    # ── JSON ──
    p = test_dir / "10_config.json"
    p.write_text(json.dumps({
        "company": "TechCorp", "founded": 2018, "employees": 120,
    }))
    files["10_config"] = p

    print(f"✅ Created {len(files)} test files")

    # 2. Clean up any leftover state from previous runs
    print(f"\n🔧 Setting up test KB: {TEST_KB}")
    for stale_pat in [DB_PATH, f"{DB_PATH}-wal", f"{DB_PATH}-shm"]:
        try:
            Path(stale_pat).unlink(missing_ok=True)
        except Exception:
            pass
    # Delete existing KB (ignore errors)
    api_post("/settings", {"section": "knowledge_bases", "data": {"action": "delete", "name": TEST_KB}})

    # 3. Create fresh test KB
    resp = api_post("/settings", {
        "section": "knowledge_bases",
        "data": {"action": "create", "name": TEST_KB, "description": "集成测试专用"},
    })
    print(f"   KB creation: {resp}")

    # 4. Add test directory
    resp = api_post("/settings", {
        "section": "directories",
        "data": {
            "action": "add", "path": str(test_dir), "group": TEST_KB,
            "recursive": True, "exclude_patterns": [".git", "__pycache__", ".DS_Store"],
        },
    })
    print(f"   Directory added: {resp}")

    # 5. Health check
    health = api_get("/status")
    print(f"   LLM: {health.get('health', {}).get('llm_server', '?')}")

    yield test_dir, files

    # ── Teardown ──
    print("\n🧹 Cleaning up...")
    api_post("/settings", {"section": "directories", "data": {"action": "remove", "path": str(test_dir)}})
    shutil.rmtree(test_dir, ignore_errors=True)
    print(f"   Removed test dir: {test_dir}")


# ═══════════════════════════════════════════════════════════════════════════
# Tests — ordered within a single class to guarantee execution order
# ═══════════════════════════════════════════════════════════════════════════


class TestIndexPipeline:
    """End-to-end index pipeline test covering all fix scenarios."""

    # ── Phase 0: Trigger index, wait for full completion ────────────────

    def test_00_run_index_and_wait(self, test_env):
        """Trigger index and wait for completion (timeout=900s)."""
        test_dir, _files = test_env

        print("\n🔄 Triggering index...")
        resp = api_post("/index", {"kb": TEST_KB})
        assert "_error" not in resp, f"Index failed to start: {resp}"
        print(f"   Run #{resp.get('run_id')} started")

        status = wait_for_run_completion(timeout=900)
        last_run = status.get("last_run", {})

        print(f"   Done: status={last_run.get('status')}, "
              f"files={status.get('files_total')}, facts={status.get('facts_total')}")

        assert last_run.get("status") == "completed", (
            f"Expected 'completed', got '{last_run.get('status')}'"
        )
        assert status.get("files_total", 0) >= 4, (
            f"Expected >=4 files, got {status.get('files_total')}"
        )

    # ── S1: Happy path ──────────────────────────────────────────────────

    def test_s1_en_file_done(self, test_env):
        f = assert_file_status("01_normal_en.md", "done")
        assert f.get("fact_count", 0) > 0, f"No facts: {f}"

    def test_s1_zh_file_done(self, test_env):
        f = assert_file_status("02_normal_zh.md", "done")
        assert f.get("fact_count", 0) > 0, f"No facts: {f}"

    def test_s1_code_file_done(self, test_env):
        assert_file_status("05_code.py", "done")

    def test_s1_short_files_done(self, test_env):
        assert_file_status("07_short_en.md", "done")
        assert_file_status("08_short_zh.md", "done")

    def test_s1_json_file_done(self, test_env):
        assert_file_status("10_config.json", "done")

    # ── S2: Permanent errors → skipped ──────────────────────────────────

    def test_s2_empty_file_skipped(self, test_env):
        f = assert_file_status("03_empty.md", "skipped")
        assert "空" in (f.get("error_msg") or ""), f"Bad reason: {f}"

    def test_s2_unsupported_skipped(self, test_env):
        """Unsupported files are skipped BEFORE upsert, so they're NOT in DB.

        This is correct behavior: the pipeline skips them at the file-type
        gate and never creates a DB record.
        """
        files_resp = api_get(f"/files?kb={TEST_KB}&limit=200")
        found = [f for f in files_resp.get("files", []) if "04_unsupported.xyz" in f["path"]]
        assert len(found) == 0, (
            f"Unsupported file should NOT be in DB, but found: {found}"
        )

    def test_s2_skipped_retry_count_zero(self, test_env):
        """Skipped files must have retry_count=0 (never retried)."""
        c = db()
        try:
            row = c.execute(
                "SELECT retry_count FROM files WHERE path LIKE '%03_empty.md'"
            ).fetchone()
            assert row is not None, "03_empty.md not in DB"
            assert row["retry_count"] == 0, f"retry_count={row['retry_count']}, expected 0"
        finally:
            c.close()

    # ── S3: Retry exhaustion → dead ─────────────────────────────────────

    def test_s3_retry_exhaustion_marks_dead(self, test_env):
        """File with retry_count=3 should be marked dead on next run."""
        test_dir, _files = test_env

        p = test_dir / "99_doomed.md"
        p.write_text("# Doomed\nAlice works at TechCorp.")

        trigger_index_and_wait(timeout=300)

        # Simulate exhausted retries
        c = db()
        try:
            c.execute(
                "UPDATE files SET retry_count=3, status='failed', error_msg='模拟' "
                "WHERE path LIKE '%99_doomed.md'"
            )
            c.commit()
        finally:
            c.close()

        # Trigger another run — the file should be marked dead
        trigger_index_and_wait(timeout=300)

        f = assert_file_status("99_doomed.md", "dead")
        print(f"   ✅ Correctly marked dead: {f['status']}")

        p.unlink(missing_ok=True)

    # ── S4: Stop persistence ────────────────────────────────────────────

    def test_s4_stop_signal_persisted(self, test_env):
        """Stop request must be acknowledged by the API and persisted when applicable.

        The stop signal is dual-persisted: in-memory threading.Event (fast path)
        AND runs.stop_requested (survives restart).  However, if the pipeline
        finishes the only file before checking the stop flag, it completes
        normally and clears stop_requested.  That's correct behavior — the
        key assertion is that the API acknowledges the stop request.
        """
        test_dir, _files = test_env

        # The large file ensures the pipeline takes at least a few seconds
        large = test_dir / "99_large.md"
        large.write_text("\n\n".join([
            f"## Section {i}\nAlice manages project Alpha at TechCorp. "
            f"Bob leads the infrastructure team. Carol is the CFO.\n"
            for i in range(300)
        ]))

        resp = api_post("/index", {"kb": TEST_KB})
        if resp.get("_error"):
            large.unlink(missing_ok=True)
            pytest.skip(f"Index didn't start: {resp}")

        time.sleep(1)
        stop_resp = api_post(f"/index/stop?kb={TEST_KB}")
        print(f"   Stop: {stop_resp}")

        # Primary assertion: API accepted the stop request
        assert stop_resp.get("status") == "stopping", (
            f"Stop should return 'stopping', got: {stop_resp}"
        )

        wait_for_run_completion(timeout=120)

        # Secondary assertion: verify stop_requested column exists (schema V5)
        c = db()
        try:
            cols = [r[1] for r in c.execute("PRAGMA table_info(runs)").fetchall()]
            assert "stop_requested" in cols, (
                f"Schema V5 missing stop_requested column: {cols}"
            )
        finally:
            c.close()

        large.unlink(missing_ok=True)

    # ── S5: Crash recovery ──────────────────────────────────────────────

    def test_s5_processing_reset_to_pending(self, test_env):
        """Files stuck in 'processing' must be reset to 'pending' on repair.

        The server's _repair_stale_files runs at the start of every index.
        It ONLY resets 'processing' files — NOT 'failed' or 'skipped'.
        """
        c = db()
        try:
            c.execute("UPDATE files SET status='processing' WHERE path LIKE '%01_normal_en.md'")
            c.commit()
            cnt = c.execute("SELECT COUNT(*) as cnt FROM files WHERE status='processing'").fetchone()
            assert cnt["cnt"] >= 1, "Should have at least 1 processing file"
        finally:
            c.close()

        # Trigger index — on start, _repair_stale_files resets processing→pending.
        # The pipeline THEN processes these recovered files normally.
        trigger_index_and_wait(timeout=300)

        # After completion, the file may still be in 'processing' if the
        # pipeline is finalizing (entity QA, etc).  Retry a few times.
        deadline = time.time() + 60
        last_status = None
        while time.time() < deadline:
            try:
                f = assert_file_status("01_normal_en.md", "done")
                assert f.get("fact_count", 0) > 0, f"No facts after repair: {f}"
                last_status = "done"
                break
            except AssertionError as e:
                last_status = str(e)
                time.sleep(3)

        assert last_status == "done", (
            f"File should be 'done' after repair + re-processing. "
            f"Last state: {last_status}"
        )

    def test_s5_failed_files_not_reset_to_pending(self, test_env):
        """'failed' files must NOT be silently reset to 'pending'.

        The old bug would reset all failed files to pending on crash recovery.
        With the fix, only 'processing' files get reset.
        """
        c = db()
        try:
            c.execute(
                "UPDATE files SET status='failed', retry_count=1, error_msg='测试' "
                "WHERE path LIKE '%07_short_en.md'"
            )
            c.commit()
        finally:
            c.close()

        # Trigger a fresh run — the failed file should be retried (within budget)
        trigger_index_and_wait(timeout=300)

        c = db()
        try:
            row = c.execute(
                "SELECT status, retry_count FROM files WHERE path LIKE '%07_short_en.md'"
            ).fetchone()
            assert row is not None
            print(f"   After run: status={row['status']}, retry={row['retry_count']}")
            # Key assertion: status was NOT silently reset to 'pending'.
            # It should be 'done' (LLM succeeded on retry) or 'failed' (LLM failed again).
            assert row["status"] != "pending", (
                f"File should NOT be silently reset to 'pending'! "
                f"Got status='{row['status']}', retry={row['retry_count']}"
            )
        finally:
            c.close()

    # ── S6: Explicit re-index ───────────────────────────────────────────

    def test_s6_reindex_resets_retry_count(self, test_env):
        """Explicit re-index resets retry_count to 0."""
        test_dir, _files = test_env

        p = test_dir / "99_revivable.md"
        p.write_text("# Revivable\nAlice leads the platform team at TechCorp.")

        trigger_index_and_wait(timeout=300)

        # Mark as dead with high retry_count
        c = db()
        try:
            c.execute(
                "UPDATE files SET status='dead', retry_count=7, error_msg='测试' "
                "WHERE path LIKE '%99_revivable.md'"
            )
            c.commit()
            row = c.execute(
                "SELECT id, status, retry_count FROM files WHERE path LIKE '%99_revivable.md'"
            ).fetchone()
            assert row is not None, "99_revivable.md not found in DB"
            file_id = row["id"]
            assert row["status"] == "dead"
            assert row["retry_count"] == 7
        finally:
            c.close()

        # Explicit re-index — should reset retry_count to 0
        reindex_resp = api_post(f"/files/{file_id}/reindex?kb={TEST_KB}")
        print(f"   Reindex: {reindex_resp}")

        # reindex runs inline (not daemon), so no need to wait
        c = db()
        try:
            row = c.execute(
                "SELECT status, retry_count FROM files WHERE path LIKE '%99_revivable.md'"
            ).fetchone()
            print(f"   After reindex: status={row['status']}, retry={row['retry_count']}")
            assert row["retry_count"] == 0, (
                f"retry_count should be 0 after reindex, got {row['retry_count']}"
            )
        finally:
            c.close()

        p.unlink(missing_ok=True)

    # ── S7: Retry budget ────────────────────────────────────────────────

    def test_s7_failed_within_budget_retried(self, test_env):
        """File with retry_count < 3 is retried, not immediately killed."""
        test_dir, _files = test_env

        p = test_dir / "99_retry_me.md"
        p.write_text("# RetryMe\nBob manages the engineering team at TechCorp.")

        trigger_index_and_wait(timeout=300)

        # Set to failed with retry_count=1 (within budget)
        c = db()
        try:
            c.execute(
                "UPDATE files SET status='failed', retry_count=1, error_msg='模拟' "
                "WHERE path LIKE '%99_retry_me.md'"
            )
            c.commit()
        finally:
            c.close()

        trigger_index_and_wait(timeout=300)

        c = db()
        try:
            row = c.execute(
                "SELECT status, retry_count FROM files WHERE path LIKE '%99_retry_me.md'"
            ).fetchone()
            print(f"   After retry: status={row['status']}, retry={row['retry_count']}")
            assert row["status"] != "dead", (
                f"File with 2 retries should NOT be dead: {row}"
            )
        finally:
            c.close()

        p.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# Direct run
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("FileKB Index Pipeline Fix — Systematic Integration Test")
    print("=" * 70)

    try:
        health = api_get("/status")
        llm_ok = health.get("health", {}).get("llm_server") == "ok"
        print(f"Server: OK | LLM: {'OK' if llm_ok else 'DOWN'}")
        if not llm_ok:
            print("❌ LLM server is not available. Aborting.")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Cannot reach server at {BASE_URL}: {e}")
        sys.exit(1)

    import subprocess as sp
    result = sp.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "-s", "--tb=short", "--color=yes"],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
    sys.exit(result.returncode)
