"""LLM client — OpenAI-compatible HTTP interface with resilience.

Uses tenacity for retry with exponential backoff + jitter.
Detects finish_reason == 'length' for overflow recovery.
Provides adaptive_resplit for context-overflow chunks.

Per ADR-10: No pre-flight token counting. Overflow detection is
purely reactive via the API's finish_reason field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

# Error classes per resilience taxonomy (DEVELOPMENT_V3.md §10.1)
ERROR_CONTEXT_LENGTH = "CONTEXT_LENGTH_EXCEEDED"
ERROR_RATE_LIMIT = "RATE_LIMIT"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_INVALID_JSON = "INVALID_JSON"
ERROR_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
ERROR_UNKNOWN = "UNKNOWN"


@dataclass
class LLMResponse:
    """Structured result from an LLM call."""

    content: str
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def is_truncated(self) -> bool:
        """True if the response was cut off (context overflow)."""
        return self.finish_reason == "length"


def classify_http_error(status_code: int) -> str:
    """Map HTTP status codes to error classes."""
    if status_code == 429:
        return ERROR_RATE_LIMIT
    elif status_code in (502, 503, 504) or status_code >= 500:
        return ERROR_MODEL_UNAVAILABLE
    return ERROR_UNKNOWN


def _is_retryable(exception: Exception) -> bool:
    """Determine if an exception warrants a retry."""
    if isinstance(exception, httpx.TimeoutException):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        status = exception.response.status_code
        return status in (429, 502, 503, 504)
    if isinstance(exception, (httpx.ConnectError, httpx.RemoteProtocolError)):
        return True
    return False


class LLMClient:
    """OpenAI-compatible HTTP client for local LLM inference.

    Wraps httpx with tenacity retry logic. Handles finish_reason
    detection for overflow recovery.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        model: str = "Qwen3.6-35B-A3B-MTP",
        api_key: str = "not-needed",
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.3,
        stream: bool = False,
        extra_body: dict[str, Any] | None = None,
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        """Send a chat completion request with automatic retry.

        Args:
            messages: List of {"role": "...", "content": "..."} dicts.
            max_tokens: Maximum tokens in the response.
            temperature: Sampling temperature.
            stream: Whether to stream the response.
            extra_body: Additional JSON fields to merge into the request body
                        (e.g. {"enable_thinking": False} for oMLX).
            response_format: Optional response_format dict (e.g. {"type": "json_object"}).

        Returns:
            LLMResponse with content and metadata.
        """
        return self._chat_with_retry(messages, max_tokens, temperature, stream, extra_body, response_format)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=30, jitter=2),
        retry=retry_if_exception(_is_retryable),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _chat_with_retry(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        stream: bool,
        extra_body: dict[str, Any] | None = None,
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        """Internal method with tenacity retry decorator."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if extra_body:
            payload.update(extra_body)
        if response_format:
            payload["response_format"] = response_format

        response = self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        finish_reason = choice.get("finish_reason")

        return LLMResponse(
            content=content,
            finish_reason=finish_reason,
            usage=data.get("usage", {}),
            model=data.get("model", self.model),
            raw_response=data,
        )

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ):
        """Stream chat tokens via Server-Sent Events from oMLX.

        Yields (token_text, finish_reason_or_None) tuples.
        The last tuple has finish_reason set.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        with self._client.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            finish_reason = None

            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]  # Strip "data: " prefix
                if data_str == "[DONE]":
                    yield ("", "stop")
                    return

                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        finish_reason = choices[0].get("finish_reason")
                        if content:
                            yield (content, finish_reason)
                        if finish_reason:
                            return
                except json.JSONDecodeError:
                    continue

            yield ("", finish_reason or "stop")

    # ------------------------------------------------------------------
    # Extraction-specific
    # ------------------------------------------------------------------

    def extract_facts(
        self,
        chunk_text: str,
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Extract facts from a text chunk.

        Args:
            chunk_text: The chunk content to extract facts from.
            system_prompt: The extraction prompt (system message).
            max_tokens: Max output tokens.
            temperature: Sampling temperature.
            extra_body: Additional JSON fields for the request body.

        Returns:
            LLMResponse — check is_truncated to detect overflow.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": chunk_text},
        ]
        return self.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
            response_format={"type": "json_object"},
        )

    def generate_answer(
        self,
        question: str,
        context: str,
        system_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> LLMResponse:
        """Generate a sourced answer from retrieved context.

        Args:
            question: The user's question.
            context: Retrieved context chunks with source citations.
            system_prompt: Answer generation prompt.
            max_tokens: Max output tokens.
            temperature: Sampling temperature.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ]
        return self.chat(messages, max_tokens=max_tokens, temperature=temperature)

    def continue_truncated(
        self,
        partial_json: str,
        system_prompt: str,
        chunk_text: str = "",
        max_tokens: int = 2048,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Continue generation after a truncated JSON response.

        Used when finish_reason == 'length'. Includes the original document
        text so the LLM has full context to continue extraction.
        """
        if chunk_text:
            # Full recovery: re-send original document + partial response,
            # ask LLM to continue from where it left off
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": chunk_text},
                {"role": "assistant", "content": partial_json},
                {"role": "user", "content": (
                    "Your response above was cut off. Continue outputting "
                    "the REMAINING JSON facts only — do NOT repeat facts you "
                    "already output above. No reasoning, no explanation, "
                    "just the remaining facts in the same JSON format."
                )},
            ]
        else:
            # Fallback (no chunk text available): generic continuation
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    "Your previous response was cut off before you could output "
                    "the JSON facts. Output ONLY the JSON object now — no reasoning, "
                    "no explanation, just pure JSON with the facts you identified.\n\n"
                    'Required format: {"facts": [{"title": "...", "subject": "...", '
                    '"predicate": "...", "object": "...", "evidence_span": "...", '
                    '"confidence": <0-100>, "description": "...", "tags": [...]}, ...]}'
                )},
            ]
        return self.chat(
            messages,
            max_tokens=max_tokens,
            temperature=0.3,
            extra_body=extra_body,
            response_format={"type": "json_object"},
        )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> dict[str, Any]:
        """Check if the LLM server is reachable."""
        try:
            response = self._client.get("/models")
            return {"status": "ok", "models": response.json()}
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}

    def close(self) -> None:
        self._client.close()


# ============================================================================
# Adaptive resplit
# ============================================================================


def adaptive_resplit(chunk_text: str, depth: int = 0, max_depth: int = 3) -> list[str]:
    """Recursively split an oversized chunk into smaller sub-chunks.

    This is called when finish_reason == 'length' indicates a context
    overflow. Splits the text in half at each level, up to max_depth.
    Beyond max_depth, returns hard-truncated chunks.

    Note: Does NOT use any tokenizer — the structural guarantee of
    max_chars_per_chunk=24000 ensures the splitter's output fits
    within Qwen3.6's 32K context window in normal operation.

    Args:
        chunk_text: The text that caused an overflow.
        depth: Current recursion depth.
        max_depth: Maximum recursion depth (default 3).

    Returns:
        List of smaller text chunks.
    """
    if depth >= max_depth:
        # Hard split — shouldn't normally be reached
        logger.warning("adaptive_resplit: max depth %d reached, hard-splitting", max_depth)
        mid = len(chunk_text) // 2
        return [chunk_text[:mid], chunk_text[mid:]]

    # Split in half at a natural boundary
    mid = len(chunk_text) // 2
    # Try paragraph boundary first
    para_break = chunk_text.rfind("\n\n", 0, mid)
    if para_break > 0 and para_break > mid // 2:
        mid = para_break + 2
    else:
        # Try line break
        line_break = chunk_text.rfind("\n", 0, mid)
        if line_break > 0 and line_break > mid // 2:
            mid = line_break + 1
        else:
            # Try sentence boundary
            for sep in [". ", "。", "! ", "？", "? "]:
                pos = chunk_text.rfind(sep, 0, mid)
                if pos > 0 and pos > mid // 2:
                    mid = pos + len(sep)
                    break

    part1 = chunk_text[:mid].strip()
    part2 = chunk_text[mid:].strip()

    result: list[str] = []
    for part in (part1, part2):
        if not part:
            continue
        # We rely on the LLM to tell us if this sub-chunk still overflows
        result.append(part)

    return result
