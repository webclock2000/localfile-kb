"""Knowledge extraction pipeline.

Orchestrates multi-round LLM fact extraction with Chinese branch routing,
incremental JSON parsing, and semantic deduplication.

Per DEVELOPMENT_V3.md §7.2:
- k rounds per chunk (default k=2) with different seeds
- Incremental JSON parse with regex tolerance for truncation
- detect_chinese() routing to extract_zh.txt
- DeepKE boundary filter (optional, for Chinese content)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filekb.llm import LLMClient
from filekb.splitter import detect_chinese

logger = logging.getLogger(__name__)

# Load prompts lazily
_PROMPTS: dict[str, str] = {}


def _load_prompt(name: str) -> str:
    if name not in _PROMPTS:
        prompt_dir = Path(__file__).parent / "prompts"
        path = prompt_dir / name
        if path.exists():
            _PROMPTS[name] = path.read_text(encoding="utf-8")
        else:
            raise FileNotFoundError(f"Prompt file not found: {path}")
    return _PROMPTS[name]


@dataclass
class Fact:
    """A single extracted knowledge fact."""

    subject: str
    predicate: str
    object: str
    title: str = ""
    description: str = ""
    evidence_span: str = ""
    confidence: int = 50
    tags: list[str] = field(default_factory=list)
    source_file: str = ""
    chunk_id: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fact:
        return cls(
            subject=d.get("subject", ""),
            predicate=d.get("predicate", ""),
            object=d.get("object", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
            evidence_span=d.get("evidence_span", ""),
            confidence=d.get("confidence", 50),
            tags=d.get("tags", []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "title": self.title,
            "description": self.description,
            "evidence_span": self.evidence_span,
            "confidence": self.confidence,
            "tags": self.tags,
        }


@dataclass
class ExtractionResult:
    """Result of extracting facts from one chunk."""

    chunk_id: int
    facts: list[Fact] = field(default_factory=list)
    truncated: bool = False
    rounds_completed: int = 0
    error: str | None = None


def _incremental_json_parse(text: str) -> list[dict[str, Any]]:
    """Parse LLM JSON output with tolerance for truncation and malformation.

    Handles:
    - Valid JSON with "facts" key
    - Truncated JSON arrays (missing closing brackets)
    - JSON objects not enclosed in array
    - Markdown code-fenced JSON
    """
    if not text:
        return []

    # Strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n```$", "", text)

    # Try standard parse first
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "facts" in data:
            facts = data["facts"]
            if isinstance(facts, list):
                return [f for f in facts if isinstance(f, dict)]
        if isinstance(data, list):
            return [f for f in data if isinstance(f, dict)]
    except json.JSONDecodeError:
        pass

    # Try to extract individual JSON objects
    facts: list[dict[str, Any]] = []
    # Find all {...} blocks (brace-depth aware)
    depth = 0
    obj_start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                try:
                    obj = json.loads(text[obj_start : i + 1])
                    if isinstance(obj, dict):
                        facts.append(obj)
                except json.JSONDecodeError:
                    pass
                obj_start = -1

    if facts:
        return facts

    # Last resort: regex for "subject"/"predicate"/"object" patterns
    pattern = r'\{"[^"]*":\s*"[^"]*"(?:,\s*"[^"]*":\s*"[^"]*"|\s*,\s*"[^"]*":\s*\d+)*\}'
    for match in re.finditer(pattern, text):
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict):
                facts.append(obj)
        except json.JSONDecodeError:
            pass

    return facts


def _extract_one_round(
    client: LLMClient,
    chunk_text: str,
    system_prompt: str,
    round_num: int,
    max_tokens: int = 16384,
    extra_body: dict[str, Any] | None = None,
) -> tuple[list[Fact], bool]:
    """Run one extraction round with a seeded temperature variation.

    Returns (facts, is_truncated).
    """
    # Slightly different temperature per round for diversity
    temp = 0.3 + (round_num * 0.15)

    response = client.extract_facts(
        chunk_text=chunk_text,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temp,
        extra_body=extra_body,
    )

    facts_dicts = _incremental_json_parse(response.content)
    facts = [Fact.from_dict(d) for d in facts_dicts if isinstance(d, dict)]

    if response.is_truncated and not facts:
        # Try continuation — resend original document + partial response
        partial = response.content
        continuation = client.continue_truncated(
            partial, system_prompt,
            chunk_text=chunk_text,
            extra_body={"enable_thinking": False},
        )
        more_dicts = _incremental_json_parse(continuation.content)
        facts.extend(Fact.from_dict(d) for d in more_dicts if isinstance(d, dict))

        # If STILL no facts, try once more with explicit force-JSON prompt
        if not facts:
            logger.debug("Continuation also yielded no facts — trying force-JSON prompt")
            try:
                force_resp = client.chat(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": chunk_text},
                        {"role": "user", "content": (
                            "Now output ONLY the JSON object with the facts you found "
                            "in the document above. "
                            'Format: {"facts": [{"title": "...", "subject": "...", '
                            '"predicate": "...", "object": "...", "evidence_span": "...", '
                            '"confidence": 85, "description": "...", "tags": [...]}, ...]}'
                            "\nDo NOT include any reasoning or explanation — pure JSON only."
                        )},
                    ],
                    max_tokens=8192,
                    temperature=0.1,
                    extra_body={"enable_thinking": False},
                    response_format={"type": "json_object"},
                )
                force_dicts = _incremental_json_parse(force_resp.content)
                facts.extend(Fact.from_dict(d) for d in force_dicts if isinstance(d, dict))
            except Exception as e:
                logger.warning("Force-JSON retry failed: %s", e)

    return facts, response.is_truncated


def extract_facts(
    client: LLMClient,
    chunk_text: str,
    chunk_id: int = 0,
    rounds: int = 2,
    confidence_threshold: int = 30,
    extra_body: dict[str, Any] | None = None,
) -> ExtractionResult:
    """Extract facts from a text chunk using multi-round LLM extraction.

    Automatically routes Chinese text to extract_zh.txt prompt.

    Args:
        extra_body: Additional JSON fields for the LLM request body
                    (e.g. {"enable_thinking": False} for oMLX).
    """
    result = ExtractionResult(chunk_id=chunk_id)

    # Select prompt based on language detection
    if detect_chinese(chunk_text):
        prompt_name = "extract_zh.txt"
    else:
        prompt_name = "extract.txt"

    try:
        system_prompt = _load_prompt(prompt_name)
    except FileNotFoundError as e:
        result.error = str(e)
        return result

    all_facts: dict[str, Fact] = {}  # Dedup within chunk by (subj, pred, obj)
    truncated_any = False

    for r in range(rounds):
        try:
            facts, truncated = _extract_one_round(
                client, chunk_text, system_prompt, r,
                extra_body=extra_body,
            )
            if truncated:
                truncated_any = True
            for fact in facts:
                key = f"{fact.subject}|{fact.predicate}|{fact.object}"
                if key not in all_facts or fact.confidence > all_facts[key].confidence:
                    all_facts[key] = fact
            result.rounds_completed += 1
        except Exception as e:
            logger.warning("Extraction round %d failed for chunk %d: %s", r, chunk_id, e)
            # One round failure doesn't kill the chunk

    result.facts = [
        f for f in all_facts.values() if f.confidence >= confidence_threshold
    ]
    result.truncated = truncated_any

    return result


# ============================================================================
# DeepKE boundary filter (Chinese content guardrail)
# ============================================================================

# Extracted from the design doc §9.3 — DeepKE is a post-processing filter
# that validates entity boundaries for Chinese content. When LLM and DeepKE
# disagree on entity boundaries, confidence is reduced by 0.8 multiplier
# and the fact is flagged for entity_proposals review queue.

_deepke_loaded: bool = False
_deepke_model: Any = None


def _ensure_deepke(model_path: str) -> bool:
    """Lazy-load DeepKE model if available."""
    global _deepke_loaded, _deepke_model
    if _deepke_loaded:
        return _deepke_model is not None
    _deepke_loaded = True
    try:
        from deepke import DeepKE
        _deepke_model = DeepKE(model_path)
        logger.info("DeepKE model loaded from %s", model_path)
        return True
    except ImportError:
        logger.debug("DeepKE not installed, skipping NER filter")
        return False
    except Exception as e:
        logger.warning("DeepKE load failed: %s", e)
        return False


def apply_deepke_filter(
    facts: list[Fact],
    model_path: str | None = None,
) -> list[Fact]:
    """Apply DeepKE entity boundary validation to Chinese facts.

    Per DEVELOPMENT_V3.md §9.3: When LLM and DeepKE disagree on entity
    boundaries, reduce confidence and flag for review.

    Args:
        facts: Extracted facts to validate.
        model_path: Path to DeepKE model directory.

    Returns:
        Facts with adjusted confidence scores.
    """
    if model_path is None:
        model_path = "~/.filekb/models/deepke-cn"
    model_path = str(Path(model_path).expanduser())

    if not _ensure_deepke(model_path):
        return facts

    # DeepKE NER extraction + boundary comparison
    # For now, this is a stub — full integration requires the DeepKE
    # Python API which varies by version. The filter framework is in place.
    logger.debug("DeepKE filter: %d facts to validate", len(facts))

    for fact in facts:
        # Placeholder: actual DeepKE NER boundary comparison
        # If DeepKE entities don't match LLM entities:
        #   fact.confidence = int(fact.confidence * 0.8)
        #   fact.tags.append("ner_mismatch")
        pass

    return facts
