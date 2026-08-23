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
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import CashflowSettings, get_settings
from app.ml.baseline import forecast_static
from app.ml.cashflow_history import load_bank_daily_series
from app.ml.config import MODEL_VERSION, TrainingConfig
from app.ml.real_series import load_real_daily_series
from app.ml.synthetic import DailyFlow, generate_daily_series
from app.models import Bank, CanonicalPosition, CanonicalPositionSnapshot
from app.schemas.cashflow_forecast import (
    CashflowForecastAccuracyRead,
    CashflowForecastMode,
    CashflowForecastModelScope,
    CashflowForecastPointRead,
    CashflowForecastRead,
    CashflowForecastScenario,
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
SIMULATION_PATHS = 1_000
_ASSET_TYPES = frozenset({"LOAN", "SECURITY_HOLDING", "CASH", "INTERBANK_PLACEMENT", "OTHER_ASSET"})
_LIABILITY_TYPES = frozenset({"DEPOSIT", "INTERBANK_BORROWING", "OTHER_LIABILITY"})
_INCLUDED = ("accepted", "warning")
_REQUIRED_DIAGNOSTICS = frozenset(
    {"bias_pct", "interval_coverage_pct", "residual_drift_pct", "residual_std"}
)


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


def get_forecast(  # noqa: PLR0913 - explicit tenant forecast inputs
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    horizon: int,
    mode: CashflowForecastMode,
    scenario: CashflowForecastScenario,
) -> CashflowForecastRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    return _get_service(db, ctx, bank_id).forecast(
        db, ctx, bank, horizon=horizon, mode=mode, scenario=scenario
    )


def get_history(
    db: Session, ctx: TenantContext, bank_id: str, *, days: int
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
                if (
                    loaded is None
                    or loaded[2].get("model_version") != MODEL_VERSION
                    or not _REQUIRED_DIAGNOSTICS.issubset(loaded[2])
                ):
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

    def forecast(  # noqa: PLR0913 - model, tenant, and scenario context are explicit
        self,
        db: Session,
        ctx: TenantContext,
        bank: Bank,
        *,
        horizon: int,
        mode: Literal["lstm", "static"],
        scenario: CashflowForecastScenario,
    ) -> CashflowForecastRead:
        model, scaler, metrics = self._require_trained()
        ml = _ml_model_or_503()
        as_of = self._series[-1].date
        accuracy = CashflowForecastAccuracyRead(
            lstm_mape=float(metrics["lstm_mape"]),
            static_mape=float(metrics["static_mape"]),
            improvement_pct=float(metrics["improvement_pct"]),
            bias_pct=float(metrics.get("bias_pct", 0)),
            interval_coverage_pct=float(metrics.get("interval_coverage_pct", 0)),
            residual_drift_pct=float(metrics.get("residual_drift_pct", 0)),
        )

        behavioral: list[float]
        if mode == "lstm":
            behavioral = ml.forecast_net_flows(model, scaler, self._series, horizon)
        else:
            behavioral = forecast_static(self._series, horizon)

        dates = [as_of + datetime.timedelta(days=day) for day in range(1, horizon + 1)]
        contractual = _contractual_daily_flows(db, ctx, bank, dates)
        adjustments, assumptions = _scenario_adjustments(behavioral, scenario)
        central = [
            behavioral[index] + contractual[index] + adjustments[index]
            for index in range(horizon)
        ]
        quantiles = _simulated_quantiles(
            central, float(metrics["residual_std"]), mode=mode, scenario=scenario
        )
        points = [
            CashflowForecastPointRead(
                day=index + 1,
                date=dates[index],
                net_flow=round(central[index], 4),
                lower=round(quantiles[index][0], 4),
                upper=round(quantiles[index][2], 4),
                behavioral_net_flow=round(behavioral[index], 4),
                contractual_net_flow=round(contractual[index], 4),
                scenario_adjustment=round(adjustments[index], 4),
                p5=round(quantiles[index][0], 4),
                p50=round(quantiles[index][1], 4),
                p95=round(quantiles[index][2], 4),
            )
            for index in range(horizon)
        ]

        return CashflowForecastRead(
            mode=mode,
            scenario=scenario,
            horizon=horizon,
            as_of_date=as_of,
            model_version=str(metrics["model_version"]),
            model_scope=self._scope,
            accuracy=accuracy,
            points=points,
            simulation_paths=SIMULATION_PATHS if mode == "lstm" else 1,
            scenario_assumptions=assumptions,
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
_services: dict[tuple[str, str], ForecastService] = {}
_services_lock = threading.Lock()
_key_locks: dict[tuple[str, str], threading.Lock] = {}


def _key_lock(key: tuple[str, str]) -> threading.Lock:
    with _services_lock:
        return _key_locks.setdefault(key, threading.Lock())


def _bank_artifacts_dir(settings: CashflowSettings, key: tuple[str, str]) -> Path:
    org_id, bank_id = key
    return Path(settings.artifacts_dir) / str(org_id) / str(bank_id)


def _generic_artifacts_dir(settings: CashflowSettings) -> Path:
    return Path(settings.artifacts_dir) / "generic"


def _build_service(db: Session, ctx: TenantContext, bank_id: str) -> ForecastService:
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


def _get_service(db: Session, ctx: TenantContext, bank_id: str) -> ForecastService:
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


def _contractual_daily_flows(
    db: Session, ctx: TenantContext, bank: Bank, dates: list[datetime.date]
) -> list[float]:
    """Accepted current book maturities, with assets positive and liabilities negative."""
    by_date = {day: 0.0 for day in dates}
    if not dates:
        return []
    as_of = dates[0] - datetime.timedelta(days=1)
    snapshot_as_of = db.scalar(
        select(func.max(CanonicalPositionSnapshot.as_of_date)).where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date <= as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED),
        )
    )
    if snapshot_as_of is None:
        return [0.0 for _ in dates]
    rows = db.execute(
        select(CanonicalPositionSnapshot, CanonicalPosition)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == snapshot_as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED),
            CanonicalPosition.position_type.in_((*_ASSET_TYPES, *_LIABILITY_TYPES)),
        )
    ).all()
    # Forecast as-of and book dates can differ. A missing exact snapshot must not
    # fabricate contractual flows; the behavioural component remains available.
    for snapshot, position in rows:
        maturity = snapshot.contractual_maturity
        if maturity not in by_date:
            continue
        raw = (snapshot.attributes or {}).get("balance_ghs")
        amount = float(raw) if raw not in (None, "") else float(snapshot.balance or 0)
        by_date[maturity] += amount if position.position_type in _ASSET_TYPES else -amount
    return [by_date[day] for day in dates]


def _scenario_adjustments(
    behavioral: list[float], scenario: CashflowForecastScenario
) -> tuple[list[float], list[str]]:
    if scenario == "baseline":
        return [0.0] * len(behavioral), ["Baseline: no planning overlay applied."]
    inflow_haircut, outflow_multiplier = (
        (Decimal("0.15"), Decimal("1.15"))
        if scenario == "adverse"
        else (Decimal("0.30"), Decimal("1.30"))
    )
    adjustments = [
        -float(Decimal(str(flow)) * inflow_haircut)
        if flow >= 0
        else float(Decimal(str(flow)) * (outflow_multiplier - Decimal("1")))
        for flow in behavioral
    ]
    label = "Adverse" if scenario == "adverse" else "Severe"
    return adjustments, [
        f"{label}: behavioural inflows reduced by {inflow_haircut * 100}%.",
        f"{label}: behavioural outflows increased by {(outflow_multiplier - Decimal('1')) * 100}%.",
        (
            "Contractual maturities remain unchanged; separate governed stress assumptions "
            "are required for contractual defaults and facility drawdowns."
        ),
    ]


def _simulated_quantiles(
    central: list[float],
    residual_std: float,
    *,
    mode: CashflowForecastMode,
    scenario: CashflowForecastScenario,
) -> list[tuple[float, float, float]]:
    if mode == "static" or residual_std <= 0:
        return [(value, value, value) for value in central]
    scenario_scale = {"baseline": 1.0, "adverse": 1.25, "severe": 1.6}[scenario]
    rng = np.random.default_rng(42)
    simulations = rng.normal(
        loc=np.array(central),
        scale=residual_std * scenario_scale,
        size=(SIMULATION_PATHS, len(central)),
    )
    return [
        tuple(np.quantile(simulations[:, index], [0.05, 0.5, 0.95]).tolist())
        for index in range(len(central))
    ]


def reset_forecast_service() -> None:
    """Testing hook: drop cached per-tenant services so fresh state takes effect."""
    with _services_lock:
        _services.clear()
        _key_locks.clear()


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
