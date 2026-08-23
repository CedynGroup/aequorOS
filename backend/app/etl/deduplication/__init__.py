"""ML-ETL deduplication stages (cross-source, cross-time, fingerprint anomaly)."""

from __future__ import annotations

from app.etl.deduplication.counterparty_matcher import CounterpartyMatcher
from app.etl.deduplication.cross_source_positions import (
    CanonicalPositionRow,
    CrossSourceCoverage,
    CrossSourcePositionMatcher,
    CrossSourceResult,
)
from app.etl.deduplication.fingerprint_detector import FingerprintAnomalyDetector
from app.etl.deduplication.position_deduplicator import PositionDeduplicator

__all__ = [
    "CanonicalPositionRow",
    "CounterpartyMatcher",
    "CrossSourceCoverage",
    "CrossSourcePositionMatcher",
    "CrossSourceResult",
    "FingerprintAnomalyDetector",
    "PositionDeduplicator",
]
