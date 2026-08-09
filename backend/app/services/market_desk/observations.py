"""Desk observations: manual entry and reads (spec §3 "manual-entry fallback").

Every Ghana Tier-1 source needs a manual-entry fallback because BoG publishes
HTML/PDF only and blocks automated access (spec §14). A manual entry is a
first-class observation with ``entered_by`` provenance and no capture link;
corrections are append-only — a new row supersedes the current generation for
the same (series_code, as_of_date), mirroring the canonical-store idiom.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.core.ids import new_uuid7
from app.models import DeskObservation, DeskSourceCapture

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal

    from sqlalchemy.orm import Session


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


def list_observations(
    db: Session,
    *,
    series_code: str | None = None,
    as_of_date: date | None = None,
    include_superseded: bool = False,
) -> list[DeskObservation]:
    query = select(DeskObservation).order_by(
        DeskObservation.series_code, DeskObservation.as_of_date.desc()
    )
    if series_code is not None:
        query = query.where(DeskObservation.series_code == series_code)
    if as_of_date is not None:
        query = query.where(DeskObservation.as_of_date == as_of_date)
    if not include_superseded:
        query = query.where(DeskObservation.superseded_by.is_(None))
    return list(db.scalars(query))


def list_captures(
    db: Session, *, source_key: str | None = None
) -> list[DeskSourceCapture]:
    query = select(DeskSourceCapture).order_by(DeskSourceCapture.captured_at.desc())
    if source_key is not None:
        query = query.where(DeskSourceCapture.source_key == source_key)
    return list(db.scalars(query))
