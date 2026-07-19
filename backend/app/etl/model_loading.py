"""Load **per-tenant** ML-ETL model artifacts for injection into ``run_etl``.

Governance (no cross-tenant spillover): a bank's counterparty / anomaly model is
trained on that bank's own canonical data and persisted under
``artifacts/etl_models/{org}/{bank}/``. These loaders only ever read that bank's
directory — a model trained for one bank is never loaded for another. When a bank
has no trained model yet (cold start, or too little data), the loader returns
``None`` and the deduplicator / anomaly detector fall back to their deterministic
path (heuristic blend / per-batch unsupervised fit) — which itself only ever sees
the batch being ingested, so the fallback is per-tenant too.

``run_etl`` stays a pure function: loading (disk I/O) happens here and the trained
model is injected via ``EtlConfig``. Loaded models are cached per ``(org, bank)``;
after a retrain writes a new artifact, call :func:`reset_cache` (the trainer does)
or restart the process.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from uuid import UUID

from app.etl.models._mrm import DEFAULT_ARTIFACT_DIR, ModelUnavailableError
from app.etl.models.anomaly_detection_model.model import (
    MODEL_ID as ANOMALY_MODEL_ID,
)
from app.etl.models.anomaly_detection_model.model import (
    MODEL_VERSION as ANOMALY_MODEL_VERSION,
)
from app.etl.models.anomaly_detection_model.model import (
    AnomalyDetectionModel,
)
from app.etl.models.counterparty_matching_model.model import (
    MODEL_ID as CP_MODEL_ID,
)
from app.etl.models.counterparty_matching_model.model import (
    MODEL_VERSION as CP_MODEL_VERSION,
)
from app.etl.models.counterparty_matching_model.model import (
    CounterpartyMatchingModel,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cp_cache: dict[tuple[UUID, UUID], CounterpartyMatchingModel | None] = {}
_anomaly_cache: dict[tuple[UUID, UUID], AnomalyDetectionModel | None] = {}


def tenant_artifact_dir(org_id: UUID, bank_id: UUID) -> Path:
    """Per-tenant artifact directory. One bank's models never sit in another's dir."""
    return DEFAULT_ARTIFACT_DIR / str(org_id) / str(bank_id)


def counterparty_artifact_path(org_id: UUID, bank_id: UUID) -> Path:
    return tenant_artifact_dir(org_id, bank_id) / f"{CP_MODEL_ID}-{CP_MODEL_VERSION}.joblib"


def anomaly_artifact_path(org_id: UUID, bank_id: UUID) -> Path:
    name = f"{ANOMALY_MODEL_ID}-{ANOMALY_MODEL_VERSION}.joblib"
    return tenant_artifact_dir(org_id, bank_id) / name


def load_counterparty_model(
    org_id: UUID, bank_id: UUID
) -> CounterpartyMatchingModel | None:
    """This bank's trained counterparty matcher, or ``None`` (→ heuristic fallback)."""
    key = (org_id, bank_id)
    with _lock:
        if key in _cp_cache:
            return _cp_cache[key]
    try:
        model: CounterpartyMatchingModel | None = CounterpartyMatchingModel.load(
            counterparty_artifact_path(org_id, bank_id)
        )
    except ModelUnavailableError:
        model = None
    with _lock:
        _cp_cache[key] = model
    if model is not None:
        logger.info("Loaded per-tenant counterparty matching model for bank %s", bank_id)
    return model


def load_anomaly_model(org_id: UUID, bank_id: UUID) -> AnomalyDetectionModel | None:
    """This bank's trained anomaly detector, or ``None`` (→ per-batch fallback)."""
    key = (org_id, bank_id)
    with _lock:
        if key in _anomaly_cache:
            return _anomaly_cache[key]
    try:
        model: AnomalyDetectionModel | None = AnomalyDetectionModel.load(
            anomaly_artifact_path(org_id, bank_id)
        )
    except ModelUnavailableError:
        model = None
    with _lock:
        _anomaly_cache[key] = model
    if model is not None:
        logger.info("Loaded per-tenant anomaly detection model for bank %s", bank_id)
    return model


def reset_cache() -> None:
    """Forget cached per-tenant models so the next load re-reads (post-retrain)."""
    with _lock:
        _cp_cache.clear()
        _anomaly_cache.clear()
