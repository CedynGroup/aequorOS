"""Per-tenant cash-flow forecasting (LSTM + static baseline), no spillover.

Keyed by ``(org, bank)`` like the behavioral models: each bank gets its own
forecast service, trained on its OWN ingested daily cash-flow history
(``historical_cashflows``) and persisted to a per-tenant artifact dir. A bank's
data never trains or serves another bank's model.

Cold start (ai_engine.md §12): a bank without enough daily history to train an
LSTM (``< TrainingConfig.total_days`` days) is served the shared **generic**
model — trained on the synthetic / Sample-Bank bootstrap series, never on another
real bank's data — and the response is labelled ``model_scope="generic"`` so it is
never mistaken for a bank-specific one. Once the bank has enough history, its
service trains ``model_scope="bank_specific"`` on its own series.

The torch-backed model module is imported lazily on first forecast; a broken ML
runtime degrades to a 503 rather than taking the service down.
"""

from __future__ import annotations

import datetime
import math
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import CashflowSettings, get_settings
from app.ml.baseline import forecast_static
from app.ml.cashflow_history import load_bank_daily_series
from app.ml.config import TrainingConfig
from app.ml.real_series import load_real_daily_series
from app.ml.synthetic import DailyFlow, generate_daily_series
from app.models import Bank
from app.schemas.cashflow_forecast import (
    CashflowForecastAccuracyRead,
    CashflowForecastMode,
    CashflowForecastModelScope,
    CashflowForecastPointRead,
    CashflowForecastRead,
    CashflowHistoryPointRead,
    CashflowHistoryRead,
)

if TYPE_CHECKING:
    from types import ModuleType

    from app.ml.model import CashFlowLSTM

ML_UNAVAILABLE_DETAIL = "Cash flow forecasting service is unavailable."

CONFIDENCE_Z = 1.96
BAND_WIDENING_BASE_DAYS = 7.0
BAND_WIDENING_CAP = 2.5


def _generic_series(config: TrainingConfig) -> list[DailyFlow]:
    """The generic bootstrap series: the real 10-year panel when enabled, else synthetic.

    Used only for banks without enough of their own history. Falls back to synthetic
    if the panel is absent so serving never fails just because the parquet is missing.
    """
    if config.use_real_series:
        try:
            return load_real_daily_series()
        except FileNotFoundError:
            pass
    return generate_daily_series(days=config.total_days)


def get_forecast(
    db: Session, ctx: TenantContext, bank_id: UUID, *, horizon: int, mode: CashflowForecastMode
) -> CashflowForecastRead:
    _get_bank_or_404(db, ctx, bank_id)
    return _get_service(db, ctx, bank_id).forecast(horizon=horizon, mode=mode)


def get_history(
    db: Session, ctx: TenantContext, bank_id: UUID, *, days: int
) -> CashflowHistoryRead:
    _get_bank_or_404(db, ctx, bank_id)
    return _get_service(db, ctx, bank_id).history(days)


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
    """Lazily trains/loads one bank's LSTM and serves its forecast and history."""

    def __init__(
        self,
        settings: CashflowSettings,
        config: TrainingConfig,
        *,
        series: list[DailyFlow],
        scope: CashflowForecastModelScope,
        artifacts_dir: Path,
    ) -> None:
        self._settings = settings
        self._config = config
        self._series = series
        self._scope: CashflowForecastModelScope = scope
        self._artifacts_dir = artifacts_dir
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
                loaded = ml.load_artifacts(self._artifacts_dir)
                if loaded is None:
                    ml.train_and_save(
                        config=self._config,
                        artifacts_dir=self._artifacts_dir,
                        series=self._series,
                    )
                    loaded = ml.load_artifacts(self._artifacts_dir)
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
            model_scope=self._scope,
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


# Per-tenant service cache: one warm model per (org, bank), no process-wide sharing.
_services: dict[tuple[UUID, UUID], ForecastService] = {}
_services_lock = threading.Lock()
_key_locks: dict[tuple[UUID, UUID], threading.Lock] = {}


def _key_lock(key: tuple[UUID, UUID]) -> threading.Lock:
    with _services_lock:
        return _key_locks.setdefault(key, threading.Lock())


def _bank_artifacts_dir(settings: CashflowSettings, key: tuple[UUID, UUID]) -> Path:
    org_id, bank_id = key
    return Path(settings.artifacts_dir) / str(org_id) / str(bank_id)


def _generic_artifacts_dir(settings: CashflowSettings) -> Path:
    return Path(settings.artifacts_dir) / "generic"


def _build_service(db: Session, ctx: TenantContext, bank_id: UUID) -> ForecastService:
    """Bank-specific service when the bank has enough own history, else generic."""
    settings = get_settings().cashflow
    config = TrainingConfig.from_settings(settings)
    key = (ctx.organization_id, bank_id)

    bank_series = load_bank_daily_series(db, ctx, bank_id)
    if len(bank_series) >= config.total_days:
        # Train on the most recent window of the bank's OWN daily history.
        return ForecastService(
            settings,
            config,
            series=bank_series[-config.total_days :],
            scope="bank_specific",
            artifacts_dir=_bank_artifacts_dir(settings, key),
        )
    return ForecastService(
        settings,
        config,
        series=_generic_series(config),
        scope="generic",
        artifacts_dir=_generic_artifacts_dir(settings),
    )


def _get_service(db: Session, ctx: TenantContext, bank_id: UUID) -> ForecastService:
    """One warm ForecastService per (org, bank); built once, then cached."""
    key = (ctx.organization_id, bank_id)
    with _services_lock:
        service = _services.get(key)
    if service is not None:
        return service
    with _key_lock(key):
        with _services_lock:
            service = _services.get(key)
        if service is not None:
            return service
        service = _build_service(db, ctx, bank_id)
        with _services_lock:
            _services[key] = service
        return service


def reset_forecast_service() -> None:
    """Testing hook: drop cached per-tenant services so fresh state takes effect."""
    with _services_lock:
        _services.clear()
        _key_locks.clear()


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
