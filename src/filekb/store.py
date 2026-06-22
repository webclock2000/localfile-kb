"""SQLite storage layer with WAL mode, migrations, and CRUD operations.

Schema follows DEVELOPMENT_V3.md §5. Uses PRAGMA user_version for
schema migration tracking. All write operations use atomic transactions.

Tables:
    directories, files, chunks, facts, facts_fts (FTS5),
    runs, user_feedback, user_preferences, entity_proposals,
    failed_chunks (DLQ)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3


# ============================================================================
# Schema DDL
# ============================================================================

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS directories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    recursive BOOLEAN DEFAULT 1,
    exclude_patterns TEXT DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    directory_id INTEGER REFERENCES directories(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    error_msg TEXT,
    file_size INTEGER,
    mtime REAL,
    indexed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    chunk_id INTEGER REFERENCES chunks(id),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    title TEXT,
    description TEXT,
    evidence_span TEXT,
    confidence INTEGER DEFAULT 50,
    tags TEXT DEFAULT '[]',
    embedding BLOB,
    user_score REAL DEFAULT 1.0,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    subject, predicate, object, title, description,
    content='facts',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT DEFAULT 'running',
    files_total INTEGER DEFAULT 0,
    files_changed INTEGER DEFAULT 0,
    facts_added INTEGER DEFAULT 0,
    facts_removed INTEGER DEFAULT 0,
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT
);
"""

SCHEMA_V3 = """
ALTER TABLE entity_proposals ADD COLUMN proposal_type TEXT DEFAULT 'merge';
"""

SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_name TEXT NOT NULL DEFAULT '默认',
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sources TEXT DEFAULT '[]',
    related_facts TEXT DEFAULT '[]',
    feedback_given TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_history_session
    ON chat_history(kb_name, session_id, created_at);
"""

SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_name TEXT NOT NULL DEFAULT '默认',
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sources TEXT DEFAULT '[]',
    related_facts TEXT DEFAULT '[]',
    feedback_given TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_history_session
    ON chat_history(kb_name, session_id, created_at);
"""

SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_name TEXT NOT NULL DEFAULT '默认',
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sources TEXT DEFAULT '[]',
    related_facts TEXT DEFAULT '[]',
    feedback_given TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_history_session
    ON chat_history(kb_name, session_id, created_at);
"""

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS user_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER REFERENCES facts(id) ON DELETE CASCADE,
    feedback_type TEXT NOT NULL CHECK(feedback_type IN ('positive', 'negative')),
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pref_key TEXT NOT NULL,
    pref_value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entity_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    proposed_name TEXT,
    confidence REAL NOT NULL,
    llm_response TEXT,
    status TEXT DEFAULT 'proposed',
    reviewed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS failed_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id INTEGER REFERENCES chunks(id),
    file_id INTEGER REFERENCES files(id),
    error_class TEXT NOT NULL,
    error_msg TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0,
    next_retry_at TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# FTS5 triggers — keep FTS index in sync with facts table
FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, subject, predicate, object, title, description)
    VALUES (new.id, new.subject, new.predicate, new.object, new.title, new.description);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, predicate, object, title, description)
    VALUES ('delete', old.id, old.subject, old.predicate, old.object, old.title, old.description);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, predicate, object, title, description)
    VALUES ('delete', old.id, old.subject, old.predicate, old.object, old.title, old.description);
    INSERT INTO facts_fts(rowid, subject, predicate, object, title, description)
    VALUES (new.id, new.subject, new.predicate, new.object, new.title, new.description);
END;
"""


# ============================================================================
# Store
# ============================================================================


class Store:
    """SQLite storage manager with WAL mode and migration support."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path).expanduser())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------

    def _migrate(self) -> None:
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if current < 1:
            self.conn.executescript(SCHEMA_V1)
            self.conn.executescript(FTS_TRIGGERS)
            self.conn.execute("PRAGMA user_version = 1")
            logger.info("Migrated DB to schema v1")
            current = 1
        if current < 2:
            self.conn.executescript(SCHEMA_V2)
            self.conn.execute("PRAGMA user_version = 2")
            logger.info("Migrated DB to schema v2")
            current = 2
        if current < 3:
            self.conn.executescript(SCHEMA_V3)
            self.conn.execute("PRAGMA user_version = 3")
            logger.info("Migrated DB to schema v3")
            current = 3
        if current < 4:
            self.conn.executescript(SCHEMA_V4)
            self.conn.execute("PRAGMA user_version = 4")
            logger.info("Migrated DB to schema v4")

    # ------------------------------------------------------------------
    # Directory CRUD
    # ------------------------------------------------------------------

    def add_directory(
        self, path: str, recursive: bool = True, exclude_patterns: list[str] | None = None
    ) -> int:
        if exclude_patterns is None:
            exclude_patterns = [".git", "__pycache__", ".DS_Store"]
        self.conn.execute(
            "INSERT OR REPLACE INTO directories (path, recursive, exclude_patterns) VALUES (?, ?, ?)",
            (str(Path(path).expanduser()), int(recursive), json.dumps(exclude_patterns)),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_directories(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM directories").fetchall()

    def remove_directory(self, dir_id: int) -> None:
        self.conn.execute("DELETE FROM directories WHERE id = ?", (dir_id,))
        self.conn.commit()

    # ------------------------------------------------------------------
    # File CRUD
    # ------------------------------------------------------------------

    def upsert_file(
        self,
        path: str,
        sha256: str,
        directory_id: int,
        file_size: int = 0,
        mtime: float = 0.0,
        status: str = "pending",
        error_msg: str | None = None,
    ) -> int:
        self.conn.execute(
            """INSERT INTO files (directory_id, path, sha256, file_size, mtime, status, error_msg)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
               sha256=excluded.sha256, file_size=excluded.file_size,
               mtime=excluded.mtime, status=excluded.status,
               error_msg=excluded.error_msg""",
            (directory_id, path, sha256, file_size, mtime, status, error_msg),
        )
        self.conn.commit()
        return self.conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()["id"]

    def get_file_by_path(self, path: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()

    def get_file_by_id(self, file_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()

    def get_files_by_status(self, status: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM files WHERE status = ?", (status,)
        ).fetchall()

    def list_files(
        self,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
        zero_facts: bool = False,
    ) -> tuple[list[sqlite3.Row], int]:
        """List files with fact counts, optional status filter and path search.

        Returns (rows, total_count).
        """
        where: list[str] = ["f.status != 'deleted'"]
        params: list[Any] = []
        having: list[str] = []

        if status:
            where.append("f.status = ?")
            params.append(status)
        if search:
            where.append("f.path LIKE ?")
            params.append(f"%{search}%")
        if zero_facts:
            having.append("fact_count = 0")

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        having_clause = f"HAVING {' AND '.join(having)}" if having else ""

        count_sql = f"""
            SELECT COUNT(*) FROM (
                SELECT f.id, COUNT(DISTINCT fa.id) AS fact_count
                FROM files f
                LEFT JOIN facts fa ON fa.file_id = f.id AND fa.status = 'active'
                {where_clause}
                GROUP BY f.id
                {having_clause}
            )
        """
        total = self.conn.execute(count_sql, params).fetchone()[0]

        query_sql = f"""
            SELECT f.id, f.path, f.status, f.error_msg, f.file_size,
                   f.mtime, f.indexed_at, f.created_at,
                   COUNT(DISTINCT c.id) AS chunk_count,
                   COUNT(DISTINCT fa.id) AS fact_count
            FROM files f
            LEFT JOIN chunks c ON c.file_id = f.id
            LEFT JOIN facts fa ON fa.file_id = f.id AND fa.status = 'active'
            {where_clause}
            GROUP BY f.id
            {having_clause}
            ORDER BY f.indexed_at DESC, f.created_at DESC
            LIMIT ? OFFSET ?
        """
        rows = self.conn.execute(query_sql, params + [limit, offset]).fetchall()
        return rows, total

    def update_file_status(
        self, file_id: int, status: str, error_msg: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE files SET status = ?, error_msg = ? WHERE id = ?",
            (status, error_msg, file_id),
        )
        self.conn.commit()

    def mark_file_indexed(self, file_id: int) -> None:
        self.conn.execute(
            "UPDATE files SET status = 'done', indexed_at = datetime('now') WHERE id = ?",
            (file_id,),
        )
        self.conn.commit()

    def soft_delete_file(self, file_id: int) -> None:
        """Mark a file and its facts as deleted (soft delete).

        Detach facts from chunks so chunk cleanup won't violate FK constraints.
        """
        self.conn.execute("UPDATE files SET status = 'deleted' WHERE id = ?", (file_id,))
        self.conn.execute(
            "UPDATE facts SET status = 'deleted', chunk_id = NULL WHERE file_id = ?",
            (file_id,),
        )
        self.conn.commit()

    def stale_check(self, file_records: list[sqlite3.Row]) -> list[sqlite3.Row]:
        """Check file existence on disk; soft-delete missing ones.

        Returns only the records whose paths still exist.  This is a cheap
        ``os.path.exists()`` check — ~30 syscalls per page, not a SHA256 scan.
        """
        import os

        existing: list[sqlite3.Row] = []
        for r in file_records:
            if os.path.exists(r["path"]):
                existing.append(r)
            else:
                self.soft_delete_file(r["id"])
                logger.info("Stale file soft-deleted: %s", r["path"])
        return existing

    def get_file_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def clear_all_data(self) -> dict[str, int]:
        """Delete all rows from every data table, preserving schema.

        Tables are cleared in reverse-dependency order (children before
        parents) so foreign-key constraints are satisfied.  The FTS5
        index is rebuilt (empty) and sqlite_sequence counters are reset.

        Returns a dict of ``{table_name: rows_deleted}``.
        """
        # Order matters — delete leaf tables first
        tables = [
            "failed_chunks",      # → chunks, files
            "user_feedback",      # → facts
            "entity_proposals",   # no FK
            "facts",              # → files, chunks  (FTS triggers keep facts_fts in sync)
            "chunks",             # → files
            "files",              # → directories
            "directories",        # root
            "runs",               # no FK
            "user_preferences",   # no FK
        ]

        counts: dict[str, int] = {}
        with self.conn:
            for table in tables:
                cur = self.conn.execute(f"DELETE FROM {table}")
                counts[table] = cur.rowcount

            # Reset auto-increment counters so ids start from 1 again
            for table in tables:
                self.conn.execute(
                    "DELETE FROM sqlite_sequence WHERE name = ?", (table,)
                )

            # Rebuild FTS5 index from the (now-empty) facts table
            self.conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")

        # VACUUM must run outside any transaction
        self.conn.execute("VACUUM")

        logger.info("Cleared all data (%d tables): %s", len(tables), counts)
        return counts

    # ------------------------------------------------------------------
    # Chunk CRUD
    # ------------------------------------------------------------------

    def insert_chunk(self, file_id: int, chunk_index: int, content: str) -> int:
        self.conn.execute(
            "INSERT INTO chunks (file_id, chunk_index, content, status) VALUES (?, ?, ?, 'pending')",
            (file_id, chunk_index, content),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_chunks_by_file(self, file_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM chunks WHERE file_id = ? ORDER BY chunk_index", (file_id,)
        ).fetchall()

    def update_chunk_status(self, chunk_id: int, status: str) -> None:
        self.conn.execute("UPDATE chunks SET status = ? WHERE id = ?", (status, chunk_id))
        self.conn.commit()

    def count_chunks_by_status(self, file_id: int, status: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE file_id = ? AND status = ?", (file_id, status)
        ).fetchone()[0]

    # ------------------------------------------------------------------
    # Fact CRUD
    # ------------------------------------------------------------------

    def insert_fact(
        self,
        file_id: int,
        chunk_id: int,
        subject: str,
        predicate: str,
        object: str,
        title: str | None = None,
        description: str | None = None,
        evidence_span: str | None = None,
        confidence: int = 50,
        tags: list[str] | None = None,
        embedding: bytes | None = None,
    ) -> int:
        self.conn.execute(
            """INSERT INTO facts (file_id, chunk_id, subject, predicate, object,
               title, description, evidence_span, confidence, tags, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                file_id,
                chunk_id,
                subject,
                predicate,
                object,
                title,
                description,
                evidence_span,
                confidence,
                json.dumps(tags or []),
                embedding,
            ),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_facts_by_file(self, file_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM facts WHERE file_id = ? AND status = 'active'", (file_id,)
        ).fetchall()

    def get_facts_by_entity(self, entity: str, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT * FROM facts
               WHERE status = 'active' AND (subject = ? OR object = ?)
               LIMIT ?""",
            (entity, entity, limit),
        ).fetchall()

    def get_fact_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM facts WHERE status = 'active'"
        ).fetchone()[0]

    def get_facts_without_embeddings(
        self, limit: int = 200, offset: int = 0
    ) -> list[sqlite3.Row]:
        """Return active facts whose embedding column is NULL."""
        return self.conn.execute(
            """SELECT id, subject, predicate, object, title, description
               FROM facts WHERE status = 'active' AND embedding IS NULL
               ORDER BY id LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()

    def update_fact_embedding(self, fact_id: int, embedding: bytes) -> None:
        """Store embedding bytes for a fact."""
        self.conn.execute(
            "UPDATE facts SET embedding = ? WHERE id = ?", (embedding, fact_id)
        )

    def soft_delete_facts_by_file(self, file_id: int) -> None:
        """Mark facts as deleted and detach from chunks so chunks can be safely
        hard-deleted without foreign-key violations."""
        self.conn.execute(
            "UPDATE facts SET status = 'deleted', chunk_id = NULL WHERE file_id = ?",
            (file_id,),
        )
        self.conn.commit()

    def delete_chunks_by_file(self, file_id: int) -> int:
        """Hard-delete all chunks for a file, after detaching any failed_chunks
        references to avoid foreign-key violations. Returns count deleted."""
        # Detach failed_chunks FK before deleting chunks
        self.conn.execute(
            "UPDATE failed_chunks SET chunk_id = NULL WHERE file_id = ?", (file_id,)
        )
        cur = self.conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        self.conn.commit()
        return cur.rowcount

    def update_user_score(self, fact_id: int, delta: float) -> None:
        self.conn.execute(
            "UPDATE facts SET user_score = MAX(0.0, MIN(2.0, user_score + ?)) WHERE id = ?",
            (delta, fact_id),
        )
        self.conn.commit()

    def get_user_scores(self, fact_ids: list[int]) -> dict[int, float]:
        """Bulk-fetch user_scores for a list of fact IDs.

        Returns:
            {fact_id: user_score} dict. Missing IDs are absent (caller should default to 1.0).
        """
        if not fact_ids:
            return {}
        placeholders = ",".join("?" * len(fact_ids))
        rows = self.conn.execute(
            f"SELECT id, user_score FROM facts WHERE id IN ({placeholders})",
            tuple(fact_ids),
        ).fetchall()
        return {row["id"]: row["user_score"] for row in rows}

    def apply_score_decay(self, decay_rate: float) -> int:
        """Decay all user_scores toward 1.0. Returns count of updated rows."""
        cur = self.conn.execute(
            "UPDATE facts SET user_score = user_score - ? * (user_score - 1.0) "
            "WHERE status = 'active' AND user_score != 1.0",
            (decay_rate,),
        )
        self.conn.commit()
        return cur.rowcount

    def search_facts_fts(self, query: str, limit: int = 10) -> list[sqlite3.Row]:
        """Full-text search via FTS5.

        Sanitizes the query to remove FTS5 special characters that would
        cause syntax errors (e.g. '?', '*', quotes, parentheses).
        """
        import re
        # Strip FTS5 special characters: * " ( ) ? ？ and column:term syntax
        safe = re.sub(r'[*"\'()?？：:]', ' ', query)
        # Collapse whitespace
        safe = ' '.join(safe.split())
        if not safe:
            return []
        return self.conn.execute(
            "SELECT f.* FROM facts f "
            "JOIN facts_fts fts ON f.id = fts.rowid "
            "WHERE facts_fts MATCH ? AND f.status = 'active' "
            "ORDER BY rank LIMIT ?",
            (safe, limit),
        ).fetchall()

    def get_all_active_fact_ids(self) -> list[int]:
        """Return all active fact IDs (for FAISS index rebuild)."""
        rows = self.conn.execute(
            "SELECT id FROM facts WHERE status = 'active' AND embedding IS NOT NULL"
        ).fetchall()
        return [r["id"] for r in rows]

    def get_fact_embedding(self, fact_id: int) -> bytes | None:
        row = self.conn.execute(
            "SELECT embedding FROM facts WHERE id = ?", (fact_id,)
        ).fetchone()
        return row["embedding"] if row else None

    # ------------------------------------------------------------------
    # Run tracking
    # ------------------------------------------------------------------

    def start_run(self) -> int:
        self.conn.execute("INSERT INTO runs (status) VALUES ('running')")
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def update_run(self, run_id: int, **kwargs: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self.conn.execute(
            f"UPDATE runs SET {sets} WHERE id = ?", (*kwargs.values(), run_id)
        )
        self.conn.commit()

    def finish_run(self, run_id: int, status: str = "completed") -> None:
        self.conn.execute(
            "UPDATE runs SET status = ?, finished_at = datetime('now') WHERE id = ?",
            (status, run_id),
        )
        self.conn.commit()

    def get_last_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    # ------------------------------------------------------------------
    # User feedback
    # ------------------------------------------------------------------

    def record_feedback(self, fact_id: int, feedback_type: str, reason: str | None = None) -> int:
        self.conn.execute(
            "INSERT INTO user_feedback (fact_id, feedback_type, reason) VALUES (?, ?, ?)",
            (fact_id, feedback_type, reason),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # ------------------------------------------------------------------
    # Entity proposals
    # ------------------------------------------------------------------

    def insert_proposal(
        self,
        entity_a: str,
        entity_b: str,
        confidence: float,
        proposed_name: str | None = None,
        llm_response: str | None = None,
        status: str = "proposed",
        proposal_type: str = "merge",
    ) -> int:
        self.conn.execute(
            """INSERT INTO entity_proposals
               (entity_a, entity_b, proposed_name, confidence, llm_response, status, proposal_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entity_a, entity_b, proposed_name, confidence, llm_response, status, proposal_type),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_pending_proposals(
        self, proposal_type: str | None = None, status: str | None = None
    ) -> list[sqlite3.Row]:
        if status and proposal_type:
            return self.conn.execute(
                """SELECT * FROM entity_proposals
                   WHERE status = ? AND proposal_type = ?
                   ORDER BY confidence DESC""",
                (status, proposal_type),
            ).fetchall()
        if proposal_type:
            return self.conn.execute(
                """SELECT * FROM entity_proposals
                   WHERE status = 'proposed' AND proposal_type = ?
                   ORDER BY confidence DESC""",
                (proposal_type,),
            ).fetchall()
        if status:
            return self.conn.execute(
                """SELECT * FROM entity_proposals
                   WHERE status = ?
                   ORDER BY confidence DESC""",
                (status,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM entity_proposals WHERE status = 'proposed' ORDER BY confidence DESC"
        ).fetchall()

    def get_all_proposals(
        self, proposal_type: str | None = None
    ) -> list[sqlite3.Row]:
        """Get all proposals (any status), optionally filtered by type."""
        if proposal_type:
            return self.conn.execute(
                "SELECT * FROM entity_proposals WHERE proposal_type = ? ORDER BY created_at DESC",
                (proposal_type,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM entity_proposals ORDER BY created_at DESC"
        ).fetchall()

    def update_proposal_status(self, proposal_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE entity_proposals SET status = ?, reviewed_at = datetime('now') WHERE id = ?",
            (status, proposal_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Entity rename / delete (for suspect entity review)
    # ------------------------------------------------------------------

    def rename_entity(self, old_name: str, new_name: str) -> int:
        """Rename an entity in all active facts (subject + object columns).

        Returns number of fact rows updated.
        """
        updated = 0
        cur = self.conn.execute(
            "UPDATE facts SET subject = ? WHERE subject = ? AND status = 'active'",
            (new_name, old_name),
        )
        updated += cur.rowcount
        cur = self.conn.execute(
            "UPDATE facts SET object = ? WHERE object = ? AND status = 'active'",
            (new_name, old_name),
        )
        updated += cur.rowcount
        self.conn.commit()
        logger.info("Entity renamed: '%s' → '%s' (%d facts updated)", old_name, new_name, updated)
        return updated

    def delete_entity_facts(self, entity_name: str) -> int:
        """Soft-delete all facts involving an entity.

        Returns number of facts soft-deleted.
        """
        cur = self.conn.execute(
            """UPDATE facts SET status = 'deleted'
               WHERE status = 'active' AND (subject = ? OR object = ?)""",
            (entity_name, entity_name),
        )
        self.conn.commit()
        logger.info("Entity facts soft-deleted: '%s' → %d facts", entity_name, cur.rowcount)
        return cur.rowcount

    # ------------------------------------------------------------------
    # DLQ (failed_chunks)
    # ------------------------------------------------------------------

    def enqueue_failed_chunk(
        self,
        chunk_id: int,
        file_id: int,
        error_class: str,
        error_msg: str,
    ) -> int:
        self.conn.execute(
            """INSERT INTO failed_chunks (chunk_id, file_id, error_class, error_msg)
               VALUES (?, ?, ?, ?)""",
            (chunk_id, file_id, error_class, error_msg),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_pending_dlq(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT * FROM failed_chunks
               WHERE status = 'pending'
               AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))
               LIMIT ?""",
            (limit,),
        ).fetchall()

    def update_dlq_entry(
        self, entry_id: int, status: str, retry_count: int | None = None
    ) -> None:
        if retry_count is not None:
            self.conn.execute(
                "UPDATE failed_chunks SET status = ?, retry_count = ? WHERE id = ?",
                (status, retry_count, entry_id),
            )
        else:
            self.conn.execute(
                "UPDATE failed_chunks SET status = ? WHERE id = ?", (status, entry_id)
            )
        self.conn.commit()

    def prune_dlq(self, days: int = 30) -> int:
        cur = self.conn.execute(
            "DELETE FROM failed_chunks WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        self.conn.commit()
        return cur.rowcount

    def count_failed_by_class(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT error_class, COUNT(*) as cnt FROM failed_chunks "
            "WHERE status = 'pending' GROUP BY error_class"
        ).fetchall()

    def get_all_dlq(
        self, status: str | None = None, limit: int = 100
    ) -> list[sqlite3.Row]:
        """Return DLQ entries with file path for UI display."""
        query = (
            "SELECT fc.*, f.path AS file_path "
            "FROM failed_chunks fc "
            "LEFT JOIN files f ON fc.file_id = f.id "
        )
        params: tuple = ()
        if status:
            query += " WHERE fc.status = ?"
            params = (status,)
        query += " ORDER BY fc.created_at DESC LIMIT ?"
        params = params + (limit,)
        return self.conn.execute(query, params).fetchall()

    def retry_single_dlq(self, entry_id: int) -> bool:
        """Mark a single DLQ entry for retry by resetting status to pending."""
        cur = self.conn.execute(
            "UPDATE failed_chunks SET status = 'pending', "
            "next_retry_at = datetime('now') WHERE id = ? AND status != 'done'",
            (entry_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------
    # Chat history
    # ------------------------------------------------------------------

    def save_chat_message(
        self,
        kb_name: str,
        session_id: str,
        role: str,
        content: str,
        sources: str | None = None,
        related_facts: str | None = None,
    ) -> int:
        """Persist a single chat turn. Returns the new row ID."""
        self.conn.execute(
            """INSERT INTO chat_history
               (kb_name, session_id, role, content, sources, related_facts)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (kb_name, session_id, role, content,
             sources or "[]", related_facts or "[]"),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def update_chat_feedback(
        self, message_id: int, feedback_type: str,
    ) -> None:
        """Record feedback (positive/negative) on a chat message."""
        self.conn.execute(
            "UPDATE chat_history SET feedback_given = ? WHERE id = ?",
            (feedback_type, message_id),
        )
        self.conn.commit()

    def get_chat_sessions(self, kb_name: str) -> list[dict]:
        """Return distinct session ids + metadata for a KB, newest first."""
        rows = self.conn.execute(
            """SELECT session_id,
                      MIN(created_at) AS created_at,
                      COUNT(*) FILTER (WHERE role = 'user') AS turns
               FROM chat_history
               WHERE kb_name = ?
               GROUP BY session_id
               ORDER BY MAX(created_at) DESC""",
            (kb_name,),
        ).fetchall()
        return [{"session_id": r["session_id"],
                 "created_at": r["created_at"],
                 "turns": r["turns"]} for r in rows]

    def get_chat_history(
        self, kb_name: str, session_id: str,
    ) -> list[dict]:
        """Return all messages for a session in chronological order."""
        rows = self.conn.execute(
            """SELECT id, role, content, sources, related_facts,
                      feedback_given, created_at
               FROM chat_history
               WHERE kb_name = ? AND session_id = ?
               ORDER BY id ASC""",
            (kb_name, session_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_chat_session(self, kb_name: str, session_id: str) -> int:
        """Delete all messages for a session. Returns count of deleted rows."""
        cur = self.conn.execute(
            "DELETE FROM chat_history WHERE kb_name = ? AND session_id = ?",
            (kb_name, session_id),
        )
        self.conn.commit()
        return cur.rowcount

    def batch_entity_operations(
        self,
        operations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Execute a batch of entity rename/delete operations in one transaction.

        After all operations complete, the caller is responsible for
        rebuilding the graph and FAISS index.

        Args:
            operations: List of {"action": "rename"|"delete", "entity": str,
                                  "new_name": str (rename only)} dicts.

        Returns:
            List of result dicts with "action", "entity", "success", and
            action-specific fields ("facts_updated" or "facts_deleted").
        """
        results: list[dict[str, Any]] = []

        for op in operations:
            action = op.get("action", "")
            entity = op.get("entity", "")

            if not entity:
                results.append({
                    "action": action, "entity": entity,
                    "success": False, "error": "entity name is required",
                })
                continue

            if action == "rename":
                new_name = op.get("new_name", "").strip()
                if not new_name or new_name == entity:
                    results.append({
                        "action": "rename", "entity": entity,
                        "success": False, "error": "new_name is required and must differ",
                    })
                    continue

                try:
                    updated = self.rename_entity(entity, new_name)
                    # Insert protection record for new name
                    self.insert_proposal(
                        entity_a=new_name,
                        entity_b="",
                        confidence=1.0,
                        status="approved",
                        proposal_type="suspect",
                        llm_response=json.dumps({
                            "reason": f"用户从「{entity}」批量重命名",
                            "flags": [],
                            "gibberish_score": 0,
                        }, ensure_ascii=False),
                    )
                    results.append({
                        "action": "rename", "entity": entity,
                        "new_name": new_name, "success": True,
                        "facts_updated": updated,
                    })
                except Exception as e:
                    logger.error("Batch rename failed for '%s': %s", entity, e)
                    results.append({
                        "action": "rename", "entity": entity,
                        "success": False, "error": str(e),
                    })

            elif action == "delete":
                try:
                    deleted = self.delete_entity_facts(entity)
                    results.append({
                        "action": "delete", "entity": entity,
                        "success": True, "facts_deleted": deleted,
                    })
                except Exception as e:
                    logger.error("Batch delete failed for '%s': %s", entity, e)
                    results.append({
                        "action": "delete", "entity": entity,
                        "success": False, "error": str(e),
                    })
            else:
                results.append({
                    "action": action, "entity": entity,
                    "success": False, "error": f"unknown action: {action}",
                })

        self.conn.commit()
        return results

    def list_entities_with_stats(
        self,
        *,
        search: str | None = None,
        sort_by: str = "count",
        order: str = "desc",
        page: int = 1,
        page_size: int = 50,
        suspect_only: bool = False,
        merge_proposal_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """List all entities with occurrence count and metadata.

        Args:
            search: Optional substring filter on entity name.
            sort_by: "count" (occurrence), "name" (alphabetical), or "degree".
            order: "asc" or "desc".
            page: 1-based page number.
            page_size: Items per page (max 200).
            suspect_only: Only return entities with pending suspect proposals.
            merge_proposal_only: Only return entities with pending merge proposals.

        Returns:
            (entities_list, total_count)
            Each entity dict: {name, fact_count, has_suspect_proposal,
                               has_merge_proposal, suspect_flags}
        """
        page_size = min(page_size, 200)
        offset = (page - 1) * page_size

        # Build the base query: entity occurrence counts
        where_clauses: list[str] = []
        having_clauses: list[str] = []
        params: list[Any] = []
        count_params: list[Any] = []

        if search:
            where_clauses.append("e.entity LIKE ?")
            params.append(f"%{search}%")

        # ── suspect_only / merge_proposal_only: push filter into SQL ──
        # Using JOIN instead of post-filter so filtering happens BEFORE
        # pagination.  Otherwise low-count suspect entities never appear
        # on the first page.
        join_clause = ""
        if suspect_only:
            join_clause = (
                "JOIN entity_proposals ep ON e.entity = ep.entity_a "
                "AND ep.proposal_type = 'suspect' AND ep.status = 'proposed'"
            )
        elif merge_proposal_only:
            join_clause = (
                "JOIN entity_proposals ep ON (e.entity = ep.entity_a OR e.entity = ep.entity_b) "
                "AND ep.proposal_type = 'merge' AND ep.status = 'proposed'"
            )

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # ── Literal-value filtering: match graph_store.rebuild_from_facts() ──
        # Graph logic: ALL subjects become nodes; objects only if NOT literal.
        # We replicate that exactly so entity list count == graph node count.
        from filekb.entity_qa import is_literal_value

        # Get all distinct subjects (always included, matching graph behavior)
        subj_sql = f"""
            SELECT e.entity FROM (
                SELECT DISTINCT subject AS entity FROM facts WHERE status = 'active'
            ) e
            {join_clause}
            {where_sql}
        """
        subj_rows = self.conn.execute(subj_sql, params).fetchall()
        subject_set = {r["entity"] for r in subj_rows}

        # Get all distinct objects (only non-literal ones count)
        obj_sql = f"""
            SELECT e.entity FROM (
                SELECT DISTINCT object AS entity FROM facts WHERE status = 'active'
            ) e
            {join_clause}
            {where_sql}
        """
        obj_rows = self.conn.execute(obj_sql, params).fetchall()
        non_literal_objs = {r["entity"] for r in obj_rows if not is_literal_value(r["entity"])}

        # Valid entities = same set the graph would produce
        valid_entities = subject_set | non_literal_objs
        total = len(valid_entities)

        order_clause = "ORDER BY total_count DESC"
        if sort_by == "name":
            order_clause = "ORDER BY entity ASC" if order == "asc" else "ORDER BY entity DESC"
        elif sort_by == "degree":
            order_clause = "ORDER BY total_count DESC"  # degree ≈ count proxy
        # default: count desc

        # Fetch extra rows to account for literal-value filtering
        fetch_limit = min(page_size * 3, 1000)
        query_sql = f"""
            SELECT
                e.entity,
                SUM(e.cnt) AS total_count
            FROM (
                SELECT subject AS entity, COUNT(*) AS cnt
                FROM facts WHERE status = 'active'
                GROUP BY subject
                UNION ALL
                SELECT object AS entity, COUNT(*) AS cnt
                FROM facts WHERE status = 'active'
                GROUP BY object
            ) e
            {join_clause}
            {where_sql}
            GROUP BY e.entity
            {order_clause}
            LIMIT ? OFFSET 0
        """
        rows = self.conn.execute(query_sql, params + [fetch_limit]).fetchall()

        # ── Filter: keep only entities the graph would keep ──
        rows = [r for r in rows if r["entity"] in valid_entities]

        # Slice for the requested page
        offset = (page - 1) * page_size
        page_rows = rows[offset:offset + page_size]

        if not page_rows:
            return [], total

        # Gather entity names for proposal lookups
        entity_names = [r["entity"] for r in page_rows]
        placeholders = ",".join("?" * len(entity_names))

        # Fetch suspect proposals for these entities (for the badge display)
        suspect_rows = self.conn.execute(
            f"""SELECT entity_a FROM entity_proposals
                WHERE entity_a IN ({placeholders})
                  AND proposal_type = 'suspect'
                  AND status = 'proposed'""",
            entity_names,
        ).fetchall()
        suspect_set = {r["entity_a"] for r in suspect_rows}

        # Fetch merge proposals for these entities (for the badge display)
        merge_set: set[str] = set()
        if not merge_proposal_only:  # already filtered by JOIN if set
            merge_rows = self.conn.execute(
                f"""SELECT entity_a, entity_b FROM entity_proposals
                    WHERE (entity_a IN ({placeholders}) OR entity_b IN ({placeholders}))
                      AND proposal_type = 'merge'
                      AND status = 'proposed'""",
                entity_names + entity_names,
            ).fetchall()
            for r in merge_rows:
                if r["entity_a"] in entity_names:
                    merge_set.add(r["entity_a"])
                if r["entity_b"] in entity_names:
                    merge_set.add(r["entity_b"])

        # Build result
        entities_list: list[dict[str, Any]] = []
        for row in page_rows:
            name = row["entity"]
            e = {
                "name": name,
                "fact_count": row["total_count"],
                "has_suspect_proposal": name in suspect_set,
                "has_merge_proposal": name in merge_set,
                "suspect_flags": [],
            }
            entities_list.append(e)

        return entities_list, total

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
