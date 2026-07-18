"""IsolationForest fingerprint anomaly detector (flags, never modifies)."""

from __future__ import annotations

from app.etl.deduplication.fingerprint_detector.detector import FingerprintAnomalyDetector
from app.etl.deduplication.fingerprint_detector.fingerprint import (
    FINGERPRINT_FEATURES,
    fingerprint,
    fingerprint_matrix,
)

__all__ = [
    "FINGERPRINT_FEATURES",
    "FingerprintAnomalyDetector",
    "fingerprint",
    "fingerprint_matrix",
]
