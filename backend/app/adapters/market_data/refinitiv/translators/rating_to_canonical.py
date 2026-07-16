"""Rating translator: RDP issuer ratings -> canonical rating records.

Agency mapping is fixed by the §7.2 catalog fields:
``TR.MoodysIssuerRating`` -> ``moodys``, ``TR.SPIssuerRating`` -> ``sp``,
``TR.FitchIssuerRating`` -> ``fitch``. Bundle warnings stay vendor-free
(§12.3): vendor field mnemonics and raw values go to the internal log only.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

from app.adapters.market_data.pull_runner import MarketDataBundle, RatingRecord
from app.adapters.market_data.refinitiv.extractors.credit_data import RatingObservation
from app.adapters.market_data.scope_taxonomy import DataScope

logger = logging.getLogger(__name__)

_SCOPE_PREFIX = "CREDIT_RATING_"

AGENCY_BY_FIELD: dict[str, str] = {
    "TR.MoodysIssuerRating": "moodys",
    "TR.SPIssuerRating": "sp",
    "TR.FitchIssuerRating": "fitch",
}
AGENCY_LABELS: dict[str, str] = {"moodys": "Moody's", "sp": "S&P", "fitch": "Fitch"}

# Canonical watch statuses per the counterparty-rating CHECK constraint.
VALID_WATCH_STATUSES = frozenset({"positive", "negative", "stable", "developing"})


def _issuer_label(issuer: str) -> str:
    return issuer.replace("_", " ").capitalize()


def ratings_to_bundle(
    scope: DataScope,
    observations: Sequence[RatingObservation],
    default_rating_date: date,
) -> MarketDataBundle:
    """Translate one credit-rating scope's observations into a bundle.

    ``default_rating_date`` (the pull's business date) backfills observations
    whose recorded response carried no agency action date. Observations for
    unrecognized rating fields are skipped with a vendor-free warning.
    """
    issuer = scope.value.removeprefix(_SCOPE_PREFIX)
    issuer_label = _issuer_label(issuer)
    bundle = MarketDataBundle()

    for observation in observations:
        agency = AGENCY_BY_FIELD.get(observation.field)
        if agency is None:
            logger.warning(
                "Unrecognized Refinitiv rating field %r for %s; observation skipped",
                observation.field,
                scope.value,
            )
            bundle.warnings.append(
                f"one rating for {issuer_label} was not recognized and was skipped"
            )
            continue

        watch_status = observation.watch_status.lower() if observation.watch_status else None
        if watch_status is not None and watch_status not in VALID_WATCH_STATUSES:
            logger.warning(
                "Unrecognized watch status %r on %s/%s; recorded without watch status",
                observation.watch_status,
                scope.value,
                agency,
            )
            bundle.warnings.append(
                f"watch status for {issuer_label} ({AGENCY_LABELS[agency]}) was not "
                "recognized and was omitted"
            )
            watch_status = None

        bundle.ratings.append(
            RatingRecord(
                issuer=issuer,
                agency=agency,
                rating=observation.rating,
                watch_status=watch_status,
                rating_date=observation.rating_date or default_rating_date,
                source_reference=observation.ric,
            )
        )
        bundle.sample_values[f"{issuer_label} ({AGENCY_LABELS[agency]})"] = observation.rating
    return bundle
