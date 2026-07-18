"""Train and persist both ML-ETL models — the ``train → validate → persist`` step.

Run: ``python -m app.etl.models.train`` (needs ``scikit-learn``; reads the Sample Bank
tapes under ``<repo>/data/``). Trains the counterparty matcher and the anomaly detector,
validates each (held-out split / injected corruptions), writes governed joblib artifacts
to ``artifacts/etl_models/`` (untracked), and clears the loader cache so a same-process
``run_etl`` picks up the fresh models. A long-running server must restart (or call
``app.etl.model_loading.reset_cache``) to load newly written artifacts.

This closes the loop the ingestion pipeline expects: with an artifact present the matcher/
detector run the *trained* RandomForest/IsolationForest and stamp ``model_id``; without one
they fall back to the deterministic blend / per-batch fit.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.etl.model_loading import reset_cache
from app.etl.models.anomaly_detection_model import training as anomaly_training
from app.etl.models.counterparty_matching_model import training as counterparty_training


def train_and_persist() -> dict[str, dict[str, Any]]:
    """Train, validate, and persist both models; return a per-model summary."""
    counterparty = counterparty_training.train_and_validate()
    counterparty_path = counterparty.model.save()

    anomaly = anomaly_training.train_and_validate()
    anomaly_path = anomaly.model.save()

    reset_cache()  # so an in-process run_etl loads the artifacts just written

    return {
        "counterparty_matching_model": {
            "artifact": str(counterparty_path),
            "n_train": counterparty.n_train,
            "n_test": counterparty.n_test,
            "metrics": counterparty.metrics,
        },
        "anomaly_detection_model": {
            "artifact": str(anomaly_path),
            "n_train": anomaly.n_train,
            "injected_recall": anomaly.injected_recall,
            "clean_false_positive_rate": anomaly.clean_false_positive_rate,
        },
    }


def main() -> None:  # pragma: no cover - manual training entry point
    configure_logging(get_settings().logging.log_level)
    print(json.dumps(train_and_persist(), indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover - manual training entry point
    main()
