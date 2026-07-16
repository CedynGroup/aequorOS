"""Credit-rating extractor: rating field mnemonics -> text observations (§6.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.adapters.market_data.bloomberg.auth import ensure_scope_permitted
from app.adapters.market_data.bloomberg.extractors import (
    build_request_spec,
    require_field,
    security_field_data,
    unknown_instrument_error,
)
from app.adapters.market_data.errors import MarketDataError

if TYPE_CHECKING:
    from app.adapters.market_data.bloomberg.auth import BloombergSession
    from app.adapters.market_data.bloomberg.transport import BlpTransport
    from app.adapters.market_data.scope_taxonomy import DataScope


@dataclass(frozen=True)
class RatingObservation:
    """One agency rating as Bloomberg returned it (e.g. ``"Caa1"``)."""

    security: str
    field: str
    rating_text: str


@dataclass(frozen=True)
class RatingExtraction:
    """Raw vendor response plus the typed observations extracted from it."""

    raw_response: dict[str, Any]
    observations: tuple[RatingObservation, ...]


def extract_ratings(
    session: BloombergSession,
    transport: BlpTransport,
    scope: DataScope,
    requests: list[dict[str, Any]],
) -> RatingExtraction:
    """Pull every catalog rating field for one credit-rating scope."""
    ensure_scope_permitted(session, scope)
    raw = transport.request(session, build_request_spec(scope, requests))
    by_security = security_field_data(raw, scope)
    observations = []
    for request in requests:
        security = str(request["security"])
        field = str(request["field"])
        value = require_field(by_security, security, field, scope)
        rating_text = str(value).strip()
        if not rating_text:
            raise MarketDataError(
                unknown_instrument_error(scope),
                internal_detail=f"empty rating value for {field!r} on security {security!r}",
            )
        observations.append(
            RatingObservation(security=security, field=field, rating_text=rating_text)
        )
    return RatingExtraction(raw_response=raw, observations=tuple(observations))
