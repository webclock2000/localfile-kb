"""Text splitter — natural boundary chunking.

Splits parsed document text into chunks at natural boundaries:
    paragraph → line break → sentence → character

Uses exponential backoff: try the preferred boundary first, then
fall back to coarser boundaries if the chunk is too small.

Configurable:
    max_chars_per_chunk (default: 24000)
    overlap_chars (default: 500)

Also provides detect_chinese() for routing to the Chinese
extraction prompt.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# CJK Unicode ranges
_CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x20000, 0x2A6DF), # CJK Unified Ideographs Extension B
    (0x2A700, 0x2B73F), # CJK Unified Ideographs Extension C
    (0x2B740, 0x2B81F), # CJK Unified Ideographs Extension D
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F), # CJK Compatibility Ideographs Supplement
    (0x3000, 0x303F),   # CJK Symbols and Punctuation
    (0xFF00, 0xFFEF),   # Halfwidth and Fullwidth Forms
    (0x2E80, 0x2EFF),   # CJK Radicals Supplement
    (0x31C0, 0x31EF),   # CJK Strokes
]


def _is_cjk(char: str) -> bool:
    """Check if a character is in CJK Unicode ranges."""
    cp = ord(char)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def detect_chinese(text: str) -> bool:
    """Check if text is predominantly Chinese (>50% CJK characters by default).

    Args:
        text: The text to analyze.

    Returns:
        True if ratio of CJK characters exceeds threshold.
    """
    if not text:
        return False
    cjk_count = sum(1 for c in text if _is_cjk(c))
    total = len(text)
    # Count only letters and CJK, ignore whitespace/digits/punctuation for ratio
    relevant = sum(1 for c in text if c.isalpha() or _is_cjk(c))
    if relevant == 0:
        return False
    return (cjk_count / relevant) >= 0.50


def _split_paragraphs(text: str) -> list[str]:
    """Split text at paragraph boundaries (double newline)."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _split_lines(text: str) -> list[str]:
    """Split text at single newlines."""
    return [l.strip() for l in text.splitlines() if l.strip()]


def _split_sentences(text: str) -> list[str]:
    """Split text at sentence boundaries (period, question mark, etc. followed by space)."""
    return [
        s.strip()
        for s in re.split(r"(?<=[.!?。！？])\s+", text)
        if s.strip()
    ]


def chunk_text(
    text: str,
    max_chars: int = 24000,
    overlap_chars: int = 500,
) -> list[str]:
    """Split text into overlapping chunks at natural boundaries.

    Strategy (tries each boundary type, falling back to coarser splits):
    1. Split at paragraph boundaries, merge small paragraphs into chunks
    2. If a single paragraph exceeds max_chars, split it at line boundaries
    3. If a single line exceeds max_chars, split at sentence boundaries
    4. If a single sentence exceeds max_chars, hard-split at max_chars

    Args:
        text: Full document text to split.
        max_chars: Maximum characters per chunk.
        overlap_chars: Number of overlapping characters between consecutive chunks.

    Returns:
        List of chunk strings.
    """
    if not text or not text.strip():
        return []

    # Step 1: Split at paragraph boundaries
    paragraphs = _split_paragraphs(text)
    chunks = _merge_with_limit(paragraphs, max_chars)

    # Step 2-4: Handle oversized chunks with progressively finer boundaries
    final_chunks: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final_chunks.append(chunk)
        else:
            # Try line-level split
            sub = _split_lines(chunk)
            sub_merged = _merge_with_limit(sub, max_chars)
            for s in sub_merged:
                if len(s) <= max_chars:
                    final_chunks.append(s)
                else:
                    # Try sentence-level split
                    sent = _split_sentences(s)
                    sent_merged = _merge_with_limit(sent, max_chars)
                    for ss in sent_merged:
                        if len(ss) <= max_chars:
                            final_chunks.append(ss)
                        else:
                            # Hard split at character boundary
                            final_chunks.extend(_hard_split(ss, max_chars))

    # Add overlap between consecutive chunks
    if overlap_chars > 0 and len(final_chunks) > 1:
        overlapped = [final_chunks[0]]
        for i in range(1, len(final_chunks)):
            prev = final_chunks[i - 1]
            curr = final_chunks[i]
            if len(prev) > overlap_chars:
                overlap = prev[-overlap_chars:]
                overlapped.append(overlap + "\n\n" + curr)
            else:
                overlapped.append(curr)
        return overlapped

    return final_chunks


def _merge_with_limit(parts: list[str], max_chars: int) -> list[str]:
    """Merge small parts into chunks that fit within max_chars."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for part in parts:
        part_len = len(part)
        if current_len + part_len + (2 if current else 0) <= max_chars:
            current.append(part)
            current_len += part_len + (2 if len(current) > 1 else 0)
        else:
            if current:
                chunks.append("\n\n".join(current))
            if part_len > max_chars:
                # Single part too large — will be handled by caller
                chunks.append(part)
                current = []
                current_len = 0
            else:
                current = [part]
                current_len = part_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Brute-force split at max_chars boundary (last resort)."""
    chunks = []
    for i in range(0, len(text), max_chars):
        chunks.append(text[i : i + max_chars])
    return chunks
