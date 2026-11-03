"""ML-ETL deduplication stages (cross-source, cross-time, fingerprint anomaly)."""

from __future__ import annotations

from app.data_engine.etl.deduplication.counterparty_matcher import CounterpartyMatcher
from app.data_engine.etl.deduplication.cross_source_positions import (
    CanonicalPositionRow,
    CrossSourceCoverage,
    CrossSourcePositionMatcher,
    CrossSourceResult,
)
from app.data_engine.etl.deduplication.fingerprint_detector import FingerprintAnomalyDetector
from app.data_engine.etl.deduplication.position_deduplicator import PositionDeduplicator

__all__ = [
    "CanonicalPositionRow",
    "CounterpartyMatcher",
    "CrossSourceCoverage",
    "CrossSourcePositionMatcher",
    "CrossSourceResult",
    "FingerprintAnomalyDetector",
    "PositionDeduplicator",
]
