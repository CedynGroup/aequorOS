"""FX translator: RDP mid prices -> canonical FX rate records.

Convention (locked to the §9.2 sample "USD/GHS spot: 12.85"):
``FX_SPOT_USD_GHS`` produces ``base_currency=USD``, ``quote_currency=GHS``,
and ``rate`` = GHS per 1 USD (~11-13). The ``USDGHS=R`` RIC quotes in
exactly that direction, so the vendor value maps through unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.adapters.market_data.pull_runner import FxRateRecord, MarketDataBundle
from app.adapters.market_data.refinitiv.extractors.fx import FxObservation
from app.adapters.market_data.scope_taxonomy import DataScope

_SPOT_SCOPE_PARTS = 4  # FX_SPOT_{BASE}_{QUOTE}


def fx_to_bundle(scope: DataScope, observations: Sequence[FxObservation]) -> MarketDataBundle:
    """Translate one FX spot scope's observations into a persistable bundle.

    Only spot scopes are supported: the §7.2 catalog documents no forward
    RICs, and inventing them is prohibited (§16.4).
    """
    parts = scope.value.split("_")
    if len(parts) != _SPOT_SCOPE_PARTS or parts[0] != "FX" or parts[1] != "SPOT":
        msg = f"fx_to_bundle only translates FX_SPOT scopes, got {scope.value!r}."
        raise ValueError(msg)
    base_currency, quote_currency = parts[2], parts[3]

    bundle = MarketDataBundle()
    for observation in observations:
        bundle.fx_rates.append(
            FxRateRecord(
                base_currency=base_currency,
                quote_currency=quote_currency,
                rate_type="spot",
                tenor_months=None,
                rate=observation.value,
                source_reference=observation.ric,
            )
        )
        bundle.sample_values[f"{base_currency}/{quote_currency}"] = f"{observation.value:.2f}"
    return bundle
