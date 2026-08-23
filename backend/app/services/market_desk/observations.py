"""Desk observations: manual entry and reads (spec §3 "manual-entry fallback").

Every Ghana Tier-1 source needs a manual-entry fallback because BoG publishes
HTML/PDF only and blocks automated access (spec §14). A manual entry is a
first-class observation with ``entered_by`` provenance and no capture link;
corrections are append-only — a new row supersedes the current generation for
the same (series_code, as_of_date), mirroring the canonical-store idiom.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.core.ids import new_uuid7
from app.models import DeskObservation, DeskSourceCapture

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal

    from sqlalchemy.orm import Session

#: Default and hard-cap page sizes for observation reads. The full table runs to
#: hundreds of thousands of rows (vendor + desk captures over years), so an
#: unbounded read is never legitimate — the service clamps defensively even when
#: called outside the API layer.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


def record_manual_observation(  # noqa: PLR0913 - one call carries the full entry
    db: Session,
    *,
    series_code: str,
    as_of_date: date,
    value: Decimal,
    unit: str,
    entered_by: str,
    attributes: dict[str, Any] | None = None,
    quality_flags: list[Any] | None = None,
) -> DeskObservation:
    """Insert a manual observation, superseding any current-generation row
    for the same (series_code, as_of_date). Append-only: the old row stays.

    The new row's id is minted eagerly and the old rows are flushed OUT of
    the current-generation partial unique index before the new row is
    inserted — the ``pull_runner._supersede`` idiom; inserting first would
    collide with the index.
    """
    row = DeskObservation(
        id=new_uuid7(),
        capture_id=None,
        series_code=series_code,
        as_of_date=as_of_date,
        value=value,
        unit=unit,
        attributes=attributes or {},
        quality_flags=quality_flags or [],
        entered_by=entered_by,
    )
    current = list(
        db.scalars(
            select(DeskObservation).where(
                DeskObservation.series_code == series_code,
                DeskObservation.as_of_date == as_of_date,
                DeskObservation.superseded_by.is_(None),
            )
        )
    )
    for old in current:
        old.superseded_by = row.id
    if current:
        db.flush()
    db.add(row)
    db.flush()
    return row


def _observation_filters(
    *,
    series_code: str | None,
    as_of_from: date | None,
    as_of_to: date | None,
    include_superseded: bool,
) -> list[Any]:
    """Shared WHERE clauses so the page read and its count can never drift.

    ``series_code`` is a PREFIX match (a ledger filters ``GHS.TBILL`` to every
    tenor); ``as_of_from``/``as_of_to`` are an inclusive date range.
    """
    clauses: list[Any] = []
    if series_code is not None:
        clauses.append(DeskObservation.series_code.startswith(series_code, autoescape=True))
    if as_of_from is not None:
        clauses.append(DeskObservation.as_of_date >= as_of_from)
    if as_of_to is not None:
        clauses.append(DeskObservation.as_of_date <= as_of_to)
    if not include_superseded:
        clauses.append(DeskObservation.superseded_by.is_(None))
    return clauses


def list_observations(  # noqa: PLR0913 - one keyword per ledger filter / page bound
    db: Session,
    *,
    series_code: str | None = None,
    as_of_from: date | None = None,
    as_of_to: date | None = None,
    include_superseded: bool = False,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> list[DeskObservation]:
    """Return ONE page of observations, newest ``as_of_date`` first.

    ``limit`` is clamped to ``[1, MAX_PAGE_SIZE]`` and ``offset`` floored at 0
    so the old unbounded full-table read can never be reintroduced through a
    caller that skips the endpoint's ``Query`` validators. The ordering
    (as_of_date desc, created_at desc, id desc) is total and therefore stable
    across pages.
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    query = (
        select(DeskObservation)
        .where(
            *_observation_filters(
                series_code=series_code,
                as_of_from=as_of_from,
                as_of_to=as_of_to,
                include_superseded=include_superseded,
            )
        )
        .order_by(
            DeskObservation.as_of_date.desc(),
            DeskObservation.created_at.desc(),
            DeskObservation.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(query))


def count_observations(
    db: Session,
    *,
    series_code: str | None = None,
    as_of_from: date | None = None,
    as_of_to: date | None = None,
    include_superseded: bool = False,
) -> int:
    """Total rows matching the same filters as ``list_observations`` — a real
    ``SELECT count(*)``, never ``len()`` of a materialized page."""
    query = (
        select(func.count())
        .select_from(DeskObservation)
        .where(
            *_observation_filters(
                series_code=series_code,
                as_of_from=as_of_from,
                as_of_to=as_of_to,
                include_superseded=include_superseded,
            )
        )
    )
    return int(db.scalar(query) or 0)


def list_captures(
    db: Session, *, source_key: str | None = None
) -> list[DeskSourceCapture]:
    query = select(DeskSourceCapture).order_by(DeskSourceCapture.captured_at.desc())
    if source_key is not None:
        query = query.where(DeskSourceCapture.source_key == source_key)
    return list(db.scalars(query))
