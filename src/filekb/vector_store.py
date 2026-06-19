"""FAISS vector index for semantic search.

Uses IndexFlatIP (inner product) for exact search — sufficient for
personal-scale KB (<100K vectors, <10ms search latency).

Per ADR-2: FlatIP over HNSW because:
- Exact search, no approximation (important for provenance)
- <10ms on 100K vectors
- One-line switch to IndexIVFFlat or IndexHNSWFlat at scale
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class VectorStore:
    """FAISS IndexFlatIP vector index with save/load support."""

    def __init__(self, dimension: int = 1024):
        import faiss

        self.dimension = dimension
        self.index = faiss.IndexFlatIP(dimension)  # Inner product = cosine for normalized vectors
        self._fact_ids: list[int] = []  # Maps FAISS internal ID → fact DB ID

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def add(self, embeddings: np.ndarray, fact_ids: list[int]) -> None:
        """Add vectors to the index.

        Args:
            embeddings: (N, dimension) float32 array, must be L2-normalized.
            fact_ids: Corresponding fact database IDs.
        """
        if embeddings.ndim == 1:
            embeddings = embeddings.reshape(1, -1)

        if embeddings.shape[0] != len(fact_ids):
            raise ValueError(
                f"embeddings row count ({embeddings.shape[0]}) != "
                f"fact_ids length ({len(fact_ids)})"
            )

        embeddings = np.asarray(embeddings, dtype=np.float32)
        self.index.add(embeddings)
        self._fact_ids.extend(fact_ids)

    def search(self, query: np.ndarray, k: int = 20) -> list[tuple[int, float]]:
        """Search for k nearest neighbors.

        Args:
            query: (dimension,) float32 query vector, L2-normalized.
            k: Number of results.

        Returns:
            List of (fact_id, similarity_score) tuples sorted by descending score.
        """
        if self.index.ntotal == 0:
            return []

        query = np.asarray(query, dtype=np.float32).reshape(1, -1)
        k = min(k, self.index.ntotal)

        scores, indices = self.index.search(query, k)

        results: list[tuple[int, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._fact_ids):
                continue
            results.append((self._fact_ids[idx], float(score)))

        return results

    def remove_by_fact_ids(self, fact_ids: set[int]) -> int:
        """Remove vectors for given fact IDs. Requires index rebuild.

        FAISS IndexFlatIP doesn't support removal — this marks IDs for
        exclusion and returns count. Caller should rebuild index periodically.

        Returns:
            Number of fact IDs marked.
        """
        removed = 0
        new_ids = []
        for fid in self._fact_ids:
            if fid in fact_ids:
                removed += 1
            else:
                new_ids.append(fid)
        self._fact_ids = new_ids
        return removed

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, directory: str | Path) -> None:
        """Save FAISS index and fact ID mapping to disk."""
        import faiss

        directory = Path(directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)

        index_path = directory / "faiss.index"
        ids_path = directory / "fact_ids.bin"

        faiss.write_index(self.index, str(index_path))

        with open(ids_path, "wb") as f:
            f.write(struct.pack("I", len(self._fact_ids)))
            for fid in self._fact_ids:
                f.write(struct.pack("q", fid))

        logger.info("Saved FAISS index: %d vectors to %s", self.index.ntotal, directory)

    def load(self, directory: str | Path) -> bool:
        """Load FAISS index and fact ID mapping from disk.

        Returns:
            True if load succeeded, False if files not found.
        """
        import faiss

        directory = Path(directory).expanduser()
        index_path = directory / "faiss.index"
        ids_path = directory / "fact_ids.bin"

        if not index_path.exists() or not ids_path.exists():
            logger.debug("No saved FAISS index at %s", directory)
            return False

        self.index = faiss.read_index(str(index_path))
        self.dimension = self.index.d

        with open(ids_path, "rb") as f:
            count = struct.unpack("I", f.read(4))[0]
            self._fact_ids = [struct.unpack("q", f.read(8))[0] for _ in range(count)]

        logger.info("Loaded FAISS index: %d vectors from %s", self.index.ntotal, directory)
        return True

    def rebuild_from_sqlite(self, store) -> None:  # type: ignore[no-untyped-def]
        """Rebuild the entire FAISS index from fact embeddings in SQLite.

        This is the primary update path — called after each index run
        because FAISS IndexFlatIP doesn't support incremental removal.
        """
        import faiss

        fact_ids = store.get_all_active_fact_ids()
        if not fact_ids:
            self.index = faiss.IndexFlatIP(self.dimension)
            self._fact_ids = []
            return

        embeddings: list[np.ndarray] = []
        valid_ids: list[int] = []

        for fid in fact_ids:
            emb_bytes = store.get_fact_embedding(fid)
            if emb_bytes:
                emb = np.frombuffer(emb_bytes, dtype=np.float32)
                if emb.shape[0] == self.dimension:
                    embeddings.append(emb)
                    valid_ids.append(fid)

        if embeddings:
            emb_matrix = np.stack(embeddings)
            self.index = faiss.IndexFlatIP(self.dimension)
            self.index.add(emb_matrix)
            self._fact_ids = valid_ids

        logger.info("Rebuilt FAISS index: %d vectors", self.index.ntotal)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        return self.index.ntotal

    def __len__(self) -> int:
        return self.index.ntotal
