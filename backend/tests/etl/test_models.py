"""MRM-discipline tests for the versioned sklearn model wrappers."""

from __future__ import annotations

import numpy as np
import pytest

from app.etl.deduplication.fingerprint_detector.fingerprint import FINGERPRINT_FEATURES
from app.etl.models import (
    AnomalyDetectionModel,
    CounterpartyMatchingModel,
    HumanOverride,
    ModelUnavailableError,
)
from app.etl.models.counterparty_matching_model import SIGNAL_FEATURES


# --- counterparty matching model ------------------------------------------------
def _match_signals(strength: float) -> dict[str, float]:
    """A signal vector uniformly at ``strength`` across the contract."""
    return {name: strength for name in SIGNAL_FEATURES}


def test_unfitted_model_uses_heuristic_and_caps_certainty() -> None:
    model = CounterpartyMatchingModel()
    assert model.is_fitted is False
    pred = model.predict(_match_signals(1.0))
    assert pred.method == "heuristic_blend"
    assert pred.probability == pytest.approx(1.0)
    assert pred.certainty <= 0.85  # fallback certainty is capped


def test_fit_produces_forest_backed_predictions_and_card() -> None:
    x = [_match_signals(0.95) for _ in range(6)] + [_match_signals(0.05) for _ in range(6)]
    y = [1] * 6 + [0] * 6
    model = CounterpartyMatchingModel().fit(x, y, training_data_ref="unit-fixture")

    assert model.is_fitted is True
    assert model.card is not None
    assert model.card.feature_names == SIGNAL_FEATURES
    assert model.card.training_rows == 12
    assert model.card.training_data_ref == "unit-fixture"

    pred = model.predict(_match_signals(0.95))
    assert pred.method == "random_forest"
    assert pred.probability > 0.5
    assert 0.0 <= pred.certainty <= 1.0


def test_human_override_supersedes_model_output() -> None:
    model = CounterpartyMatchingModel()
    model.set_override(
        "pair-1", HumanOverride(decided_by="reviewer", decision=True, reason="confirmed")
    )
    pred = model.predict(_match_signals(0.0), key="pair-1")
    assert pred.method == "human_override"
    assert pred.probability == 1.0
    assert pred.certainty == 1.0
    assert pred.override is not None and pred.override.decided_by == "reviewer"


def test_matching_model_artifact_roundtrip(tmp_path) -> None:  # noqa: ANN001 - pytest fixture
    x = [_match_signals(0.95) for _ in range(6)] + [_match_signals(0.05) for _ in range(6)]
    model = CounterpartyMatchingModel().fit(x, [1] * 6 + [0] * 6)
    path = tmp_path / "matcher.joblib"
    model.save(path)

    reloaded = CounterpartyMatchingModel.load(path)
    assert reloaded.is_fitted
    assert reloaded.card is not None
    assert reloaded.model_version == model.model_version
    p_new = reloaded.predict(_match_signals(0.95)).probability
    p_old = model.predict(_match_signals(0.95)).probability
    assert p_new == pytest.approx(p_old)


def test_refusing_to_persist_unfitted_matching_model(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(ModelUnavailableError):
        CounterpartyMatchingModel().save(tmp_path / "nope.joblib")


# --- anomaly detection model ----------------------------------------------------
def _normal_matrix(rng: np.random.Generator, n: int) -> np.ndarray:
    return rng.normal(0.0, 1.0, size=(n, len(FINGERPRINT_FEATURES)))


def test_anomaly_fallback_flags_extreme_record() -> None:
    model = AnomalyDetectionModel()
    assert model.is_fitted is False
    rng = np.random.default_rng(20260521)
    baseline = _normal_matrix(rng, 40)
    model.fit_fallback(baseline, feature_names=FINGERPRINT_FEATURES)

    outlier = np.full((1, len(FINGERPRINT_FEATURES)), 50.0)
    [score] = model.score(outlier)
    assert score.method == "mad_zscore"
    assert score.is_anomaly is True
    assert 0.0 <= score.score <= 1.0


def test_anomaly_forest_fit_scores_and_card() -> None:
    rng = np.random.default_rng(7)
    matrix = _normal_matrix(rng, 60)
    model = AnomalyDetectionModel().fit(
        matrix, feature_names=FINGERPRINT_FEATURES, training_data_ref="unit-fixture"
    )
    assert model.is_fitted is True
    assert model.card is not None
    assert model.card.feature_names == FINGERPRINT_FEATURES

    scores = model.score(matrix)
    assert len(scores) == 60
    assert all(s.method == "isolation_forest" for s in scores)
    assert all(0.0 <= s.score <= 1.0 for s in scores)


def test_anomaly_override_and_roundtrip(tmp_path) -> None:  # noqa: ANN001
    rng = np.random.default_rng(11)
    matrix = _normal_matrix(rng, 50)
    model = AnomalyDetectionModel().fit(matrix, feature_names=FINGERPRINT_FEATURES)
    model.set_override(
        "rec-1", HumanOverride(decided_by="reviewer", decision=True, reason="known bad")
    )
    [scored] = model.score(matrix[:1], keys=["rec-1"])
    assert scored.method == "human_override"
    assert scored.is_anomaly is True

    path = tmp_path / "anomaly.joblib"
    model.save(path)
    reloaded = AnomalyDetectionModel.load(path)
    assert reloaded.is_fitted
    assert reloaded.feature_names == FINGERPRINT_FEATURES
