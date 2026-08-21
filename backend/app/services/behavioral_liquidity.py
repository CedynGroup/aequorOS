"""Evidence-backed deposit-behavior analytics for liquidity management.

Every result is calculated from a bank's canonical deposit history. Metrics that
need unavailable rate, account-count, or seasonal observations are returned as
``None`` with a named reason rather than inferred from another segment.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.ml.behavioral import history
from app.ml.behavioral.config import BehavioralTrainingConfig
from app.models import Bank, ContingencyFundingPlan

_DIMENSIONS = ("product", "customer_segment", "concentration_group", "branch")
_MIN_RATE_OBSERVATIONS = 6
_MAX_REPRICING_LAG_MONTHS = 3


@dataclass(frozen=True)
class BehavioralLiquiditySegment:
    dimension: str
    segment: str
    current_balance_ghs: float
    observed_monthly_runoff_pct: float | None
    latest_withdrawal_pct: float | None
    position_attrition_pct: float | None
    seasonal_deviation_pct: float | None
    deposit_beta: float | None
    repricing_lag_months: int | None
    observations: int
    data_status: str
    reasons: list[str]


@dataclass(frozen=True)
class BehavioralLiquidityScenario:
    name: str
    activation_horizon: str
    linked_action: str
    deposit_runoff_uplift_pct: float
    funding_cost_uplift_bps: float
    notes: str | None


@dataclass(frozen=True)
class BehavioralLiquidityReport:
    as_of_date: datetime.date | None
    segments: list[BehavioralLiquiditySegment]
    scenarios: list[BehavioralLiquidityScenario]


def _dimension_value(row: history.DepositMonthAgg, dimension: str) -> str:
    if dimension == "product":
        return row.product_code
    if dimension == "customer_segment":
        return row.counterparty_type or "<unclassified>"
    if dimension == "concentration_group":
        return row.concentration_group
    return row.branch_id


def _series_by_dimension(
    rows: list[history.DepositMonthAgg], dimension: str
) -> dict[str, list[tuple[datetime.date, float, int, float | None]]]:
    buckets: dict[str, dict[datetime.date, dict[str, float]]] = defaultdict(dict)
    for row in rows:
        segment = _dimension_value(row, dimension)
        cell = buckets[segment].setdefault(
            row.as_of_date,
            {"balance": 0.0, "accounts": 0.0, "rate_num": 0.0, "rate_den": 0.0},
        )
        cell["balance"] += row.balance_ghs
        cell["accounts"] += row.n_accounts
        if row.avg_rate is not None and row.balance_ghs > 0:
            cell["rate_num"] += row.avg_rate * row.balance_ghs
            cell["rate_den"] += row.balance_ghs
    return {
        segment: [
            (
                observed,
                values["balance"],
                int(values["accounts"]),
                values["rate_num"] / values["rate_den"] if values["rate_den"] else None,
            )
            for observed, values in sorted(points.items())
        ]
        for segment, points in buckets.items()
    }


def _rate_metrics(
    points: list[tuple[datetime.date, float, int, float | None]],
    short_rates: dict[datetime.date, float],
) -> tuple[float | None, int | None, list[str]]:
    indexed = [
        (date, rate, short_rates[date])
        for date, _balance, _accounts, rate in points
        if rate is not None and date in short_rates
    ]
    if len(indexed) < _MIN_RATE_OBSERVATIONS:
        return None, None, [
            "At least six matched deposit-rate and market-rate observations are required."
        ]
    deposit_rates = np.asarray([row[1] for row in indexed], dtype=float)
    market_rates = np.asarray([row[2] for row in indexed], dtype=float)
    if np.var(market_rates) == 0:
        return None, None, ["Matched market-rate history has no variation for beta estimation."]
    beta = float(np.cov(market_rates, deposit_rates, ddof=0)[0, 1] / np.var(market_rates))
    best_lag: int | None = None
    best_correlation = -1.0
    for lag in range(_MAX_REPRICING_LAG_MONTHS + 1):
        if len(indexed) - lag < _MIN_RATE_OBSERVATIONS:
            continue
        deposits = deposit_rates[lag:]
        markets = market_rates[: len(market_rates) - lag]
        if np.std(deposits) == 0 or np.std(markets) == 0:
            continue
        correlation = abs(float(np.corrcoef(deposits, markets)[0, 1]))
        if correlation > best_correlation:
            best_lag, best_correlation = lag, correlation
    reasons = (
        []
        if best_lag is not None
        else ["Rate series cannot support a repricing-lag estimate."]
    )
    return beta, best_lag, reasons


def _segment_metrics(
    dimension: str,
    segment: str,
    points: list[tuple[datetime.date, float, int, float | None]],
    short_rates: dict[datetime.date, float],
) -> BehavioralLiquiditySegment:
    reasons: list[str] = []
    balances = [point[1] for point in points]
    current_balance = balances[-1] if balances else 0.0
    runoff: float | None = None
    withdrawal: float | None = None
    attrition: float | None = None
    seasonal: float | None = None
    if len(points) < 2:
        reasons.append(
            "At least two deposit snapshots are required for runoff and withdrawal analytics."
        )
    else:
        changes = [
            max(0.0, 1 - current / prior) * 100
            for prior, current in zip(balances, balances[1:], strict=False)
            if prior > 0
        ]
        runoff = max(changes) if changes else None
        withdrawal = changes[-1] if changes else None
        prior_accounts, current_accounts = points[-2][2], points[-1][2]
        if prior_accounts > 0:
            attrition = max(0.0, 1 - current_accounts / prior_accounts) * 100
        else:
            reasons.append("Prior position count is zero, so attrition cannot be computed.")
    same_month_prior = [point[1] for point in points[:-1] if point[0].month == points[-1][0].month]
    if same_month_prior:
        seasonal = (current_balance / float(np.mean(same_month_prior)) - 1) * 100
    else:
        reasons.append(
            "A prior observation for the same calendar month is required for seasonality."
        )
    beta, lag, rate_reasons = _rate_metrics(points, short_rates)
    reasons.extend(rate_reasons)
    ready_count = sum(
        value is not None for value in (runoff, withdrawal, attrition, seasonal, beta, lag)
    )
    data_status: Literal["ready", "partial", "not_computable"] = (
        "ready" if ready_count == 6 else "partial" if ready_count else "not_computable"
    )
    return BehavioralLiquiditySegment(
        dimension=dimension,
        segment=segment,
        current_balance_ghs=current_balance,
        observed_monthly_runoff_pct=runoff,
        latest_withdrawal_pct=withdrawal,
        position_attrition_pct=attrition,
        seasonal_deviation_pct=seasonal,
        deposit_beta=beta,
        repricing_lag_months=lag,
        observations=len(points),
        data_status=data_status,
        reasons=reasons,
    )


def _approved_scenarios(
    db: Session, ctx: TenantContext, bank_id: str
) -> list[BehavioralLiquidityScenario]:
    plan = db.scalar(
        select(ContingencyFundingPlan)
        .where(
            ContingencyFundingPlan.organization_id == ctx.organization_id,
            ContingencyFundingPlan.bank_id == bank_id,
            ContingencyFundingPlan.status == "approved",
        )
        .order_by(ContingencyFundingPlan.version.desc())
        .limit(1)
    )
    if plan is None:
        return []
    return [
        BehavioralLiquidityScenario(
            name=str(item["name"]),
            activation_horizon=str(item["activation_horizon"]),
            linked_action=str(item["linked_action"]),
            deposit_runoff_uplift_pct=float(item["deposit_runoff_uplift_pct"]),
            funding_cost_uplift_bps=float(item["funding_cost_uplift_bps"]),
            notes=str(item["notes"]) if item.get("notes") is not None else None,
        )
        for item in plan.content.get("behavioral_liquidity_scenarios", [])
    ]


def get_behavioral_liquidity_report(
    db: Session, ctx: TenantContext, bank_id: str
) -> BehavioralLiquidityReport:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    dates = history.available_as_of_dates(db, ctx, bank_id)
    if not dates:
        return BehavioralLiquidityReport(as_of_date=None, segments=[], scenarios=[])
    as_of = dates[-1]
    cfg = BehavioralTrainingConfig.from_settings(get_settings().behavioral)
    rows = history.load_deposit_month_aggregates(
        db, ctx, bank_id, as_of, cfg.window_months, non_maturing_only=False
    )
    short_rates = history.load_ghs_short_rate_history(db, ctx, bank_id, as_of, cfg.window_months)
    segments = [
        _segment_metrics(dimension, segment, points, short_rates)
        for dimension in _DIMENSIONS
        for segment, points in _series_by_dimension(rows, dimension).items()
    ]
    ranked = sorted(
        segments,
        key=lambda item: (-item.current_balance_ghs, item.dimension, item.segment),
    )
    return BehavioralLiquidityReport(
        as_of_date=as_of,
        segments=ranked[:40],
        scenarios=_approved_scenarios(db, ctx, bank_id),
    )