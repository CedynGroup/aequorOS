"""Credit-rating translation: RTG_* mnemonics -> canonical rating records (§6.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.market_data.bloomberg.auth import VENDOR_DISPLAY_NAME
from app.adapters.market_data.errors import (
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)
from app.adapters.market_data.pull_runner import MarketDataBundle, RatingRecord

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from app.adapters.market_data.bloomberg.extractors.credit_data import RatingObservation
    from app.adapters.market_data.scope_taxonomy import DataScope

_CREDIT_RATING_PREFIX = "CREDIT_RATING_"

# Bloomberg long-term local-currency issuer rating mnemonics -> canonical
# agencies (app.domain.ingestion.constants.RATING_AGENCIES).
_AGENCY_BY_FIELD_PREFIX: tuple[tuple[str, str], ...] = (
    ("RTG_MDY_", "moodys"),
    ("RTG_SP_", "sp"),
    ("RTG_FITCH_", "fitch"),
)

_AGENCY_DISPLAY = {"moodys": "Moody's", "sp": "S&P", "fitch": "Fitch"}


def agency_for_field(field: str, scope: DataScope) -> str:
    """The canonical agency a Bloomberg rating mnemonic belongs to."""
    for prefix, agency in _AGENCY_BY_FIELD_PREFIX:
        if field.startswith(prefix):
            return agency
    raise MarketDataError(
        render_bank_facing(
            BankFacingErrorCode.UNKNOWN_INSTRUMENT,
            vendor=VENDOR_DISPLAY_NAME,
            scope=scope.value,
        ),
        internal_detail=f"unmapped rating field mnemonic {field!r}",
    )


def rating_bundle(
    scope: DataScope,
    observations: Sequence[RatingObservation],
    as_of_date: date,
) -> MarketDataBundle:
    """One credit-rating scope's observations as a persistable bundle.

    The issuer is derived from the scope (``CREDIT_RATING_GHANA_SOVEREIGN``
    -> ``GHANA_SOVEREIGN``). §6.2 documents no rating-action-date or
    watch-status mnemonics, and mnemonics are never invented (§16.4):
    ``rating_date`` is the pull's as-of date and ``watch_status`` stays None
    until verified mnemonics land in the catalog. ``source_reference`` is the
    Bloomberg security plus the specific rating field.
    """
    issuer = scope.value.removeprefix(_CREDIT_RATING_PREFIX)
    records = []
    samples: dict[str, str] = {}
    for observation in observations:
        agency = agency_for_field(observation.field, scope)
        records.append(
            RatingRecord(
                issuer=issuer,
                agency=agency,
                rating=observation.rating_text,
                watch_status=None,
                rating_date=as_of_date,
                source_reference=f"{observation.security}/{observation.field}",
            )
        )
        samples[_AGENCY_DISPLAY.get(agency, agency)] = observation.rating_text
    return MarketDataBundle(ratings=records, sample_values=samples)
