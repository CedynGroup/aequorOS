"""Yield-curve extractor: catalog securities -> per-tenor observations (§6.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.adapters.market_data.bloomberg.auth import ensure_scope_permitted
from app.adapters.market_data.bloomberg.extractors import (
    build_request_spec,
    numeric_field_value,
    require_field,
    security_field_data,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from app.adapters.market_data.bloomberg.auth import BloombergSession
    from app.adapters.market_data.bloomberg.transport import BlpTransport
    from app.adapters.market_data.scope_taxonomy import DataScope


@dataclass(frozen=True)
class CurveFieldObservation:
    """One curve tenor as Bloomberg returned it: a percent yield (15.80)."""

    security: str
    field: str
    tenor_months: int
    value: Decimal


@dataclass(frozen=True)
class CurveExtraction:
    """Raw vendor response plus the typed observations extracted from it."""

    raw_response: dict[str, Any]
    observations: tuple[CurveFieldObservation, ...]


def extract_curve(
    session: BloombergSession,
    transport: BlpTransport,
    scope: DataScope,
    requests: list[dict[str, Any]],
) -> CurveExtraction:
    """Pull every catalog security for one yield-curve scope."""
    ensure_scope_permitted(session, scope)
    raw = transport.request(session, build_request_spec(scope, requests))
    by_security = security_field_data(raw, scope)
    observations = []
    for request in requests:
        security = str(request["security"])
        field = str(request["field"])
        value = require_field(by_security, security, field, scope)
        observations.append(
            CurveFieldObservation(
                security=security,
                field=field,
                tenor_months=int(request["tenor_months"]),
                value=numeric_field_value(value, security, field, scope),
            )
        )
    return CurveExtraction(raw_response=raw, observations=tuple(observations))
