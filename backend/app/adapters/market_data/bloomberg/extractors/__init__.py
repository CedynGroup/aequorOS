"""Bloomberg extractors (market_data_adapter.md §6.3).

One extractor per supported scope category. Extractors take an authenticated
session, look up the catalog request specification, make the vendor call
through the transport seam, and return typed intermediate structures. They do
NOT translate to canonical (translators' job, §6.4) and do NOT persist
(the shared pull runner's job).

Assumed recorded response shape — the Bloomberg reference-data service shape
as recorded to fixtures (§6.5)::

    {
      "securityData": [
        {"security": "GHGGB1M Index", "fieldData": {"PX_LAST": 15.80}},
        {"security": "BAD Index",
         "securityError": {"category": "BAD_SEC", "message": "..."}}
      ]
    }

A ``securityError`` entry classifies as UNKNOWN_INSTRUMENT; a response missing
``securityData`` classifies as VENDOR_UNAVAILABLE. Raw vendor text travels
only in ``MarketDataError.internal_detail`` (§12.3).
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from app.adapters.market_data.bloomberg.auth import VENDOR_DISPLAY_NAME
from app.adapters.market_data.errors import (
    BankFacingError,
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)

if TYPE_CHECKING:
    from app.adapters.market_data.scope_taxonomy import DataScope

_NO_CACHE_TIMESTAMP = "not available"


def build_request_spec(scope: DataScope, requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize catalog request specs into one reference-data request."""
    securities = sorted({str(request["security"]) for request in requests})
    fields = sorted({str(request["field"]) for request in requests})
    return {
        "scope": scope.value,
        "request_type": "ReferenceDataRequest",
        "securities": securities,
        "fields": fields,
    }


def security_field_data(raw: dict[str, Any], scope: DataScope) -> dict[str, dict[str, Any]]:
    """Index ``fieldData`` by security, classifying vendor error shapes."""
    security_data = raw.get("securityData")
    if not isinstance(security_data, list):
        raise MarketDataError(
            vendor_unavailable_error(),
            internal_detail=(
                f"malformed reference-data response for {scope.value}: missing securityData"
            ),
        )
    indexed: dict[str, dict[str, Any]] = {}
    for item in security_data:
        if not isinstance(item, dict):
            raise MarketDataError(
                vendor_unavailable_error(),
                internal_detail=(
                    f"malformed securityData item for {scope.value}: {type(item).__name__}"
                ),
            )
        security = str(item.get("security", ""))
        error = item.get("securityError")
        if error is not None:
            raise MarketDataError(
                unknown_instrument_error(scope),
                internal_detail=(
                    f"securityError for {security!r}: {json.dumps(error, default=str)}"
                ),
            )
        field_data = item.get("fieldData")
        if not isinstance(field_data, dict):
            raise MarketDataError(
                unknown_instrument_error(scope),
                internal_detail=f"security {security!r} returned no fieldData",
            )
        indexed[security] = field_data
    return indexed


def require_field(
    by_security: dict[str, dict[str, Any]],
    security: str,
    field: str,
    scope: DataScope,
) -> Any:
    """The vendor value for (security, field), or UNKNOWN_INSTRUMENT."""
    field_data = by_security.get(security)
    if field_data is None:
        raise MarketDataError(
            unknown_instrument_error(scope),
            internal_detail=f"security {security!r} absent from vendor response",
        )
    value = field_data.get(field)
    if value is None:
        raise MarketDataError(
            unknown_instrument_error(scope),
            internal_detail=f"field {field!r} missing for security {security!r}",
        )
    return value


def numeric_field_value(value: Any, security: str, field: str, scope: DataScope) -> Decimal:
    """Coerce a vendor field value to Decimal, or UNKNOWN_INSTRUMENT."""
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise MarketDataError(
            unknown_instrument_error(scope),
            internal_detail=(f"non-numeric value {value!r} for {field!r} on security {security!r}"),
        ) from None


def unknown_instrument_error(scope: DataScope) -> BankFacingError:
    return render_bank_facing(
        BankFacingErrorCode.UNKNOWN_INSTRUMENT,
        vendor=VENDOR_DISPLAY_NAME,
        scope=scope.value,
    )


def vendor_unavailable_error() -> BankFacingError:
    return render_bank_facing(
        BankFacingErrorCode.VENDOR_UNAVAILABLE,
        vendor=VENDOR_DISPLAY_NAME,
        timestamp=_NO_CACHE_TIMESTAMP,
    )
