"""Shared fixtures for the Phase 2 enterprise-stress domain tests.

Reuses the canonical Sample Bank Ltd fact set and BoG CRD parameters from
``test_forecasting_engine`` (the hand-authored golden home) and adds the
macro-scenario paths and the capital/liquidity fact converters the stress
modules consume. Not a ``test_*`` module, so pytest does not collect it.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.capital.engine import CapitalFact
from app.domain.liquidity.engine import LiquidityFact
from app.domain.stress.translation import MacroPathPoint
from tests.domain.test_forecasting_engine import (
    BASE_ASSUMPTIONS,
    bog_capital_params,
    bog_forecast_params,
    bog_liquidity_params,
    sample_bank_latest_facts,
)

__all__ = [
    "BASE_ASSUMPTIONS",
    "M",
    "base_paths",
    "bog_capital_params",
    "bog_forecast_params",
    "bog_liquidity_params",
    "capital_facts",
    "liquidity_facts",
    "sample_bank_latest_facts",
    "severe_paths",
]

M = Decimal("1000000")

_CAPITAL_GROUPS = (
    "balance_sheet",
    "loan_exposure",
    "off_balance",
    "market_risk",
    "operational_income",
    "capital_component",
)
_LIQUIDITY_GROUPS = (
    "balance_sheet",
    "securities",
    "loan_exposure",
    "off_balance",
    "lcr_inflow",
)


def capital_facts() -> tuple[CapitalFact, ...]:
    return tuple(
        CapitalFact(
            fact_group=fact.fact_group,
            category=fact.category,
            amount=fact.amount,
            risk_weight_code=fact.risk_weight_code,
            ccf_pct=fact.ccf_pct,
            income_year=fact.income_year,
            capital_tier=fact.capital_tier,
            is_deduction=fact.is_deduction,
            side=fact.side,
        )
        for fact in sample_bank_latest_facts()
        if fact.fact_group in _CAPITAL_GROUPS
    )


def liquidity_facts() -> tuple[LiquidityFact, ...]:
    return tuple(
        LiquidityFact(
            fact_group=fact.fact_group,
            category=fact.category,
            amount=fact.amount,
            hqla_level=fact.hqla_level,
            side=fact.side,
            cash_derived=fact.cash_derived,
        )
        for fact in sample_bank_latest_facts()
        if fact.fact_group in _LIQUIDITY_GROUPS
    )


def _p(variable: str, year: int, base: str, stress: str) -> MacroPathPoint:
    return MacroPathPoint(
        variable=variable,
        year_index=year,
        base_value=Decimal(base),
        stress_value=Decimal(stress),
    )


# Base-case macro levels (year 0 = as-of), the SAME every year (a flat plan).
_BASE_LEVELS: dict[str, str] = {
    "gdp_growth": "0.05",
    "interest_rate": "0.20",
    "inflation": "0.15",
    "unemployment": "0.06",
    "fx_usd_ghs": "12.5",
    "gse_index": "5000",
    "gog_yield": "0.22",
}
# Severe adverse stress levels applied uniformly across the projected years
# (constant per-year deltas, so the per-year machinery is exercised while the
# hand-computation of the deltas stays tractable).
_STRESS_LEVELS: dict[str, str] = {
    "gdp_growth": "0.00",
    "interest_rate": "0.25",
    "inflation": "0.21",
    "unemployment": "0.09",
    "fx_usd_ghs": "15.0",
    "gse_index": "3500",
    "gog_yield": "0.26",
}


def severe_paths(horizon: int = 3) -> tuple[MacroPathPoint, ...]:
    """A severe adverse scenario: as-of year 0 unstressed, years 1..N stressed."""
    points: list[MacroPathPoint] = []
    for variable, base in _BASE_LEVELS.items():
        stress = _STRESS_LEVELS[variable]
        for year in range(horizon + 1):
            points.append(_p(variable, year, base, base if year == 0 else stress))
    return tuple(points)


def base_paths(horizon: int = 3) -> tuple[MacroPathPoint, ...]:
    """A base scenario: stress == base everywhere (must yield a zero stress delta)."""
    points: list[MacroPathPoint] = []
    for variable, base in _BASE_LEVELS.items():
        for year in range(horizon + 1):
            points.append(_p(variable, year, base, base))
    return tuple(points)
