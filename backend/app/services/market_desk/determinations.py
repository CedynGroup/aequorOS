"""Track-1 weekly determinations (spec §5, §10): snapshot, digest, dual control.

A determination is one application of the currently-approved methodology to
one COB date. This service owns the parts that are structural NOW — the
input snapshot and its value-based digest, the maker-checker state machine,
and append-only corrections. The derived values / QA results are set by the
CALLER (the calculation pipeline lands in a later phase).

State machine::

    draft -> pending_review -> approved -> published
                            -> rejected
    published -(correction)-> NEW draft with supersedes_id

``input_digest`` mirrors the regulatory ``input_hash`` idiom: value-based,
id-free, canonically sorted JSON — given the methodology version and the
snapshot, every published value is exactly reproducible.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from sqlalchemy import select

from app.db.base import utc_now
from app.models import DeskDetermination, DeskObservation
from app.services.market_desk import register

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import Session

# The configured input series a determination snapshots (spec §5 step 1):
# the GRR-formula inputs (MPR, interbank overnight, 91-day T-bill), the full
# T-bill tenor set for the short end of the sovereign curve, and the USD/GHS
# anchor. Grows with GFIM bond series in the calculation phase.
DEFAULT_INPUT_SERIES: tuple[str, ...] = (
    "GHS.MPR",
    "GHS.INTERBANK.ON",
    "GHS.TBILL.91.DISCOUNT",
    "GHS.TBILL.91.YIELD",
    "GHS.TBILL.182.DISCOUNT",
    "GHS.TBILL.364.DISCOUNT",
    "GHS.USDGHS.MID",
    "GHS.GRR",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def snapshot_digest(snapshot: list[dict[str, str]]) -> str:
    """SHA-256 of the canonical JSON of a snapshot (value-based, id-free)."""
    return hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()


def build_input_snapshot(
    db: Session,
    cob_date: date,
    *,
    series_codes: tuple[str, ...] = DEFAULT_INPUT_SERIES,
) -> list[dict[str, str]]:
    """The exact observation set a determination consumes.

    For each configured series: the CURRENT-generation observation with the
    latest as-of date on or before the COB date (staleness limits are the
    methodology's ``max_staleness_days`` — applied by the calculation
    pipeline, not silently here). Entries carry only ``{series_code,
    as_of_date, value}`` — no row ids, so re-entered-but-identical
    observations produce an identical digest. Sorted by canonical JSON.
    """
    entries: list[dict[str, str]] = []
    for series_code in series_codes:
        observation = db.scalar(
            select(DeskObservation)
            .where(
                DeskObservation.series_code == series_code,
                DeskObservation.as_of_date <= cob_date,
                DeskObservation.superseded_by.is_(None),
            )
            .order_by(DeskObservation.as_of_date.desc())
            .limit(1)
        )
        if observation is None:
            continue
        entries.append(
            {
                "series_code": observation.series_code,
                "as_of_date": observation.as_of_date.isoformat(),
                "value": str(observation.value),
            }
        )
    entries.sort(key=_canonical_json)
    return entries


def _get_or_404(db: Session, determination_id: Any) -> DeskDetermination:
    determination = db.get(DeskDetermination, determination_id)
    if determination is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Determination does not exist.",
        )
    return determination


def _require_status(determination: DeskDetermination, *allowed: str) -> None:
    if determination.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Determination is {determination.status!r}; this action requires "
                f"{' or '.join(repr(a) for a in allowed)}."
            ),
        )


def create_draft(
    db: Session,
    *,
    cob_date: date,
    prepared_by: str,
    methodology_code: str = register.DEFAULT_METHODOLOGY_CODE,
    supersedes: DeskDetermination | None = None,
) -> DeskDetermination:
    """Open a draft determination bound to the ACTIVE methodology version.

    Refuses when no approved methodology is effective for the COB date — a
    determination is by definition an application of an approved methodology
    (spec §5 governing principle).
    """
    methodology = register.get_active(db, methodology_code, cob_date)
    if methodology is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No approved methodology version of {methodology_code!r} is effective "
                f"on {cob_date.isoformat()}; approve one before running a determination."
            ),
        )
    snapshot = build_input_snapshot(db, cob_date)
    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No current-generation desk observations exist on or before "
                f"{cob_date.isoformat()} for the configured series; capture or enter "
                "observations first."
            ),
        )
    determination = DeskDetermination(
        cob_date=cob_date,
        methodology_code=methodology.methodology_code,
        methodology_version=methodology.version,
        input_snapshot=snapshot,
        input_digest=snapshot_digest(snapshot),
        status="draft",
        prepared_by=prepared_by,
        supersedes_id=supersedes.id if supersedes is not None else None,
    )
    db.add(determination)
    db.flush()
    return determination


def set_results(
    db: Session,
    determination_id: Any,
    *,
    derived_values: dict[str, Any],
    qa_results: dict[str, Any],
) -> DeskDetermination:
    """Attach the calculation pipeline's outputs to a DRAFT.

    Only drafts are writable: once submitted, what the checker reviews must
    be what gets approved and published.
    """
    determination = _get_or_404(db, determination_id)
    _require_status(determination, "draft")
    determination.derived_values = derived_values
    determination.qa_results = qa_results
    db.flush()
    return determination


def submit_for_review(db: Session, determination_id: Any) -> DeskDetermination:
    """Maker step complete: 'methodology vX correctly applied to these inputs'."""
    determination = _get_or_404(db, determination_id)
    _require_status(determination, "draft")
    determination.status = "pending_review"
    db.flush()
    return determination


def _require_checker(determination: DeskDetermination, reviewer: str) -> None:
    if reviewer.strip().lower() == determination.prepared_by.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "A determination cannot be reviewed by its preparer "
                "(maker-checker, spec §4)."
            ),
        )


def approve(db: Session, determination_id: Any, *, reviewed_by: str) -> DeskDetermination:
    determination = _get_or_404(db, determination_id)
    _require_status(determination, "pending_review")
    _require_checker(determination, reviewed_by)
    determination.status = "approved"
    determination.reviewed_by = reviewed_by
    db.flush()
    return determination


def reject(
    db: Session, determination_id: Any, *, reviewed_by: str, reason: str
) -> DeskDetermination:
    determination = _get_or_404(db, determination_id)
    _require_status(determination, "pending_review")
    _require_checker(determination, reviewed_by)
    if not reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A rejection requires a non-empty reason.",
        )
    determination.status = "rejected"
    determination.reviewed_by = reviewed_by
    determination.review_note = reason
    db.flush()
    return determination


def supersede(
    db: Session, determination_id: Any, *, prepared_by: str
) -> DeskDetermination:
    """Correction path (spec §10): a PUBLISHED determination is immutable —
    the correction is a NEW draft for the same COB date carrying
    ``supersedes_id``, re-snapshotting the (presumably corrected) current
    observations. It walks the full maker-checker path before it can publish.
    """
    published = _get_or_404(db, determination_id)
    _require_status(published, "published")
    return create_draft(
        db,
        cob_date=published.cob_date,
        prepared_by=prepared_by,
        methodology_code=published.methodology_code,
        supersedes=published,
    )


def mark_published(db: Session, determination: DeskDetermination) -> DeskDetermination:
    """Stamp transaction time. Called by the publication service only."""
    determination.status = "published"
    determination.published_at = utc_now()
    db.flush()
    return determination


def get(db: Session, determination_id: Any) -> DeskDetermination:
    return _get_or_404(db, determination_id)


def list_determinations(
    db: Session, *, cob_date: date | None = None, status_filter: str | None = None
) -> list[DeskDetermination]:
    query = select(DeskDetermination).order_by(
        DeskDetermination.cob_date.desc(), DeskDetermination.created_at.desc()
    )
    if cob_date is not None:
        query = query.where(DeskDetermination.cob_date == cob_date)
    if status_filter is not None:
        query = query.where(DeskDetermination.status == status_filter)
    return list(db.scalars(query))
