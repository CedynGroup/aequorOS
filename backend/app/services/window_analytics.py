"""Engine-computed window analytics: real start/end-date computations server-side.

``compute_window`` selects the bank's reporting periods whose ``period_end``
falls inside the requested window and resolves each period's ratio values with
the SAME semantics as the module dashboards' trend builders (``_build_trend``
in ``regulatory_liquidity`` / ``regulatory_capital``): the latest succeeded
stored baseline run wins, otherwise the value is recomputed inline from the
canonical facts, and periods that can neither be read nor computed are skipped
(honest gaps, never zeros). On top of the series it computes the window
statistics — start/end, the move across the window, average and extremes —
plus per-module daily aggregates over the ``LiveMetricSnapshot`` ladder.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module, Permission, Sensitivity
from app.domain.capital.engine import CapitalComputationError
from app.domain.capital.engine import MissingParameterError as CapitalMissingParameter
from app.domain.liquidity.engine import LiquidityComputationError
from app.domain.liquidity.engine import MissingParameterError as LiquidityMissingParameter
from app.models import Bank, BankReportingPeriod, LiveMetricSnapshot
from app.schemas.window_analytics import (
    WindowAnalyticsRead,
    WindowDailyStatRead,
    WindowRatio,
    WindowRatioModule,
    WindowRatioPointRead,
    WindowRatioStatRead,
)
from app.services import scoped_authorization
from app.services.regulatory_capital import (
    CapitalRunError,
)
from app.services.regulatory_capital import (
    _compute_inline as _capital_compute_inline,  # noqa: PLC2701 - dashboard-trend parity
)
from app.services.regulatory_capital import (
    _decimal_metrics as _capital_run_metrics,  # noqa: PLC2701 - dashboard-trend parity
)
from app.services.regulatory_capital import (
    _latest_succeeded_baseline_run as _latest_capital_baseline_run,  # noqa: PLC2701
)
from app.services.regulatory_liquidity import (
    LiquidityRunError,
)
from app.services.regulatory_liquidity import (
    _compute_inline as _liquidity_compute_inline,  # noqa: PLC2701 - dashboard-trend parity
)
from app.services.regulatory_liquidity import (
    _latest_succeeded_baseline_run as _latest_liquidity_baseline_run,  # noqa: PLC2701
)
from app.services.regulatory_liquidity import (
    _scalar_metrics as _liquidity_run_metrics,  # noqa: PLC2701 - dashboard-trend parity
)
from app.services.stress_scenarios import _get_bank_or_404  # noqa: PLC2701 - shared guard

_MAX_WINDOW_MONTHS = 36
# Averages quantize to the run metrics' own 6-dp precision, so a one-point
# window reports avg == the point value exactly.
_QUANT = Decimal("0.000001")

# Mirror of the dashboard's components/live/moduleDisplay.ts PRIMARY_METRIC —
# the one headline metric per live module, in LIVE_MODULES order (``_daily_stats``
# emits rows in this order). ``tests/services/test_primary_metric_parity.py``
# reads both maps and fails when they drift.
_PRIMARY_METRIC_KEY: dict[str, str] = {
    "liquidity": "lcr_pct",
    "capital": "car_pct",
    "credit": "npl_ratio_pct",
    # ΔEVE / Tier 1 is stored SIGNED (a loss is negative) and the engine judges
    # it on magnitude; ``eve_limit_pct`` is the limit, a parameter, not a metric.
    "irr": "worst_eve_change_pct_tier1",
    "fx": "nop_pct_tier1",
    "ftp": "portfolio_nim_pct",
    # ``pit_pd_upper_pct`` is ADVISORY_ONLY in the authority registry, which is
    # a reason to label it, not a reason to drop the module: leaving it out made
    # the rating ladder silently absent from every window a Treasurer opened
    # while the pulse card headlined the same figure. The window-analysis panel
    # marks it (and FTP, and forecast) advisory — ``ADVISORY_HEADLINE_MODULES``
    # in ``components/live/moduleDisplay.ts``, pinned against this registry by
    # ``tests/services/test_primary_metric_parity.py``.
    "rating": "pit_pd_upper_pct",
    "forecast": "year5_car_pct",
}

#: Daily-snapshot modules served only to a principal holding an exact aggregated
#: ``view`` binding on the engine's module, filtered in SQL before aggregation.
#: Capital and rating rows are still served to every tenant reader —
#: their module cutovers own that decision, and this list stays identical to
#: ``live_view._GATED_ENGINE_MODULES`` so the two surfaces cannot disagree about
#: who may read an engine. It must only ever grow.
_GATED_ENGINE_MODULES: tuple[tuple[str, Module], ...] = (
    ("liquidity", Module.LIQUIDITY),
    ("credit", Module.CREDIT),
    ("irr", Module.IRRBB),
    ("fx", Module.FX),
    ("ftp", Module.FTP),
    ("forecast", Module.FORECASTING),
)


def compute_window(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    start_date: date,
    end_date: date,
) -> WindowAnalyticsRead:
    """Ratio series + window statistics + daily aggregates for [start, end]."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    _validate_window(start_date, end_date)
    allowed: dict[str, bool] = {}
    for engine, module in _GATED_ENGINE_MODULES:
        decision = scoped_authorization.evaluate_bank_permission(
            db,
            ctx,
            bank,
            permission=Permission.VIEW,
            module=module,
            sensitivity=Sensitivity.AGGREGATED,
            surface="window_analytics",
        )
        allowed[engine] = decision is not None and decision.allowed
    periods = _periods_in_window(db, ctx, bank, start_date, end_date)
    ratios = [
        *(_liquidity_series(db, ctx, bank, periods) if allowed["liquidity"] else []),
        *_capital_series(db, ctx, bank, periods),
    ]
    return WindowAnalyticsRead(
        bank_id=bank.id,
        start_date=start_date,
        end_date=end_date,
        period_count=len(periods),
        ratios=ratios,
        daily=_daily_stats(db, ctx, bank, start_date, end_date, allowed=allowed),
    )


def _validate_window(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "invalid_window",
                "message": "start_date must be on or before end_date.",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    if months > _MAX_WINDOW_MONTHS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "window_too_large",
                "message": f"The analysis window is capped at {_MAX_WINDOW_MONTHS} months.",
                "window_months": months,
                "max_months": _MAX_WINDOW_MONTHS,
            },
        )


def _periods_in_window(
    db: Session, ctx: TenantContext, bank: Bank, start_date: date, end_date: date
) -> list[BankReportingPeriod]:
    """EVERY reporting period whose ``period_end`` falls in [start, end], ascending.

    Deliberately NOT ``domain.reporting.period_windows.trailing_month_end_window``,
    which the five module dashboards select their sparkline through. That helper
    exists because their fixed 13-ROW slice stood in for a 13-month horizon, so
    its span changed meaning with the bank's feed cadence; it is anchored on the
    latest period, looks back a number of MONTHS, and has no earlier bound to
    respect.

    Nothing stands in for anything here: the horizon is the caller's own two
    dates, and ``period_count`` reports this count ON THE WIRE (the window
    analysis footer reads "N periods"). Thinning the selection to month-ends
    would drop periods the caller explicitly asked for and cap the 36-month
    window at 13 points — a change to what an API field means, not a tidier
    selection. A daily feeder returning ~250 points a year is the honest answer
    to the dates it was given. Pinned by
    ``tests/services/test_window_analytics.py::test_period_count_counts_every_period_the_caller_asked_for``.
    """
    return list(
        db.scalars(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.organization_id == ctx.organization_id,
                BankReportingPeriod.bank_id == bank.id,
                BankReportingPeriod.period_end >= start_date,
                BankReportingPeriod.period_end <= end_date,
            )
            .order_by(BankReportingPeriod.period_end)
        )
    )


def _liquidity_series(
    db: Session, ctx: TenantContext, bank: Bank, periods: list[BankReportingPeriod]
) -> list[WindowRatioStatRead]:
    lcr: list[WindowRatioPointRead] = []
    nsfr: list[WindowRatioPointRead] = []
    for period in periods:
        run = _latest_liquidity_baseline_run(db, ctx, bank, period.id)
        if run is not None:
            metrics = _liquidity_run_metrics(run)
            lcr.append(_point(period, metrics["lcr_pct"], stored=True))
            nsfr.append(_point(period, metrics["nsfr_pct"], stored=True))
            continue
        try:
            lcr_result, nsfr_result, _params = _liquidity_compute_inline(db, ctx, bank, period)
        except (LiquidityMissingParameter, LiquidityComputationError, LiquidityRunError):
            continue
        lcr.append(_point(period, lcr_result.lcr_pct, stored=False))
        nsfr.append(_point(period, nsfr_result.nsfr_pct, stored=False))
    return [
        *_ratio_stat("lcr_pct", "liquidity", lcr),
        *_ratio_stat("nsfr_pct", "liquidity", nsfr),
    ]


def _capital_series(
    db: Session, ctx: TenantContext, bank: Bank, periods: list[BankReportingPeriod]
) -> list[WindowRatioStatRead]:
    car: list[WindowRatioPointRead] = []
    cet1: list[WindowRatioPointRead] = []
    for period in periods:
        run = _latest_capital_baseline_run(db, ctx, bank, period.id)
        if run is not None:
            metrics = _capital_run_metrics(run)
            car.append(_point(period, metrics["car_pct"], stored=True))
            cet1.append(_point(period, metrics["cet1_ratio_pct"], stored=True))
            continue
        try:
            _rwa, ratios, _params = _capital_compute_inline(db, ctx, bank, period)
        except (CapitalMissingParameter, CapitalComputationError, CapitalRunError):
            continue
        car.append(_point(period, ratios.car_pct, stored=False))
        cet1.append(_point(period, ratios.cet1_ratio_pct, stored=False))
    return [
        *_ratio_stat("car_pct", "capital", car),
        *_ratio_stat("cet1_ratio_pct", "capital", cet1),
    ]


def _point(period: BankReportingPeriod, value: Decimal, *, stored: bool) -> WindowRatioPointRead:
    return WindowRatioPointRead(period_end=period.period_end, value=value, stored=stored)


def _ratio_stat(
    ratio: WindowRatio, module: WindowRatioModule, points: list[WindowRatioPointRead]
) -> list[WindowRatioStatRead]:
    """Zero or one stat rows — a ratio with no resolvable points is omitted."""
    if not points:
        return []
    values = [point.value for point in points]
    avg = (sum(values, Decimal(0)) / Decimal(len(values))).quantize(_QUANT)
    return [
        WindowRatioStatRead(
            ratio=ratio,
            module=module,
            points=points,
            start_value=values[0],
            end_value=values[-1],
            change=values[-1] - values[0],
            avg=avg,
            min=min(values),
            max=max(values),
        )
    ]


def _daily_stats(  # noqa: PLR0913 - explicit tenant, date window, and authorization decision
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    start_date: date,
    end_date: date,
    *,
    allowed: Mapping[str, bool],
) -> list[WindowDailyStatRead]:
    """``allowed`` carries one verdict per gated engine (``_GATED_ENGINE_MODULES``).

    A gated engine absent from ``allowed`` is excluded: the gate list and the
    verdicts come from the same tuple, so a missing key can only mean the
    caller skipped the evaluation, and that must not read as permission.
    """
    query = (
        select(LiveMetricSnapshot)
        .where(
            LiveMetricSnapshot.organization_id == ctx.organization_id,
            LiveMetricSnapshot.bank_id == bank.id,
            LiveMetricSnapshot.snapshot_date >= start_date,
            LiveMetricSnapshot.snapshot_date <= end_date,
        )
        .order_by(LiveMetricSnapshot.snapshot_date)
    )
    for engine, _module in _GATED_ENGINE_MODULES:
        if not allowed.get(engine, False):
            query = query.where(LiveMetricSnapshot.module != engine)
    # capital carries no module gate here (``live-summary`` exposes it ungated
    # today); the capital cutover owns that decision.
    values_by_module: dict[str, list[Decimal]] = {}
    for row in db.scalars(query):
        key = _PRIMARY_METRIC_KEY.get(row.module)
        if key is None:
            continue
        raw = row.metrics.get(key)
        if isinstance(raw, bool) or not isinstance(raw, str | int | float):
            continue
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            continue
        values_by_module.setdefault(row.module, []).append(value)
    return [
        WindowDailyStatRead(
            module=module,
            metric_key=_PRIMARY_METRIC_KEY[module],
            day_count=len(values),
            min=min(values),
            avg=(sum(values, Decimal(0)) / Decimal(len(values))).quantize(_QUANT),
            max=max(values),
        )
        # _PRIMARY_METRIC_KEY iterates in the canonical module order.
        for module in _PRIMARY_METRIC_KEY
        if (values := values_by_module.get(module))
    ]
