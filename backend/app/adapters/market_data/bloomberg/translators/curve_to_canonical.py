"""Yield-curve translation: percent PX_LAST values -> decimal-fraction curve (§6.4)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from app.adapters.market_data.pull_runner import CurvePoint, CurveRecord, MarketDataBundle

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.adapters.market_data.bloomberg.extractors.curves import CurveFieldObservation
    from app.adapters.market_data.scope_taxonomy import DataScope

_PERCENT = Decimal("100")
# Canonical rate precision: Numeric(18, 8) on canonical_yield_curve_points.
_RATE_QUANTUM = Decimal("0.00000001")
_MONTHS_PER_YEAR = 12
_YIELD_CURVE_PREFIX = "YIELD_CURVE_"


def tenor_label(tenor_months: int) -> str:
    """Human-readable tenor: months under two years, years beyond (``3M``, ``5Y``)."""
    if tenor_months < 2 * _MONTHS_PER_YEAR or tenor_months % _MONTHS_PER_YEAR:
        return f"{tenor_months}M"
    return f"{tenor_months // _MONTHS_PER_YEAR}Y"


def curve_bundle(
    scope: DataScope, observations: Sequence[CurveFieldObservation]
) -> MarketDataBundle:
    """One yield-curve scope's observations as a persistable bundle.

    Bloomberg BVAL PX_LAST yields arrive as percents (``15.80``); canonical
    rates are decimal fractions (``0.158``) per data_engine.md §4.6 — divide
    by 100 and quantize to the stored precision. The curve-level
    ``source_reference`` lists the constituent Bloomberg tickers; the pull
    runner derives per-point references by appending the tenor.
    """
    currency = scope.value.removeprefix(_YIELD_CURVE_PREFIX)
    ordered = sorted(observations, key=lambda obs: obs.tenor_months)
    points = tuple(
        CurvePoint(
            tenor_months=obs.tenor_months,
            rate=(obs.value / _PERCENT).quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP),
        )
        for obs in ordered
    )
    record = CurveRecord(
        currency=currency,
        # Same naming as the Refinitiv translator so one economic curve keeps
        # one name across sources; cross-source arbitration stays read-time.
        curve_name=f"{currency}_SOVEREIGN",
        curve_type="sovereign",
        source_reference=",".join(obs.security for obs in ordered),
        points=points,
    )
    samples = {
        f"{currency} {tenor_label(obs.tenor_months)}": f"{obs.value:.2f}%" for obs in ordered
    }
    return MarketDataBundle(curves=[record], sample_values=samples)
