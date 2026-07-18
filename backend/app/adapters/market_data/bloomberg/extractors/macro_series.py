"""Macro-forecast extractor: catalog fields -> typed forecast observations (§6.3).

Macro forecast scopes (``MACRO_{COUNTRY}_{INDICATOR}``) produce canonical
``market_index`` records tagged with scenario and horizon (§5.2). Each catalog
request spec names a Bloomberg security + field plus the business context the
value carries — the ``scenario`` (base / adverse / severely_adverse) and the
forecast ``horizon_months``. The extractor pulls the numeric value and pairs
it with that context; the translator maps it to a canonical index record.

Response shape is the standard reference-data envelope (see
``extractors/__init__``): ``securityData[*].fieldData[field]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.adapters.market_data.bloomberg.auth import ensure_scope_permitted
from app.adapters.market_data.bloomberg.extractors import (
    build_request_spec,
    numeric_field_value,
    require_field,
    security_field_data,
)

if TYPE_CHECKING:
    from app.adapters.market_data.bloomberg.auth import BloombergSession
    from app.adapters.market_data.bloomberg.transport import BlpTransport
    from app.adapters.market_data.scope_taxonomy import DataScope

_DEFAULT_SCENARIO = "base"


@dataclass(frozen=True)
class MacroObservation:
    """One macro forecast point as Bloomberg returned it, with its context."""

    security: str
    field: str
    value: Decimal
    scenario: str
    horizon_months: int | None


def extract_macro(
    session: BloombergSession,
    transport: BlpTransport,
    scope: DataScope,
    requests: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[MacroObservation, ...]]:
    """Pull every catalog field for one macro-forecast scope.

    Returns ``(raw_response, observations)``; the raw response is preserved
    verbatim for raw-tier audit storage (§13.3). ``scenario`` defaults to the
    base scenario and ``horizon_months`` to ``None`` (a point forecast) when a
    catalog spec omits them.
    """
    ensure_scope_permitted(session, scope)
    raw = transport.request(session, build_request_spec(scope, requests))
    by_security = security_field_data(raw, scope)
    observations: list[MacroObservation] = []
    for request in requests:
        security = str(request["security"])
        field = str(request["field"])
        value = require_field(by_security, security, field, scope)
        horizon = request.get("horizon_months")
        observations.append(
            MacroObservation(
                security=security,
                field=field,
                value=numeric_field_value(value, security, field, scope),
                scenario=str(request.get("scenario", _DEFAULT_SCENARIO)),
                horizon_months=int(horizon) if horizon is not None else None,
            )
        )
    return raw, tuple(observations)
