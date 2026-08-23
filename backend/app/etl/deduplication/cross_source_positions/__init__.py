"""Row-level cross-source position matching (detection only; never resolution)."""

from __future__ import annotations

from app.etl.deduplication.cross_source_positions.matcher import (
    AMBIGUOUS_CONFIDENCE,
    ATTRIBUTE_CONFIDENCE,
    ATTRIBUTE_UNSTATED_MATURITY_CONFIDENCE,
    MATCH_ATTRIBUTE_FINGERPRINT,
    MATCH_SHARED_REFERENCE,
    SHARED_REFERENCE_CONFIDENCE,
    CanonicalPositionRow,
    CrossSourceCoverage,
    CrossSourcePositionMatcher,
    CrossSourceResult,
)

__all__ = [
    "AMBIGUOUS_CONFIDENCE",
    "ATTRIBUTE_CONFIDENCE",
    "ATTRIBUTE_UNSTATED_MATURITY_CONFIDENCE",
    "MATCH_ATTRIBUTE_FINGERPRINT",
    "MATCH_SHARED_REFERENCE",
    "SHARED_REFERENCE_CONFIDENCE",
    "CanonicalPositionRow",
    "CrossSourceCoverage",
    "CrossSourcePositionMatcher",
    "CrossSourceResult",
]
