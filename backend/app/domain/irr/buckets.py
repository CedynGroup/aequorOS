"""Repricing-bucket assignment for the nine IRRBB buckets (pure).

``app.domain.irr.engine.IRR_BUCKETS`` names the buckets and fixes their
midpoints; it says nothing about WHERE a position's repricing horizon falls,
because the engine receives positions already bucketed. The one thing this
module adds is the upper bound, in days, of each bucket — defined here once —
plus the two assignment rules fact derivation applies with it, lifted verbatim
(P0-8) so the BI plane can bucket a position exactly as the IRR facts do.

The bucket order and the midpoint strings are DERIVED from the engine, never
restated: if the engine ever renames or reorders a bucket, ``REPRICING_BUCKETS``
fails to build rather than silently disagreeing with it.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from app.domain.irr.engine import IRR_BUCKETS

#: Upper bound of each bucket's repricing horizon in days, keyed by the engine's
#: bucket name; ``None`` marks the unbounded long end.
BUCKET_UPPER_DAYS: dict[str, int | None] = {
    "overnight": 1,
    "1-7d": 7,
    "8-30d": 30,
    "1-3m": 91,
    "3-6m": 182,
    "6-12m": 365,
    "1-3y": 1095,
    "3-5y": 1825,
    "5y+": None,
}

#: ``(name, upper bound in days, midpoint years)`` in engine order — the shape
#: fact derivation walks. The midpoint is the engine's Decimal stringified,
#: because that string is what the ``irr_position`` fact carries and hashes.
REPRICING_BUCKETS: tuple[tuple[str, int | None, str], ...] = tuple(
    (name, BUCKET_UPPER_DAYS[name], str(midpoint)) for name, midpoint in IRR_BUCKETS
)


class RepricingInputs(Protocol):
    """The three position fields a repricing horizon is read from."""

    @property
    def rate_type(self) -> str | None: ...

    @property
    def contractual_maturity(self) -> date | None: ...

    @property
    def next_repricing_date(self) -> date | None: ...


def bucket_for_days(days: int) -> str:
    """The bucket whose upper bound is the first at or beyond ``days``."""
    for name, upper, _ in REPRICING_BUCKETS:
        if upper is None or days <= upper:
            return name
    return REPRICING_BUCKETS[-1][0]  # pragma: no cover - the 5y+ bucket is unbounded


def repricing_bucket(row: RepricingInputs, as_of: date) -> str | None:
    """The bucket a position reprices in, or ``None`` when it states no horizon.

    A floating-rate position reprices at its next repricing date; anything else
    at its contractual maturity (falling back to a stated repricing date). A
    horizon already in the past counts as day zero.
    """
    horizon: date | None
    if row.rate_type == "FLOATING" and row.next_repricing_date is not None:
        horizon = row.next_repricing_date
    else:
        horizon = row.contractual_maturity or row.next_repricing_date
    if horizon is None:
        return None
    return bucket_for_days(max((horizon - as_of).days, 0))
