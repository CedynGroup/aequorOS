"""AI logging with an allow-listed field set.

The fact sheet, the prompt, the model's output and the open questions are the
four things this feature exists to keep inside the platform's boundary, so they
are the four things that must never appear in a log line. An allow-list rather
than a deny-list, because a deny-list is one careless ``**kwargs`` away from
leaking, and the failure is silent.

An unknown field raises. That is a programming error caught in the test that
plants a sentinel, not a runtime state anyone should handle.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("app.ai")

#: Everything a log line may carry. Codes, digests, ids, counts and token
#: numbers — nothing that reconstructs content.
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "feature",
        "suggestion_id",
        "organization_id",
        "bank_id",
        "cycle_id",
        "section_key",
        "status",
        "outcome",
        "failure_code",
        "validation_error_codes",
        "refusal_category",
        "stop_reason",
        "prompt_version",
        "prompt_sha256",
        "fact_sheet_sha256",
        "fact_sheet_mode",
        "fact_count",
        "model_requested",
        "model_served",
        "fallback_used",
        "request_id",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "inference_geo",
        "latency_ms",
        "gate_code",
        "decision",
    }
)

EVENT_ENQUEUED = "ai.request.enqueued"
EVENT_GATED = "ai.request.gated"
EVENT_COMPLETED = "ai.call.completed"
EVENT_CACHE_MISS = "ai.call.cache_miss"
EVENT_DECIDED = "ai.suggestion.decided"


def log_ai_event(event: str, **fields: Any) -> None:
    """Log one AI event. Raises on any field outside the allow-list."""
    unknown = sorted(set(fields) - ALLOWED_FIELDS)
    if unknown:
        message = f"log_ai_event received fields outside the allow-list: {unknown}"
        raise ValueError(message)
    logger.info(event, extra={"ai_event": event, **fields})


def log_cache_miss_if_cold(*, cache_read_input_tokens: int | None, **fields: Any) -> None:
    """Warn when a request whose prefix should have been cached read nothing.

    A zero cache read on a request built from a byte-stable system prompt means
    a silent invalidator got in — a stray timestamp, an unsorted key, a reordered
    block. It costs money quietly, so it is worth a WARN rather than a metric
    nobody reads.
    """
    if cache_read_input_tokens == 0:
        unknown = sorted(set(fields) - ALLOWED_FIELDS)
        if unknown:
            message = f"log_ai_event received fields outside the allow-list: {unknown}"
            raise ValueError(message)
        logger.warning(
            EVENT_CACHE_MISS,
            extra={"ai_event": EVENT_CACHE_MISS, "cache_read_input_tokens": 0, **fields},
        )


__all__ = [
    "ALLOWED_FIELDS",
    "EVENT_CACHE_MISS",
    "EVENT_COMPLETED",
    "EVENT_DECIDED",
    "EVENT_ENQUEUED",
    "EVENT_GATED",
    "log_ai_event",
    "log_cache_miss_if_cold",
]
