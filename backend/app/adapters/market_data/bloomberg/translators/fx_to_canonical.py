"""FX translation: Bloomberg price -> canonical fx_rate record (§6.4)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from app.adapters.market_data.pull_runner import FxRateRecord, MarketDataBundle

if TYPE_CHECKING:
    from app.adapters.market_data.bloomberg.extractors.fx import FxObservation
    from app.adapters.market_data.scope_taxonomy import DataScope

# Canonical rate precision: Numeric(18, 8) on canonical_fx_rates.
_RATE_QUANTUM = Decimal("0.00000001")
_SCOPE_PARTS = 4  # FX_SPOT_{BASE}_{QUOTE}
_FORWARD_SCOPE_PARTS = 5  # FX_FORWARD_{BASE}_{QUOTE}_{TENOR}
_MONTHS_PER_YEAR = 12


def _tenor_to_months(tenor: str) -> int:
    """Parse a scope tenor suffix (``1M``, ``3M``, ``12M``, ``1Y``) to months."""
    unit = tenor[-1].upper()
    try:
        magnitude = int(tenor[:-1])
    except ValueError as exc:
        msg = f"unparsable FX forward tenor {tenor!r}"
        raise ValueError(msg) from exc
    if unit == "M":
        return magnitude
    if unit == "Y":
        return magnitude * _MONTHS_PER_YEAR
    msg = f"unsupported FX forward tenor unit in {tenor!r}"
    raise ValueError(msg)


def fx_spot_bundle(scope: DataScope, observation: FxObservation) -> MarketDataBundle:
    """One FX-spot scope's observation as a persistable bundle.

    ``FX_SPOT_USD_GHS`` denominates the pair base=USD, quote=GHS: the rate is
    GHS per one USD (``USDGHS Curncy`` PX_LAST ~12.85). FX prices are prices,
    not percents — no /100 conversion. Spot rates carry no tenor.
    """
    parts = scope.value.split("_")
    if len(parts) != _SCOPE_PARTS or parts[:2] != ["FX", "SPOT"]:
        msg = f"{scope.value} is not an FX spot scope."
        raise ValueError(msg)
    base_currency, quote_currency = parts[2], parts[3]
    rate = observation.value.quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP)
    record = FxRateRecord(
        base_currency=base_currency,
        quote_currency=quote_currency,
        rate_type="spot",
        tenor_months=None,
        rate=rate,
        source_reference=observation.security,
    )
    samples = {f"{base_currency}/{quote_currency} spot": f"{observation.value:.4f}"}
    return MarketDataBundle(fx_rates=[record], sample_values=samples)


def fx_forward_bundle(scope: DataScope, observation: FxObservation) -> MarketDataBundle:
    """One FX-forward scope's observation as a persistable bundle.

    ``FX_FORWARD_USD_GHS_3M`` denominates base=USD, quote=GHS at the 3-month
    tenor. The Bloomberg value is the forward outright rate (GHS per one USD,
    3 months forward); like spot it is a price, never a percent. Forward rates
    carry their tenor in months so the runner's natural key distinguishes
    tenors of the same pair.
    """
    parts = scope.value.split("_")
    if len(parts) != _FORWARD_SCOPE_PARTS or parts[:2] != ["FX", "FORWARD"]:
        msg = f"{scope.value} is not an FX forward scope."
        raise ValueError(msg)
    base_currency, quote_currency, tenor = parts[2], parts[3], parts[4]
    tenor_months = _tenor_to_months(tenor)
    rate = observation.value.quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP)
    record = FxRateRecord(
        base_currency=base_currency,
        quote_currency=quote_currency,
        rate_type="forward",
        tenor_months=tenor_months,
        rate=rate,
        source_reference=observation.security,
    )
    samples = {f"{base_currency}/{quote_currency} {tenor} forward": f"{observation.value:.4f}"}
    return MarketDataBundle(fx_rates=[record], sample_values=samples)
