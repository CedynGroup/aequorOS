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


def fx_forward_to_bundle(
    scope: DataScope, observations: Sequence[FxObservation]
) -> MarketDataBundle:
    """Translate one FX forward scope's observations into a persistable bundle.

    ``FX_FORWARD_USD_GHS_3M`` produces ``base=USD``, ``quote=GHS``,
    ``rate_type='forward'`` at the 3-month tenor. The RDP forward-outright RIC
    quotes GHS per 1 USD like spot, so the value maps through unchanged; the
    tenor (parsed from the scope) is what distinguishes forward records of the
    same pair in the canonical natural key.
    """
    parts = scope.value.split("_")
    if len(parts) != _FORWARD_SCOPE_PARTS or parts[0] != "FX" or parts[1] != "FORWARD":
        msg = f"fx_forward_to_bundle only translates FX_FORWARD scopes, got {scope.value!r}."
        raise ValueError(msg)
    base_currency, quote_currency, tenor = parts[2], parts[3], parts[4]
    tenor_months = _tenor_to_months(tenor)

    bundle = MarketDataBundle()
    for observation in observations:
        bundle.fx_rates.append(
            FxRateRecord(
                base_currency=base_currency,
                quote_currency=quote_currency,
                rate_type="forward",
                tenor_months=tenor_months,
                rate=observation.value,
                source_reference=observation.ric,
            )
        )
        bundle.sample_values[f"{base_currency}/{quote_currency} {tenor}"] = (
            f"{observation.value:.2f}"
        )
    return bundle
