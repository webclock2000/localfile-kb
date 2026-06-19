"""Embedding model — supports local sentence-transformers and oMLX API.

Default: oMLX API (faster, no extra memory).
Fallback: local sentence-transformers (offline, ~2GB RAM).

Both backends produce 1024-dim L2-normalized vectors.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# State
_backend: str = "omlx"  # "omlx" | "local"
_local_model: SentenceTransformer | None = None
_local_model_name: str = "BAAI/bge-m3"
_local_device: str = "cpu"
_omlx_url: str = "http://127.0.0.1:8081/v1"
_omlx_model: str = "bge-m3-mlx-fp16"
_normalize: bool = True


def configure(
    backend: str = "omlx",
    model_name: str = "bge-m3-mlx-fp16",
    omxl_url: str = "http://127.0.0.1:8081/v1",
    device: str = "cpu",
    normalize: bool = True,
) -> None:
    """Configure the embedding backend.

    Args:
        backend: 'omlx' (use oMLX API) or 'local' (sentence-transformers).
        model_name: For omxl: model name in oMLX. For local: HF model ID.
        omxl_url: oMLX base URL (only for backend='omlx').
        device: 'cpu' or 'mps' (only for backend='local').
        normalize: L2-normalize output vectors.
    """
    global _backend, _local_model_name, _local_device, _omlx_url, _omlx_model, _normalize
    _backend = backend
    _normalize = normalize
    if backend == "local":
        _local_model_name = model_name
        _local_device = device
    else:
        _omlx_url = omxl_url.rstrip("/")
        _omlx_model = model_name


def _embed_omlx(texts: list[str]) -> np.ndarray:
    """Embed via oMLX API."""
    import httpx

    client = httpx.Client(timeout=30)
    resp = client.post(
        f"{_omlx_url}/embeddings",
        json={"model": _omlx_model, "input": texts},
    )
    resp.raise_for_status()
    data = resp.json()
    embeddings = [d["embedding"] for d in data["data"]]
    return np.array(embeddings, dtype=np.float32)


def _embed_local(texts: list[str], batch_size: int = 32) -> np.ndarray:
    """Embed via local sentence-transformers."""
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading local embedding model %s on %s", _local_model_name, _local_device)
        _local_model = SentenceTransformer(_local_model_name, device=_local_device)
    embeddings = _local_model.encode(
        texts, normalize_embeddings=_normalize, batch_size=batch_size, show_progress_bar=False,
    )
    return np.asarray(embeddings, dtype=np.float32)


def embed_single(text: str) -> np.ndarray:
    """Embed a single text to a 1024-dim vector."""
    arr = embed_batch([text])
    return arr[0]


def embed_batch(texts: list[str], batch_size: int = 32) -> np.ndarray:
    """Embed a batch of texts."""
    if _backend == "omlx":
        return _embed_omlx(texts)
    return _embed_local(texts, batch_size=batch_size)


def embed_query(question: str) -> np.ndarray:
    """Embed a query (same as embed_single for bge-m3)."""
    return embed_single(question)


def embed_fact(subject: str, predicate: str, object_text: str,
               title: str = "", description: str = "") -> bytes:
    """Embed a fact triplet for FAISS storage."""
    text = f"{title}\n{subject} {predicate} {object_text}"
    if description:
        text += f"\n{description}"
    return embed_single(text).tobytes()


def decode_embedding(data: bytes) -> np.ndarray:
    """Decode embedding bytes back to numpy array."""
    return np.frombuffer(data, dtype=np.float32)


def get_dimension() -> int:
    return 1024


def is_loaded() -> bool:
    return _backend == "omlx" or _local_model is not None
