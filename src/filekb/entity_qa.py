"""Entity quality assessment — automatic suspicious entity detection.

Pure-local detection rules with zero LLM token cost. Flags entities that
are likely OCR errors, gibberish, or otherwise low-quality for human review.

Detection rules:
  1. Character count anomaly — single-char or excessively long entities
  2. Gibberish score — unusual Unicode ranges, repeated n-grams, low entropy
  3. Jieba word validity — Chinese entity fails to segment into valid words
  4. Graph isolation — entity has zero edges in the knowledge graph
  5. OCR source — all source files are OCR-recovered or image-based

An entity flagged by >=2 rules is marked as "suspect" and queued for review.
Exception: "not_a_word" from an image/OCR source counts as 2 (harsh on OCR).
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# Unicode ranges for CJK characters
_CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x20000, 0x2A6DF), # CJK Unified Ideographs Extension B
    (0x2A700, 0x2B73F), # CJK Unified Ideographs Extension C
    (0x2B740, 0x2B81F), # CJK Unified Ideographs Extension D
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
]

# Characters that look like noise but could appear in real text
_SUSPICIOUS_CHARS = re.compile(
    r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f'  # Control chars
    r'�￾￿'                      # Unicode replacement/specials
    r'​‌‍‎‏'          # Zero-width chars
    r'﻿'                                    # BOM
    r']'
)

# Characters that shouldn't repeat excessively in entity names
_REPEAT_THRESHOLD = 4  # same char repeating >= this many times is suspicious


def _is_cjk(ch: str) -> bool:
    """Check if a character is within CJK Unicode ranges."""
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _char_entropy(text: str) -> float:
    """Compute Shannon entropy of characters in text.

    Low entropy (<2.0) suggests repetitive or gibberish content.
    High entropy (>4.5) with mixed scripts also suspicious.
    """
    if not text:
        return 0.0
    counter = Counter(text)
    length = len(text)
    entropy = 0.0
    for count in counter.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


def _repeated_ngram_ratio(text: str, n: int = 2) -> float:
    """Ratio of character n-grams that repeat (suspect if high)."""
    if len(text) < n * 2:
        return 0.0
    ngrams = [text[i:i+n] for i in range(len(text) - n + 1)]
    counter = Counter(ngrams)
    repeated = sum(1 for c in counter.values() if c > 1)
    return repeated / len(counter) if counter else 0.0


def _has_repeated_chars(text: str, threshold: int = _REPEAT_THRESHOLD) -> bool:
    """Check if any single character repeats >= threshold times."""
    counter = Counter(text)
    return any(c >= threshold for c in counter.values())


def compute_gibberish_score(name: str) -> float:
    """Compute a gibberish score (0.0 = normal, 1.0 = definitely gibberish).

    Weights multiple signals:
    - Control/suspicious characters: +0.4 per occurrence (capped at 0.4)
    - Very low character entropy (<1.5): +0.3
    - High repeated n-gram ratio (>0.5): +0.3
    - Repeated single character (>=4 times): +0.2
    - Mixed CJK + random Latin in unnatural pattern: +0.2
    """
    if not name or not name.strip():
        return 1.0

    score = 0.0
    signals: list[str] = []

    # Signal 1: suspicious characters
    suspicious_count = len(_SUSPICIOUS_CHARS.findall(name))
    if suspicious_count > 0:
        score += min(0.4, suspicious_count * 0.1)
        signals.append(f"suspicious_chars={suspicious_count}")

    # Signal 2: low character entropy
    entropy = _char_entropy(name)
    if entropy < 1.5 and len(name) >= 3:
        score += 0.3
        signals.append(f"low_entropy={entropy:.2f}")

    # Signal 3: high repeated n-gram ratio
    ngram_ratio = _repeated_ngram_ratio(name, n=2)
    if ngram_ratio > 0.5:
        score += 0.3
        signals.append(f"repeated_ngram={ngram_ratio:.2f}")

    # Signal 4: repeated single character
    if _has_repeated_chars(name):
        score += 0.2
        signals.append("repeated_char")

    # Signal 5: unnatural mixed script
    cjk_chars = sum(1 for ch in name if _is_cjk(ch))
    latin_chars = sum(1 for ch in name if ch.isascii() and ch.isalpha())
    total = len(name)
    if total > 0 and cjk_chars > 0 and latin_chars > 0:
        # Natural mixed-script names (e.g. "iPhone 15 Pro") have meaningful Latin.
        # Gibberish like "才代A米B究" has scattered Latin among CJK.
        if latin_chars <= 2 and cjk_chars >= 3:
            score += 0.2
            signals.append("mixed_script_suspect")

    if signals:
        logger.debug("Gibberish score %.2f for '%s': %s", score, name, ", ".join(signals))

    return min(1.0, score)


# ============================================================================
# Jieba word validity
# ============================================================================

_jieba_loaded: bool = False


def _ensure_jieba() -> bool:
    """Lazy-load jieba."""
    global _jieba_loaded
    if _jieba_loaded:
        return True
    try:
        import jieba
        jieba.initialize()
        _jieba_loaded = True
        return True
    except ImportError:
        logger.debug("jieba not installed, skipping word validity check")
        return False
    except Exception as e:
        logger.warning("jieba init failed: %s", e)
        return False


# Common Chinese entity suffixes — entities ending with these are likely valid
_COMMON_ENTITY_SUFFIXES = frozenset({
    # Organizations
    "大学", "学院", "中学", "小学", "学校", "研究院", "研究所",
    "公司", "集团", "中心", "部门", "委员会", "办公室", "办事处",
    "银行", "医院", "图书馆", "博物馆",
    # Places
    "省", "市", "县", "区", "镇", "村", "街道",
    "山", "河", "湖", "海", "岛",
    # Person-related
    "教授", "博士", "老师", "先生", "女士", "同学",
    # Projects/Abstracts
    "项目", "计划", "方案", "报告", "通知", "办法", "规定",
    "系统", "平台", "模型", "方法", "技术", "理论", "工程",
    "专业", "课程", "教材", "论文", "实验",
})

# Characters that commonly appear at the start of Chinese names
_COMMON_SURNAME_CHARS = frozenset(
    "王李张刘陈杨黄赵周吴徐孙马胡朱郭何罗高林郑梁谢唐许冯宋韩邓彭曹曾田萧潘袁蔡蒋余于杜叶程魏苏吕丁任卢姚沈钟姜崔谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤温康"
)


def _is_valid_chinese_word(name: str) -> bool:
    """Check if a Chinese entity name segments into plausible words.

    Uses jieba to segment the name and validates against:
    - Common entity suffixes (学校, 公司, 项目, etc.)
    - Character n-gram plausibility
    - Single-character entity names are OK (could be surnames)

    Names with <=2 CJK characters get a pass unless they contain
    extremely rare characters.
    """
    if not _ensure_jieba():
        return True  # Can't check, assume valid

    import jieba

    cjk_chars = [ch for ch in name if _is_cjk(ch)]
    cjk_count = len(cjk_chars)

    # Single-char entities: OK (e.g., surnames)
    if cjk_count <= 1:
        return True

    # 2-char entities: check if it looks like a valid Chinese name
    if cjk_count == 2:
        # Common pattern: Surname + given name (e.g., "王伟")
        if cjk_chars[0] in _COMMON_SURNAME_CHARS:
            return True
        # Otherwise, check if jieba recognizes it as a word
        segments = list(jieba.cut(name))
        if len(segments) == 1 and len(segments[0]) == 2:
            return True  # Recognized 2-char word
        # Unknown 2-char entity: slightly suspicious but give benefit of doubt
        return True

    # 3+ CJK characters: full validation
    segments = list(jieba.cut(name))

    # Rule A: Check for known entity suffix
    has_known_suffix = any(name.endswith(s) for s in _COMMON_ENTITY_SUFFIXES)
    if has_known_suffix:
        return True

    # Rule B: Check if this looks like a Chinese person name (surname + given name)
    # e.g., "赵文吉", "杜世宏" — 3 chars, jieba may HMM-merge into one segment
    if cjk_count == 3 and cjk_chars[0] in _COMMON_SURNAME_CHARS:
        return True  # Likely a person name, don't flag

    # Rule C: Multi-char segment analysis
    multi_char_segments = [s for s in segments if len(s) >= 2 and all(_is_cjk(ch) for ch in s)]
    single_cjk = sum(1 for s in segments if len(s) == 1 and _is_cjk(s))

    # If most CJK chars are in single-char segments → gibberish
    if cjk_count >= 3 and single_cjk > cjk_count * 0.5:
        return False

    # If only one multi-char segment found by HMM, and it's 3+ chars,
    # and the entity has no common suffix → probably HMM-made-up word
    if cjk_count >= 3 and len(multi_char_segments) == 1 and len(multi_char_segments[0]) >= 3:
        if not has_known_suffix:
            return False

    # Rule D: If ALL segments are single characters except one HMM-invented long word
    if cjk_count >= 4 and len(segments) <= 2 and len(multi_char_segments) <= 1:
        return False

    return True


# ============================================================================
# Entity quality check — main entry point
# ============================================================================


# Image file extensions — entities sourced only from these are OCR-dependent
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif", ".webp"}


def check_entity(
    name: str,
    *,
    degree: int = 0,
    source_files: list[str] | None = None,
    ocr_file_count: int = 0,
) -> dict[str, Any]:
    """Run all detection rules against a single entity.

    Args:
        name: Entity name.
        degree: Graph degree (number of connected edges).
        source_files: List of source file paths for this entity.
        ocr_file_count: Number of source files that were OCR-recovered.

    Returns:
        {
            "entity": str,
            "flags": [str],           # which rules flagged it
            "gibberish_score": float, # 0-1
            "is_suspect": bool,       # overall verdict
            "reason": str,            # human-readable summary
        }
    """
    flags: list[str] = []

    # Rule 1: Character count anomaly
    char_count = len(name)
    if char_count <= 1:
        flags.append("too_short")
    elif char_count > 15:
        flags.append("too_long")

    # Rule 2: Gibberish score
    gib_score = compute_gibberish_score(name)
    if gib_score >= 0.3:
        flags.append("gibberish")

    # Rule 3: Jieba word validity (Chinese only)
    has_cjk = any(_is_cjk(ch) for ch in name)
    if has_cjk and not _is_valid_chinese_word(name):
        flags.append("not_a_word")

    # Rule 4: Graph isolation
    if degree == 0:
        flags.append("isolated")

    # Rule 5: OCR / image source check
    # ocr_only: sources were OCR-recovered (tracked via error_msg LIKE '%OCR%')
    # image_source: sources are image files (JPG/PNG/etc.) — always OCR
    is_ocr_sourced = False
    is_image_sourced = False
    if source_files:
        if ocr_file_count > 0 and ocr_file_count == len(source_files):
            flags.append("ocr_only")
            is_ocr_sourced = True
        if all(
            any(f.lower().endswith(ext) for ext in _IMAGE_EXTENSIONS)
            for f in source_files
        ):
            flags.append("image_source")
            is_image_sourced = True

    # Verdict: >=2 flags → suspect.
    # EXCEPTION: "not_a_word" from an image/OCR source is especially risky
    # (OCR is the biggest source of gibberish entities).  A single
    # "not_a_word" flag combined with image or OCR sourcing counts as 2.
    is_ocr_or_image = is_ocr_sourced or is_image_sourced
    if "not_a_word" in flags and is_ocr_or_image:
        is_suspect = True  # Harsh: bad word from OCR/image = always suspect
    else:
        is_suspect = len(flags) >= 2

    # Build human-readable reason
    reason_parts = []
    if "too_short" in flags:
        reason_parts.append("实体名过短（1个字符）")
    if "too_long" in flags:
        reason_parts.append(f"实体名过长（{char_count}个字符）")
    if "gibberish" in flags:
        reason_parts.append(f"疑似乱码（得分{gib_score:.2f}）")
    if "not_a_word" in flags:
        reason_parts.append("无法构成有效中文词组")
    if "isolated" in flags:
        reason_parts.append("图中无连接（孤立节点）")
    if "ocr_only" in flags:
        reason_parts.append("仅出现在OCR恢复文件中")
    if "image_source" in flags:
        reason_parts.append("来源为图片文件（OCR识别）")

    return {
        "entity": name,
        "flags": flags,
        "gibberish_score": gib_score,
        "degree": degree,
        "is_suspect": is_suspect,
        "reason": "；".join(reason_parts) if reason_parts else "正常",
        "ocr_file_count": ocr_file_count,
    }


def scan_entities(
    entities: list[str],
    *,
    graph_degrees: dict[str, int] | None = None,
    entity_sources: dict[str, list[str]] | None = None,
    ocr_files: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Batch scan entities and return suspect results.

    Args:
        entities: List of entity names to scan.
        graph_degrees: {entity_name: degree} mapping.
        entity_sources: {entity_name: [file_path, ...]} mapping.
        ocr_files: Set of file paths that were OCR-recovered.

    Returns:
        List of check_entity() results for suspect entities only.
    """
    if graph_degrees is None:
        graph_degrees = {}
    if entity_sources is None:
        entity_sources = {}
    if ocr_files is None:
        ocr_files = set()

    results: list[dict[str, Any]] = []

    for name in entities:
        degree = graph_degrees.get(name, 0)
        sources = entity_sources.get(name, [])
        ocr_count = sum(1 for s in sources if s in ocr_files)

        result = check_entity(
            name,
            degree=degree,
            source_files=sources if sources else None,
            ocr_file_count=ocr_count,
        )

        if result["is_suspect"]:
            results.append(result)

    # Sort by severity: more flags first, then higher gibberish score
    results.sort(key=lambda r: (len(r["flags"]), r["gibberish_score"]), reverse=True)

    logger.info("Entity QA scan: %d entities → %d suspects", len(entities), len(results))
    return results


# ============================================================================
# Statistics for reporting
# ============================================================================


def suspect_stats(results: list[dict[str, Any]]) -> dict[str, int]:
    """Count suspects by flag type."""
    stats: dict[str, int] = {}
    for r in results:
        for flag in r["flags"]:
            stats[flag] = stats.get(flag, 0) + 1
    return stats
