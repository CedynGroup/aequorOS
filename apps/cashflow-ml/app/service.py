"""Forecast service: owns the synthetic series, model artifacts, and responses."""

from __future__ import annotations

import datetime
import math
from typing import Literal

from app.baseline import forecast_static
from app.config import Settings, TrainingConfig
from app.model import CashFlowLSTM, forecast_net_flows, load_artifacts, train_and_save
from app.schemas import (
    ForecastAccuracy,
    ForecastPoint,
    ForecastResponse,
    HistoryPoint,
    HistoryResponse,
)
from app.synthetic import generate_daily_series

CONFIDENCE_Z = 1.96
BAND_WIDENING_BASE_DAYS = 7.0
BAND_WIDENING_CAP = 2.5


def _band_half_width(day: int, residual_std: float) -> float:
    """+/-1.96 sigma, widening by sqrt(day/7) clamped to [1.0, 2.5]."""
    widening = min(max(math.sqrt(day / BAND_WIDENING_BASE_DAYS), 1.0), BAND_WIDENING_CAP)
    return CONFIDENCE_Z * residual_std * widening


class ForecastService:
    """Lazily trains/loads the LSTM and serves forecast, history, and health data."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._config = TrainingConfig.from_settings(self._settings)
        self._series = generate_daily_series(days=self._config.total_days)
        self._model: CashFlowLSTM | None = None
        self._scaler: dict[str, float | int | str] | None = None
        self._metrics: dict[str, float | str] | None = None

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    @property
    def model_version(self) -> str | None:
        if self._metrics is None:
            return None
        return str(self._metrics["model_version"])

    def load_if_available(self) -> bool:
        """Load saved artifacts if they exist; never trains."""
        if self._model is not None:
            return True
        loaded = load_artifacts(self._settings.artifacts_dir)
        if loaded is None:
            return False
        self._model, self._scaler, self._metrics = loaded
        return True

    def train(self) -> dict[str, float | str]:
        """(Re)train from the synthetic series and reload the saved artifacts."""
        metrics = train_and_save(config=self._config, artifacts_dir=self._settings.artifacts_dir)
        self._model = None
        if not self.load_if_available():  # pragma: no cover - artifacts were just written
            raise RuntimeError("training completed but artifacts could not be loaded")
        return metrics

    def ensure_trained(self) -> None:
        if not self.load_if_available():
            self.train()

    def _require_trained(
        self,
    ) -> tuple[CashFlowLSTM, dict[str, float | int | str], dict[str, float | str]]:
        self.ensure_trained()
        if self._model is None or self._scaler is None or self._metrics is None:
            raise RuntimeError("model is not trained")  # pragma: no cover - guarded above
        return self._model, self._scaler, self._metrics

    def forecast(self, horizon: int, mode: Literal["lstm", "static"]) -> ForecastResponse:
        model, scaler, metrics = self._require_trained()
        as_of = self._series[-1].date
        accuracy = ForecastAccuracy(
            lstm_mape=float(metrics["lstm_mape"]),
            static_mape=float(metrics["static_mape"]),
            improvement_pct=float(metrics["improvement_pct"]),
        )

        points: list[ForecastPoint] = []
        if mode == "lstm":
            nets = forecast_net_flows(model, scaler, self._series, horizon)
            residual_std = float(metrics["residual_std"])
            for day, net in enumerate(nets, start=1):
                half_width = _band_half_width(day, residual_std)
                points.append(
                    ForecastPoint(
                        day=day,
                        date=as_of + datetime.timedelta(days=day),
                        net_flow=round(net, 4),
                        lower=round(net - half_width, 4),
                        upper=round(net + half_width, 4),
                    )
                )
        else:
            for day, net in enumerate(forecast_static(self._series, horizon), start=1):
                rounded = round(net, 4)
                points.append(
                    ForecastPoint(
                        day=day,
                        date=as_of + datetime.timedelta(days=day),
                        net_flow=rounded,
                        lower=rounded,
                        upper=rounded,
                    )
                )

        return ForecastResponse(
            mode=mode,
            horizon=horizon,
            as_of_date=as_of,
            model_version=str(metrics["model_version"]),
            accuracy=accuracy,
            points=points,
        )

    def history(self, days: int) -> HistoryResponse:
        trailing = self._series[-min(days, len(self._series)) :]
        return HistoryResponse(
            points=[HistoryPoint(date=flow.date, net_flow=round(flow.net, 4)) for flow in trailing]
        )
