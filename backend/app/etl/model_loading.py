"""Load persisted ML-ETL model artifacts for injection into ``run_etl``.

``run_etl`` is a pure function (no I/O), so model loading lives here: callers load
the trained artifacts once and inject them via :class:`~app.etl.pipeline.EtlConfig`.
When no artifact is present (fresh deploy, CI, before the first training run) the
loaders return ``None`` and the deduplicator / anomaly detector fall back to their
deterministic paths — the graceful-degradation contract on the model classes.

Loaded models are cached per process (``lru_cache``): the RandomForest / IsolationForest
are loaded from disk once, not per ingestion batch. After a retrain writes a new
artifact, call :func:`reset_cache` (the trainer does) or restart the process to pick
it up — the same load-once contract the cash-flow and behavioral models use.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.etl.models._mrm import ModelUnavailableError
from app.etl.models.anomaly_detection_model.model import AnomalyDetectionModel
from app.etl.models.counterparty_matching_model.model import CounterpartyMatchingModel

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_counterparty_model() -> CounterpartyMatchingModel | None:
    """The trained counterparty matcher if an artifact exists, else ``None``."""
    try:
        model = CounterpartyMatchingModel.load()
    except ModelUnavailableError:
        return None
    logger.info("Loaded trained counterparty matching model v%s", model.model_version)
    return model


@lru_cache(maxsize=1)
def load_anomaly_model() -> AnomalyDetectionModel | None:
    """The trained anomaly detector if an artifact exists, else ``None``."""
    try:
        model = AnomalyDetectionModel.load()
    except ModelUnavailableError:
        return None
    logger.info("Loaded trained anomaly detection model v%s", model.model_version)
    return model


def reset_cache() -> None:
    """Forget the loaded artifacts so the next call re-reads from disk (post-retrain)."""
    load_counterparty_model.cache_clear()
    load_anomaly_model.cache_clear()
