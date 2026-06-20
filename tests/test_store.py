"""Tests for SQLite storage layer (store.py)."""



class TestStoreSchema:
    """Schema creation and migration tests."""

    def test_store_creates_schema(self, store):
        """Store initializes with correct schema version and tables."""
        ver = store.conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 4

        tables = [
            r[0]
            for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        assert "directories" in tables
        assert "files" in tables
        assert "chunks" in tables
        assert "facts" in tables
        assert "runs" in tables
        assert "user_feedback" in tables
        assert "entity_proposals" in tables
        assert "failed_chunks" in tables

    def test_wal_mode_enabled(self, store):
        """WAL journal mode should be active."""
        mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_foreign_keys_enabled(self, store):
        """Foreign key constraints should be enforced."""
        fk = store.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


class TestDirectoryCRUD:
    def test_add_directory(self, store):
        dir_id = store.add_directory("/tmp/test", recursive=True)
        assert dir_id == 1

    def test_add_directory_with_patterns(self, store):
        dir_id = store.add_directory("/tmp/test", exclude_patterns=[".git", "node_modules"])
        row = store.conn.execute("SELECT * FROM directories WHERE id = ?", (dir_id,)).fetchone()
        assert row["path"] == "/tmp/test"
        assert ".git" in row["exclude_patterns"]

    def test_get_directories(self, store):
        store.add_directory("/tmp/a")
        store.add_directory("/tmp/b")
        dirs = store.get_directories()
        assert len(dirs) == 2

    def test_remove_directory(self, store):
        store.add_directory("/tmp/test")
        store.remove_directory(1)
        assert len(store.get_directories()) == 0


class TestFileCRUD:
    def test_upsert_file_creates(self, store):
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test/doc.md", "abc123", 1, 500)
        assert fid > 0

    def test_upsert_file_updates(self, store):
        store.add_directory("/tmp/test")
        fid1 = store.upsert_file("/tmp/test/doc.md", "abc123", 1, 500)
        fid2 = store.upsert_file("/tmp/test/doc.md", "def456", 1, 600)
        assert fid1 == fid2
        row = store.get_file_by_path("/tmp/test/doc.md")
        assert row["sha256"] == "def456"

    def test_update_file_status(self, store):
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test/doc.md", "abc123", 1)
        store.update_file_status(fid, "done")
        row = store.get_file_by_id(fid)
        assert row["status"] == "done"

    def test_mark_file_indexed(self, store):
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test/doc.md", "abc123", 1)
        store.mark_file_indexed(fid)
        row = store.get_file_by_id(fid)
        assert row["status"] == "done"
        assert row["indexed_at"] is not None

    def test_soft_delete_file(self, store):
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test/doc.md", "abc123", 1)
        store.insert_chunk(fid, 0, "test chunk content")
        store.insert_fact(fid, 1, "A", "p", "B")
        store.soft_delete_file(fid)
        row = store.get_file_by_id(fid)
        assert row["status"] == "deleted"


class TestFactCRUD:
    def test_insert_and_get_facts(self, store_with_data):
        facts = store_with_data.get_facts_by_file(1)
        assert len(facts) == 2

    def test_get_facts_by_entity(self, store_with_data):
        facts = store_with_data.get_facts_by_entity("Alice")
        assert len(facts) >= 1

    def test_fact_count(self, store_with_data):
        assert store_with_data.get_fact_count() == 2

    def test_update_user_score(self, store_with_data):
        store_with_data.update_user_score(1, 0.5)
        row = store_with_data.conn.execute(
            "SELECT user_score FROM facts WHERE id = 1"
        ).fetchone()
        assert row["user_score"] == 1.5

    def test_user_score_clamped(self, store_with_data):
        store_with_data.update_user_score(1, 5.0)
        row = store_with_data.conn.execute(
            "SELECT user_score FROM facts WHERE id = 1"
        ).fetchone()
        assert row["user_score"] == 2.0  # Clamped at 2.0

    def test_fts_search(self, store_with_data):
        results = store_with_data.search_facts_fts("Alice", limit=5)
        assert len(results) >= 1


class TestRunTracking:
    def test_start_and_finish_run(self, store):
        run_id = store.start_run()
        assert run_id > 0
        store.finish_run(run_id, "completed")
        row = store.get_last_run()
        assert row["status"] == "completed"

    def test_get_last_run_none(self, store):
        assert store.get_last_run() is None


class TestDLQ:
    def test_enqueue_and_get(self, store):
        store.add_directory("/tmp/test")
        fid = store.upsert_file("/tmp/test/doc.md", "abc123", 1)
        store.insert_chunk(fid, 0, "test content")
        store.enqueue_failed_chunk(1, fid, "RATE_LIMIT", "429 Too Many Requests")
        entries = store.get_pending_dlq(limit=10)
        assert len(entries) == 1
        assert entries[0]["error_class"] == "RATE_LIMIT"
