"""Yield-curve extractor: RDP tenor-yield rows -> :class:`CurveObservation`.

Fixture-recorded RDP response shape (see ``transport.py`` for the envelope)::

    {
      "universe": ["GH1M=", "GH3M=", ...],
      "fields": ["TR.MidYield"],
      "data": [["GH1M=", "TR.MidYield", 24.10], ...]
    }

``TR.MidYield`` values arrive as PERCENTS (``15.80`` meaning 15.80%);
conversion to decimal fractions happens in the curve translator, never here —
extractors preserve vendor units so raw and intermediate stay comparable.

A requested RIC missing from the response, or carrying a non-numeric value,
classifies as UNKNOWN_INSTRUMENT (§12.1): a curve with a silently dropped
tenor would corrupt every downstream gap and EVE calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.refinitiv.transport import (
    RdpTransport,
    bank_facing_for,
    data_rows,
    raise_for_vendor_error,
    request_spec_for,
)
from app.adapters.market_data.scope_taxonomy import DataScope


@dataclass(frozen=True)
class CurveObservation:
    """One tenor's observed yield, still in vendor units (percent)."""

    ric: str
    field: str
    tenor_months: int
    value_percent: Decimal


def extract_curve(
    transport: RdpTransport,
    session_token: str,
    scope: DataScope,
    request_specs: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[CurveObservation, ...]]:
    """Fetch and parse one yield-curve scope.

    Returns ``(raw_payload, observations)`` — the raw payload is preserved
    verbatim for raw-tier audit storage (§13.3).
    """
    payload = transport.fetch(session_token, request_spec_for(scope, request_specs))
    raise_for_vendor_error(payload, scope.value)
    values = {(str(row[0]), str(row[1])): row[2] for row in data_rows(payload, scope.value)}

    observations: list[CurveObservation] = []
    for spec in request_specs:
        ric = str(spec["ric"])
        field = str(spec["field"])
        tenor_months = int(spec["tenor_months"])
        value = values.get((ric, field))
        if value is None:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.UNKNOWN_INSTRUMENT, scope.value),
                internal_detail=(
                    f"RIC {ric!r} field {field!r} missing from RDP response for {scope.value}"
                ),
            )
        try:
            value_percent = Decimal(str(value))
        except InvalidOperation:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.UNKNOWN_INSTRUMENT, scope.value),
                internal_detail=(
                    f"RIC {ric!r} field {field!r} returned non-numeric value {value!r} "
                    f"for {scope.value}"
                ),
            ) from None
        observations.append(
            CurveObservation(
                ric=ric, field=field, tenor_months=tenor_months, value_percent=value_percent
            )
        )
    return payload, tuple(observations)
