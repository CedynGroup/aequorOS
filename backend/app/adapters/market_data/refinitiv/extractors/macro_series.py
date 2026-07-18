"""Macro-forecast extractor: RDP forecast rows -> :class:`MacroObservation`.

Macro forecast scopes (``MACRO_{COUNTRY}_{INDICATOR}``) produce canonical
``market_index`` records tagged with scenario and horizon (§5.2). Each catalog
request spec names a RIC + field plus the business context the value carries:
``scenario`` (base / adverse / severely_adverse) and forecast
``horizon_months``. The extractor pulls the numeric value and pairs it with
that context.

Fixture-recorded RDP response shape (see ``transport.py`` for the envelope)::

    {
      "universe": ["GHGDP=ECI"],
      "fields": ["TR.GDPForecast"],
      "data": [["GHGDP=ECI", "TR.GDPForecast", 4.2]]
    }

A requested (RIC, field) pair missing from the response, or carrying a
non-numeric value, classifies as UNKNOWN_INSTRUMENT (§12.1).
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

_DEFAULT_SCENARIO = "base"


@dataclass(frozen=True)
class MacroObservation:
    """One macro forecast point in vendor units, with its scenario/horizon."""

    ric: str
    field: str
    value: Decimal
    scenario: str
    horizon_months: int | None


def extract_macro(
    transport: RdpTransport,
    session_token: str,
    scope: DataScope,
    request_specs: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[MacroObservation, ...]]:
    """Fetch and parse one macro-forecast scope.

    Returns ``(raw_payload, observations)`` — the raw payload is preserved
    verbatim for raw-tier audit storage (§13.3). ``scenario`` defaults to the
    base scenario and ``horizon_months`` to ``None`` when a catalog spec omits
    them.
    """
    payload = transport.fetch(session_token, request_spec_for(scope, request_specs))
    raise_for_vendor_error(payload, scope.value)
    values = {(str(row[0]), str(row[1])): row[2] for row in data_rows(payload, scope.value)}

    observations: list[MacroObservation] = []
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
            numeric = Decimal(str(value))
        except InvalidOperation:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.UNKNOWN_INSTRUMENT, scope.value),
                internal_detail=(
                    f"RIC {ric!r} field {field!r} returned non-numeric value {value!r} "
                    f"for {scope.value}"
                ),
            ) from None
        horizon = spec.get("horizon_months")
        observations.append(
            MacroObservation(
                ric=ric,
                field=field,
                value=numeric,
                scenario=str(spec.get("scenario", _DEFAULT_SCENARIO)),
                horizon_months=int(horizon) if horizon is not None else None,
            )
        )
    return payload, tuple(observations)
