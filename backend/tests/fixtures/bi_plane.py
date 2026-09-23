"""Materialise the BI marts for the canonical test book.

The BI surfaces (Insights, Dashboards, Explore) read the ``bi_*`` marts, which
in the product are written by the ``bi`` worker lane's ``bi_mart_refresh`` job
(``mart_builder.refresh_bank_as_of``) from the canonical book, the live plane
and the sealed runs. The e2e stack runs **no worker** and the hermetic suite
builds its schema with ``create_all``, so — exactly like ``live_plane.py`` for
``current_financial_facts`` — nothing would ever write the marts there, and
every BI page would open on its empty state.

This runs the product's own builder, synchronously, for the bank's latest
snapshot date. It invents nothing: whatever the marts then hold is what the
builder projected from the same rows the rest of the fixture wrote. Run it
AFTER ``materialize_live_plane`` so the engine tier and the R1/R8/R9 checks
have a live plane to read.

Fixture-only: nothing in ``app/`` imports this.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.services.bi import mart_builder
from app.services.bi.mart_builder import BuildOutcome


def materialize_bi_plane(
    session: Session, *, organization_id: str, bank_id: str, as_of: date | None = None
) -> BuildOutcome | None:
    """Build the marts for ``as_of`` (default: the bank's latest snapshot date).

    Returns the builder's outcome, or ``None`` when the bank holds no
    current-generation position snapshots at all (there is nothing to build,
    and saying so beats a fabricated empty build). The caller commits.
    """
    if as_of is None:
        dates = mart_builder.snapshot_dates(session, organization_id, bank_id)
        if not dates:
            return None
        as_of = dates[-1]
    return mart_builder.refresh_bank_as_of(
        session,
        organization_id=organization_id,
        bank_id=bank_id,
        as_of=as_of,
        reason="fixture",
    )
