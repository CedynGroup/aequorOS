from __future__ import annotations

import math

from app.core.config import CashflowSettings
from app.ml.config import TrainingConfig
from app.ml.model import forecast_net_flows, load_artifacts, train_and_save
from app.ml.synthetic import generate_daily_series


def test_training_beats_static_baseline_and_saves_artifacts(tmp_path):
    settings = CashflowSettings()
    assert settings.fast_test, "tests must run with CASHFLOW_FAST_TEST=1"
    config = TrainingConfig.from_settings(settings)

    metrics = train_and_save(config=config, artifacts_dir=tmp_path)

    assert float(metrics["lstm_mape"]) < float(metrics["static_mape"])
    assert float(metrics["improvement_pct"]) > 0
    assert metrics["model_version"] == "lstm-v1.0.0"
    for name in ("model.pt", "scaler.json", "metrics.json"):
        assert (tmp_path / name).exists()

    loaded = load_artifacts(tmp_path)
    assert loaded is not None
    model, scaler, saved_metrics = loaded
    assert saved_metrics["lstm_mape"] == metrics["lstm_mape"]
    assert int(scaler["window"]) == config.window

    series = generate_daily_series(days=config.total_days)
    forecast = forecast_net_flows(model, scaler, series, horizon=30)
    assert len(forecast) == 30
    assert all(math.isfinite(value) for value in forecast)


def test_load_artifacts_returns_none_when_missing(tmp_path):
    assert load_artifacts(tmp_path / "does-not-exist") is None
