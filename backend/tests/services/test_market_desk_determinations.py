"""Determinations: snapshot digests, maker-checker, state machine, corrections.

The digest must be value-based and deterministic (the regulatory input_hash
idiom); the state machine must refuse every illegal transition; published
rows are immutable and corrected only by supersession.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.market_desk import determinations, observations, register

COB = date(2026, 8, 7)
ANALYST = "analyst@aequoros.com"
LEAD = "lead@aequoros.com"

SERIES_SEED: tuple[tuple[str, str, str], ...] = (
    ("GHS.MPR", "2026-07-22", "15.00"),
    ("GHS.INTERBANK.ON", "2026-08-06", "13.30"),
    ("GHS.TBILL.91.DISCOUNT", "2026-08-07", "24.10"),
    ("GHS.TBILL.91.YIELD", "2026-08-07", "25.62"),
    ("GHS.TBILL.182.DISCOUNT", "2026-08-07", "24.60"),
    ("GHS.USDGHS.MID", "2026-08-07", "12.8500"),
)


def _approve_methodology(db: Session) -> None:
    register.ensure_default_methodology(db)
    register.approve_version(
        db,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        approved_by=LEAD,
        effective_from=date(2026, 1, 1),
    )


def _seed_observations(db: Session) -> None:
    for series_code, as_of, value in SERIES_SEED:
        observations.record_manual_observation(
            db,
            series_code=series_code,
            as_of_date=date.fromisoformat(as_of),
            value=Decimal(value),
            unit="pct",
            entered_by=ANALYST,
        )


@pytest.fixture
def desk(db_session: Session) -> Session:
    _approve_methodology(db_session)
    _seed_observations(db_session)
    return db_session


# -- snapshot + digest ---------------------------------------------------------------


def test_input_snapshot_digest_is_deterministic(desk: Session) -> None:
    first = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    second = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    assert first.input_snapshot == second.input_snapshot
    assert first.input_digest == second.input_digest
    assert len(first.input_digest) == 64  # sha256 hex
    # The snapshot is the exact observation set: series, as-of, value only.
    assert {entry["series_code"] for entry in first.input_snapshot} == {
        code for code, _, _ in SERIES_SEED
    }
    assert all(
        set(entry) == {"series_code", "as_of_date", "value"} for entry in first.input_snapshot
    )


def test_snapshot_uses_current_generation_only(desk: Session) -> None:
    before = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    # Correct one observation append-only; the old row is superseded.
    observations.record_manual_observation(
        desk,
        series_code="GHS.TBILL.91.YIELD",
        as_of_date=COB,
        value=Decimal("25.75"),
        unit="pct",
        entered_by=LEAD,
    )
    after = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    assert after.input_digest != before.input_digest
    corrected = {
        entry["series_code"]: entry["value"] for entry in after.input_snapshot
    }
    assert corrected["GHS.TBILL.91.YIELD"] == "25.7500000000"


def test_create_draft_requires_an_active_methodology(db_session: Session) -> None:
    _seed_observations(db_session)
    with pytest.raises(HTTPException) as excinfo:
        determinations.create_draft(db_session, cob_date=COB, prepared_by=ANALYST)
    assert excinfo.value.status_code == 409


def test_create_draft_requires_observations(db_session: Session) -> None:
    _approve_methodology(db_session)
    with pytest.raises(HTTPException) as excinfo:
        determinations.create_draft(db_session, cob_date=COB, prepared_by=ANALYST)
    assert excinfo.value.status_code == 409


# -- maker-checker --------------------------------------------------------------------


def test_preparer_cannot_review_own_determination(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    determinations.submit_for_review(desk, draft.id)
    with pytest.raises(HTTPException) as excinfo:
        determinations.approve(desk, draft.id, reviewed_by=ANALYST)
    assert excinfo.value.status_code == 422
    with pytest.raises(HTTPException) as excinfo:
        determinations.reject(desk, draft.id, reviewed_by=ANALYST, reason="self-review")
    assert excinfo.value.status_code == 422
    assert determinations.get(desk, draft.id).status == "pending_review"


def test_checker_approval_and_rejection(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    determinations.submit_for_review(desk, draft.id)
    approved = determinations.approve(desk, draft.id, reviewed_by=LEAD)
    assert approved.status == "approved"
    assert approved.reviewed_by == LEAD

    other = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    determinations.submit_for_review(desk, other.id)
    rejected = determinations.reject(
        desk, other.id, reviewed_by=LEAD, reason="stale interbank print"
    )
    assert rejected.status == "rejected"
    assert rejected.review_note == "stale interbank print"


def test_rejection_requires_a_reason(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    determinations.submit_for_review(desk, draft.id)
    with pytest.raises(HTTPException) as excinfo:
        determinations.reject(desk, draft.id, reviewed_by=LEAD, reason="   ")
    assert excinfo.value.status_code == 422


# -- state machine ----------------------------------------------------------------------


def test_illegal_transitions_are_rejected(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)

    # Approving or rejecting a draft (never submitted) is illegal.
    for action in (
        lambda: determinations.approve(desk, draft.id, reviewed_by=LEAD),
        lambda: determinations.reject(desk, draft.id, reviewed_by=LEAD, reason="x"),
    ):
        with pytest.raises(HTTPException) as excinfo:
            action()
        assert excinfo.value.status_code == 409

    determinations.submit_for_review(desk, draft.id)
    # Double-submit is illegal.
    with pytest.raises(HTTPException) as excinfo:
        determinations.submit_for_review(desk, draft.id)
    assert excinfo.value.status_code == 409
    # Results are frozen once submitted: what the checker reviews is what ships.
    with pytest.raises(HTTPException) as excinfo:
        determinations.set_results(desk, draft.id, derived_values={"x": 1}, qa_results={})
    assert excinfo.value.status_code == 409

    determinations.approve(desk, draft.id, reviewed_by=LEAD)
    # Re-approving an approved determination is illegal.
    with pytest.raises(HTTPException) as excinfo:
        determinations.approve(desk, draft.id, reviewed_by=LEAD)
    assert excinfo.value.status_code == 409
    # Rejecting an approved determination is illegal.
    with pytest.raises(HTTPException) as excinfo:
        determinations.reject(desk, draft.id, reviewed_by=LEAD, reason="late")
    assert excinfo.value.status_code == 409


def test_set_results_only_on_draft(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    updated = determinations.set_results(
        desk,
        draft.id,
        derived_values={"reference_rates": {"GHS.MPR": 15.0}},
        qa_results={"forward_positivity": "pass"},
    )
    assert updated.derived_values["reference_rates"]["GHS.MPR"] == 15.0
    assert updated.qa_results == {"forward_positivity": "pass"}


# -- corrections (supersession chain) ------------------------------------------------------


def test_correction_supersedes_published_determination(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    determinations.submit_for_review(desk, draft.id)
    determinations.approve(desk, draft.id, reviewed_by=LEAD)
    determinations.mark_published(desk, determinations.get(desk, draft.id))

    published = determinations.get(desk, draft.id)
    assert published.status == "published"
    assert published.published_at is not None

    correction = determinations.supersede(desk, published.id, prepared_by=ANALYST)
    assert correction.id != published.id
    assert correction.status == "draft"
    assert correction.supersedes_id == published.id
    assert correction.cob_date == published.cob_date
    # The published original is untouched — corrections never rewrite history.
    assert determinations.get(desk, published.id).status == "published"


def test_supersede_requires_published_status(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    with pytest.raises(HTTPException) as excinfo:
        determinations.supersede(desk, draft.id, prepared_by=ANALYST)
    assert excinfo.value.status_code == 409
