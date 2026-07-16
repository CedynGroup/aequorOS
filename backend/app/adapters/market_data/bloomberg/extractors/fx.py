"""FX-spot extractor: one catalog security -> one price observation (§6.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.adapters.market_data.bloomberg.auth import (
    VENDOR_DISPLAY_NAME,
    ensure_scope_permitted,
)
from app.adapters.market_data.bloomberg.extractors import (
    build_request_spec,
    numeric_field_value,
    require_field,
    security_field_data,
)
from app.adapters.market_data.errors import (
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from app.adapters.market_data.bloomberg.auth import BloombergSession
    from app.adapters.market_data.bloomberg.transport import BlpTransport
    from app.adapters.market_data.scope_taxonomy import DataScope


@dataclass(frozen=True)
class FxObservation:
    """One FX spot price as Bloomberg returned it (a price, not a percent)."""

    security: str
    field: str
    value: Decimal


@dataclass(frozen=True)
class FxExtraction:
    """Raw vendor response plus the typed observation extracted from it."""

    raw_response: dict[str, Any]
    observation: FxObservation


def extract_fx_spot(
    session: BloombergSession,
    transport: BlpTransport,
    scope: DataScope,
    requests: list[dict[str, Any]],
) -> FxExtraction:
    """Pull the single catalog security for one FX-spot scope."""
    ensure_scope_permitted(session, scope)
    if len(requests) != 1:
        # A malformed catalog entry, not a vendor fault; classified generically
        # so nothing internal leaks while the catalog bug is fixed.
        raise MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.VENDOR_UNAVAILABLE,
                vendor=VENDOR_DISPLAY_NAME,
                timestamp="not available",
            ),
            internal_detail=(
                f"catalog misconfiguration: FX spot scope {scope.value} must map to exactly "
                f"one request, got {len(requests)}"
            ),
        )
    raw = transport.request(session, build_request_spec(scope, requests))
    by_security = security_field_data(raw, scope)
    security = str(requests[0]["security"])
    field = str(requests[0]["field"])
    value = require_field(by_security, security, field, scope)
    return FxExtraction(
        raw_response=raw,
        observation=FxObservation(
            security=security,
            field=field,
            value=numeric_field_value(value, security, field, scope),
        ),
    )
