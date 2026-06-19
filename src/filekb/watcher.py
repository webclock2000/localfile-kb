"""File watcher — SHA256-based change detection.

Scans configured directories, computes SHA256 hashes for each file,
and classifies files as: added, modified, deleted, or unchanged.

Uses content hashing (not mtime) per ADR-7 — deterministic identity
that survives git checkout, touch, and copy operations.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default patterns to exclude from scanning
DEFAULT_EXCLUDES = {".git", "__pycache__", ".DS_Store", "node_modules", ".venv", "venv"}


def _should_exclude(path: Path, patterns: set[str]) -> bool:
    """Check if any part of the path matches an exclude pattern."""
    parts = set(path.parts)
    return bool(parts & patterns)


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file's contents."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def scan_directory(
    directory: Path,
    recursive: bool = True,
    exclude_patterns: set[str] | None = None,
) -> dict[str, str]:
    """Scan a directory and return {path: sha256} for all files.

    Args:
        directory: Root directory to scan.
        recursive: Whether to descend into subdirectories.
        exclude_patterns: Directory/file names to skip.

    Returns:
        Dict mapping absolute file path strings to SHA256 hex digests.
    """
    directory = Path(directory)
    if exclude_patterns is None:
        exclude_patterns = DEFAULT_EXCLUDES

    result: dict[str, str] = {}
    pattern = "**/*" if recursive else "*"

    for file_path in directory.glob(pattern):
        if not file_path.is_file():
            continue
        if _should_exclude(file_path, exclude_patterns):
            logger.debug("Excluded: %s", file_path)
            continue
        try:
            result[str(file_path)] = compute_sha256(file_path)
        except (PermissionError, OSError) as e:
            logger.warning("Cannot read %s: %s", file_path, e)

    return result


def detect_changes(
    current: dict[str, str],
    previous: dict[str, str],
) -> dict[str, list[str]]:
    """Compare current vs previous file hashes, classify changes.

    Args:
        current: {path: sha256} from current scan.
        previous: {path: sha256} from last indexed scan.

    Returns:
        {
            "added": [paths new since last scan],
            "modified": [paths whose content hash changed],
            "deleted": [paths in previous but not current],
            "unchanged": [paths with same hash],
        }
    """
    current_set = set(current.keys())
    previous_set = set(previous.keys())

    added = sorted(current_set - previous_set)
    deleted = sorted(previous_set - current_set)
    unchanged: list[str] = []
    modified: list[str] = []

    for path in current_set & previous_set:
        if current[path] == previous[path]:
            unchanged.append(path)
        else:
            modified.append(path)

    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": sorted(unchanged),
    }


def load_previous_hashes(store) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Load previously indexed file hashes from the database."""
    rows = store.conn.execute(
        "SELECT path, sha256 FROM files WHERE status != 'deleted'"
    ).fetchall()
    return {r["path"]: r["sha256"] for r in rows}
