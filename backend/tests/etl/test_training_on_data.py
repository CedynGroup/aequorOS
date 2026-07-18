"""Integration: train + validate both ML-ETL models on Sample Bank Limited data.

Skipped when ``data/`` is not present (keeps the default hermetic suite green); when the
untracked Sample Bank sources are available locally these prove the models train on the
real feature contract and clear sane validation floors.
"""

from __future__ import annotations

import pytest

from app.etl.models.anomaly_detection_model.training import (
    DEFAULT_LOANS_CSV,
)
from app.etl.models.anomaly_detection_model.training import (
    train_and_validate as train_anomaly,
)
from app.etl.models.counterparty_matching_model.training import (
    DEFAULT_COUNTERPARTY_CSV,
)
from app.etl.models.counterparty_matching_model.training import (
    train_and_validate as train_matcher,
)

pytestmark = pytest.mark.skipif(
    not DEFAULT_COUNTERPARTY_CSV.exists() or not DEFAULT_LOANS_CSV.exists(),
    reason="Sample Bank Limited data/ sources not present (untracked).",
)


def test_matcher_trains_and_validates_on_real_counterparties() -> None:
    report = train_matcher(max_positives=250)
    assert report.n_train > 0
    assert report.n_test > 0
    assert report.model.is_fitted
    assert report.model.card is not None
    assert report.model.card.training_data_ref == str(DEFAULT_COUNTERPARTY_CSV)
    # Same-entity variants are easy positives; the model should recover most of them
    # while keeping precision high on the distinct-entity negatives.
    assert report.metrics["recall"] >= 0.8
    assert report.metrics["precision"] >= 0.8


def test_anomaly_model_trains_and_detects_injected_corruptions() -> None:
    report = train_anomaly(train_limit=1500, n_injected=80)
    assert report.n_train > 0
    assert report.model.is_fitted
    # Injected structural corruptions should be flagged far more often than clean rows.
    assert report.injected_recall >= 0.6
    assert report.clean_false_positive_rate <= 0.2
    assert report.injected_recall > report.clean_false_positive_rate
