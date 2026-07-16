"""FX extractor: RDP price rows -> :class:`FxObservation`.

Fixture-recorded RDP response shape (see ``transport.py`` for the envelope)::

    {
      "universe": ["USDGHS=R"],
      "fields": ["TR.MidPrice"],
      "data": [["USDGHS=R", "TR.MidPrice", 12.85]]
    }

``TR.MidPrice`` on ``USDGHS=R`` is quoted as GHS per 1 USD (~12.85); the FX
translator maps it onto the canonical base/quote convention. A requested RIC
missing from the response, or carrying a non-numeric value, classifies as
UNKNOWN_INSTRUMENT (§12.1).
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
class FxObservation:
    """One observed FX price in vendor quoting convention."""

    ric: str
    field: str
    value: Decimal


def extract_fx(
    transport: RdpTransport,
    session_token: str,
    scope: DataScope,
    request_specs: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[FxObservation, ...]]:
    """Fetch and parse one FX scope.

    Returns ``(raw_payload, observations)`` — the raw payload is preserved
    verbatim for raw-tier audit storage (§13.3).
    """
    payload = transport.fetch(session_token, request_spec_for(scope, request_specs))
    raise_for_vendor_error(payload, scope.value)
    values = {(str(row[0]), str(row[1])): row[2] for row in data_rows(payload, scope.value)}

    observations: list[FxObservation] = []
    for spec in request_specs:
        ric = str(spec["ric"])
        field = str(spec["field"])
        value = values.get((ric, field))
        if value is None:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.UNKNOWN_INSTRUMENT, scope.value),
                internal_detail=(
                    f"RIC {ric!r} field {field!r} missing from RDP response for {scope.value}"
                ),
            )
        try:
            price = Decimal(str(value))
        except InvalidOperation:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.UNKNOWN_INSTRUMENT, scope.value),
                internal_detail=(
                    f"RIC {ric!r} field {field!r} returned non-numeric value {value!r} "
                    f"for {scope.value}"
                ),
            ) from None
        observations.append(FxObservation(ric=ric, field=field, value=price))
    return payload, tuple(observations)
