"""Versioned IsolationForest fingerprint-anomaly model (MRM-governed)."""

from __future__ import annotations

from app.etl.models.anomaly_detection_model.model import (
    MODEL_ID,
    MODEL_VERSION,
    OUTPUT_NAME,
    AnomalyDetectionModel,
    AnomalyScore,
)

__all__ = [
    "MODEL_ID",
    "MODEL_VERSION",
    "OUTPUT_NAME",
    "AnomalyDetectionModel",
    "AnomalyScore",
]
