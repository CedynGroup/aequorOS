"""Curve translator: percent yields -> canonical decimal-fraction curve records.

Refinitiv ``TR.MidYield`` values arrive as percents (``15.80`` meaning
15.80%). Canonical rates are decimal fractions per data_engine.md §4.6
(``0.158``, never ``15.8``): the division by 100 happens exactly once, here.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from app.adapters.market_data.pull_runner import CurvePoint, CurveRecord, MarketDataBundle
from app.adapters.market_data.refinitiv.extractors.curves import CurveObservation
from app.adapters.market_data.scope_taxonomy import DataScope

_PERCENT = Decimal("100")
# Canonical rate columns are Numeric(18, 8); quantize half-up per conventions.
RATE_QUANTUM = Decimal("0.00000001")

_MONTHS_PER_YEAR = 12


def percent_to_fraction(value_percent: Decimal) -> Decimal:
    """Convert a vendor percent yield (``15.80``) to a decimal fraction (``0.158``)."""
    return (value_percent / _PERCENT).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def tenor_label(tenor_months: int) -> str:
    """Human-readable tenor for sample values: 3 -> ``3M``, 24 -> ``2Y``."""
    if tenor_months >= _MONTHS_PER_YEAR and tenor_months % _MONTHS_PER_YEAR == 0:
        return f"{tenor_months // _MONTHS_PER_YEAR}Y"
    return f"{tenor_months}M"


def curve_to_bundle(
    scope: DataScope, observations: Sequence[CurveObservation]
) -> MarketDataBundle:
    """Translate one curve scope's observations into a persistable bundle.

    The curve header's ``source_reference`` joins every contributing RIC so
    lineage names the exact instruments (§13.2); per-point references are
    derived by the pull runner from the header.
    """
    currency = scope.value.rsplit("_", 1)[-1]
    ordered = sorted(observations, key=lambda item: item.tenor_months)
    record = CurveRecord(
        currency=currency,
        curve_name=f"{currency}_SOVEREIGN",
        curve_type="sovereign",
        source_reference=",".join(item.ric for item in ordered),
        points=tuple(
            CurvePoint(
                tenor_months=item.tenor_months, rate=percent_to_fraction(item.value_percent)
            )
            for item in ordered
        ),
    )
    sample_values = {
        f"{currency} {tenor_label(item.tenor_months)}": f"{item.value_percent:.2f}%"
        for item in ordered
    }
    return MarketDataBundle(curves=[record], sample_values=sample_values)
