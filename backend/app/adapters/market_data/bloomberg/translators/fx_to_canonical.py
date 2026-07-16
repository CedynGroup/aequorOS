"""FX-spot translation: Bloomberg price -> canonical fx_rate record (§6.4)."""

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


def fx_spot_bundle(scope: DataScope, observation: FxObservation) -> MarketDataBundle:
    """One FX-spot scope's observation as a persistable bundle.

    ``FX_SPOT_USD_GHS`` denominates the pair base=USD, quote=GHS: the rate is
    GHS per one USD (``GHSUSD Curncy`` PX_LAST ~12.85). FX prices are prices,
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
