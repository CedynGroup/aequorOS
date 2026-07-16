"""Tenant-scoped, in-process cash-flow forecasting (LSTM + static baseline).

Formerly a proxy to the standalone cashflow-ml sidecar; the ML code now lives
in ``app.ml`` and runs inside this service. This module still owns tenant
authorization (bank ownership) and shapes the ML outputs into the typed
contracts in ``app.schemas.cashflow_forecast`` consumed by the generated
OpenAPI client — the HTTP contract is unchanged.

The torch-backed model module is imported lazily on first forecast so app
startup and non-forecast requests never pay for the ML runtime. If that
import fails (broken torch install, missing native libraries) the forecast
endpoints degrade to the same 503 the old proxy returned when the sidecar was
down, instead of taking the whole service down. History needs only the
synthetic series and works without torch.
"""

from __future__ import annotations

import datetime
import math
import threading
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import CashflowSettings, get_settings
from app.ml.baseline import forecast_static
from app.ml.config import TrainingConfig
from app.ml.real_series import load_real_daily_series
from app.ml.synthetic import DailyFlow, generate_daily_series
from app.models import Bank
from app.schemas.cashflow_forecast import (
    CashflowForecastAccuracyRead,
    CashflowForecastMode,
    CashflowForecastPointRead,
    CashflowForecastRead,
    CashflowHistoryPointRead,
    CashflowHistoryRead,
)


def _load_series(config: TrainingConfig) -> list[DailyFlow]:
    """Serving series: the real 10-year panel when enabled, else synthetic.

    Falls back to the synthetic series if the real panel is missing so the
    forecast endpoint never fails to construct just because the parquet is
    absent (a missing ML runtime already degrades to a 503 elsewhere).
    """
    if config.use_real_series:
        try:
            return load_real_daily_series()
        except FileNotFoundError:
            pass
    return generate_daily_series(days=config.total_days)

if TYPE_CHECKING:
    from types import ModuleType

    from app.ml.model import CashFlowLSTM

ML_UNAVAILABLE_DETAIL = "Cash flow forecasting service is unavailable."

CONFIDENCE_Z = 1.96
BAND_WIDENING_BASE_DAYS = 7.0
BAND_WIDENING_CAP = 2.5


def get_forecast(
    db: Session, ctx: TenantContext, bank_id: UUID, *, horizon: int, mode: CashflowForecastMode
) -> CashflowForecastRead:
    _get_bank_or_404(db, ctx, bank_id)
    return _get_service().forecast(horizon=horizon, mode=mode)


def get_history(
    db: Session, ctx: TenantContext, bank_id: UUID, *, days: int
) -> CashflowHistoryRead:
    _get_bank_or_404(db, ctx, bank_id)
    return _get_service().history(days)


def _import_ml_model() -> ModuleType:
    """Deferred import of the torch-backed model module (see module docstring)."""
    from app.ml import model  # noqa: PLC0415 - deliberate lazy torch import

    return model


def _ml_model_or_503() -> ModuleType:
    try:
        return _import_ml_model()
    except (ImportError, OSError) as exc:  # torch or its native libraries failed to load
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ML_UNAVAILABLE_DETAIL,
        ) from exc


def _band_half_width(day: int, residual_std: float) -> float:
    """+/-1.96 sigma, widening by sqrt(day/7) clamped to [1.0, 2.5]."""
    widening = min(max(math.sqrt(day / BAND_WIDENING_BASE_DAYS), 1.0), BAND_WIDENING_CAP)
    return CONFIDENCE_Z * residual_std * widening


class ForecastService:
    """Lazily trains/loads the LSTM and serves forecast and history responses."""

    def __init__(self, settings: CashflowSettings) -> None:
        self._settings = settings
        self._config = TrainingConfig.from_settings(settings)
        self._series = _load_series(self._config)
        self._model: CashFlowLSTM | None = None
        self._scaler: dict[str, float | int | str] | None = None
        self._metrics: dict[str, float | str] | None = None
        self._lock = threading.Lock()

    def _require_trained(
        self,
    ) -> tuple[CashFlowLSTM, dict[str, float | int | str], dict[str, float | str]]:
        """Load saved artifacts or train on first use; single-flight under a lock."""
        ml = _ml_model_or_503()
        with self._lock:
            model, scaler, metrics = self._model, self._scaler, self._metrics
            if model is None or scaler is None or metrics is None:
                loaded = ml.load_artifacts(self._settings.artifacts_dir)
                if loaded is None:
                    ml.train_and_save(
                        config=self._config, artifacts_dir=self._settings.artifacts_dir
                    )
                    loaded = ml.load_artifacts(self._settings.artifacts_dir)
                if loaded is None:  # pragma: no cover - artifacts were just written
                    raise RuntimeError("training completed but artifacts could not be loaded")
                model, scaler, metrics = loaded
                self._model, self._scaler, self._metrics = loaded
            return model, scaler, metrics

    def forecast(self, *, horizon: int, mode: Literal["lstm", "static"]) -> CashflowForecastRead:
        model, scaler, metrics = self._require_trained()
        ml = _ml_model_or_503()
        as_of = self._series[-1].date
        accuracy = CashflowForecastAccuracyRead(
            lstm_mape=float(metrics["lstm_mape"]),
            static_mape=float(metrics["static_mape"]),
            improvement_pct=float(metrics["improvement_pct"]),
        )

        points: list[CashflowForecastPointRead] = []
        if mode == "lstm":
            nets = ml.forecast_net_flows(model, scaler, self._series, horizon)
            residual_std = float(metrics["residual_std"])
            for day, net in enumerate(nets, start=1):
                half_width = _band_half_width(day, residual_std)
                points.append(
                    CashflowForecastPointRead(
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
                    CashflowForecastPointRead(
                        day=day,
                        date=as_of + datetime.timedelta(days=day),
                        net_flow=rounded,
                        lower=rounded,
                        upper=rounded,
                    )
                )

        return CashflowForecastRead(
            mode=mode,
            horizon=horizon,
            as_of_date=as_of,
            model_version=str(metrics["model_version"]),
            accuracy=accuracy,
            points=points,
        )

    def history(self, days: int) -> CashflowHistoryRead:
        trailing = self._series[-min(days, len(self._series)) :]
        return CashflowHistoryRead(
            points=[
                CashflowHistoryPointRead(date=flow.date, net_flow=round(flow.net, 4))
                for flow in trailing
            ]
        )


_service: ForecastService | None = None
_service_lock = threading.Lock()


def _get_service() -> ForecastService:
    """Process-wide singleton keeps the loaded model warm across requests."""
    global _service  # noqa: PLW0603 - deliberate module-level cache
    with _service_lock:
        if _service is None:
            _service = ForecastService(get_settings().cashflow)
        return _service


def reset_forecast_service() -> None:
    """Testing hook: drop the cached service so fresh settings take effect."""
    global _service  # noqa: PLW0603 - deliberate module-level cache
    with _service_lock:
        _service = None


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
