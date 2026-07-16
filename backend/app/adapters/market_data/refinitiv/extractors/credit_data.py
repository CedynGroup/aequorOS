"""Credit-rating extractor: RDP issuer-rating rows -> :class:`RatingObservation`.

Fixture-recorded RDP response shape (see ``transport.py`` for the envelope)::

    {
      "universe": ["GH="],
      "fields": ["TR.MoodysIssuerRating", "TR.SPIssuerRating", "TR.FitchIssuerRating"],
      "data": [
        ["GH=", "TR.MoodysIssuerRating", "Caa1", "stable", "2026-03-27"],
        ["GH=", "TR.SPIssuerRating", "CCC+", "positive", "2026-05-15"]
      ]
    }

Credit rows carry two extra columns beyond the standard ``[ric, field,
value]`` triplet: the agency watch status and the agency's rating action
date (ISO 8601). Both are optional in recorded responses — absent or
unparsable values surface as ``None`` and the translator applies fallbacks.

A requested (RIC, field) pair missing from the response classifies as
UNKNOWN_INSTRUMENT (§12.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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

_WATCH_COLUMN = 3
_RATING_DATE_COLUMN = 4


@dataclass(frozen=True)
class RatingObservation:
    """One agency's issuer rating, still in vendor vocabulary."""

    ric: str
    field: str
    rating: str
    watch_status: str | None
    rating_date: date | None


def _optional_str(row: list[Any], index: int) -> str | None:
    if len(row) <= index or row[index] is None:
        return None
    value = str(row[index]).strip()
    return value or None


def _optional_date(row: list[Any], index: int) -> date | None:
    raw = _optional_str(row, index)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def extract_ratings(
    transport: RdpTransport,
    session_token: str,
    scope: DataScope,
    request_specs: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[RatingObservation, ...]]:
    """Fetch and parse one credit-rating scope.

    Returns ``(raw_payload, observations)`` — the raw payload is preserved
    verbatim for raw-tier audit storage (§13.3).
    """
    payload = transport.fetch(session_token, request_spec_for(scope, request_specs))
    raise_for_vendor_error(payload, scope.value)
    rows = {(str(row[0]), str(row[1])): row for row in data_rows(payload, scope.value)}

    observations: list[RatingObservation] = []
    for spec in request_specs:
        ric = str(spec["ric"])
        field = str(spec["field"])
        row = rows.get((ric, field))
        if row is None:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.UNKNOWN_INSTRUMENT, scope.value),
                internal_detail=(
                    f"RIC {ric!r} field {field!r} missing from RDP response for {scope.value}"
                ),
            )
        rating = str(row[2]).strip()
        if not rating:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.UNKNOWN_INSTRUMENT, scope.value),
                internal_detail=(
                    f"RIC {ric!r} field {field!r} returned an empty rating for {scope.value}"
                ),
            )
        observations.append(
            RatingObservation(
                ric=ric,
                field=field,
                rating=rating,
                watch_status=_optional_str(row, _WATCH_COLUMN),
                rating_date=_optional_date(row, _RATING_DATE_COLUMN),
            )
        )
    return payload, tuple(observations)
