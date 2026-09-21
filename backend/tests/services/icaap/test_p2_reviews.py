"""Independent review, the challenge log, and the supervisor's own add-ons.

Three separations are under test, and each of them is the point of its table:
the reviewer must not be a preparer, the challenge record must not be editable
after the fact, and the person who records a supervisory letter must not be the
person who confirms it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.models import User
from app.schemas.icaap import IcaapCycleRead
from app.schemas.icaap_risk_capital import (
    IcaapAuditFindingWrite,
    IcaapAuditReviewCreate,
    IcaapAuditReviewUpdate,
    IcaapChallengeCreate,
    IcaapChallengeResponseCreate,
    IcaapReason,
    IcaapRiskPut,
)
from app.services.icaap import audit_reviews, challenges, participants, risks, supervisory_addons
from tests.api.helpers import ORG_1
from tests.services.icaap.test_attachments import pdf_bytes
from tests.storage.inmemory import InMemoryStorageClient

REVIEWER = uuid4()
CHECKER = uuid4()


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


def _person(db: Session, access: IcaapAccess, user_id, email: str) -> IcaapAccess:
    name = email.partition("@")[0]
    db.add(User(id=user_id, organization_id=ORG_1, email=email, display_name=name))
    db.commit()
    return IcaapAccess(
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=user_id, authorization_version=1),
        bank=access.bank,
    )


@pytest.fixture
def reviewer(canonical_book: Session, access: IcaapAccess) -> IcaapAccess:
    """Somebody who has touched nothing in this cycle."""
    return _person(canonical_book, access, REVIEWER, "internal.audit@example.test")


def _review(**overrides: Any) -> IcaapAuditReviewCreate:
    payload: dict[str, Any] = {
        "review_kind": "internal_audit",
        "reviewer_function": "Internal Audit",
        "scope": "The ICAAP methodology, its governance and its figures.",
        "frequency_statement": "Reviewed annually as part of the audit plan.",
        "performed_on": date(2026, 2, 28),
        "overall_opinion": "satisfactory_with_findings",
        "independence_statement": "Internal Audit took no part in preparing this ICAAP.",
        "reason": "Record the annual independent review.",
    }
    payload.update(overrides)
    return IcaapAuditReviewCreate.model_validate(payload)


# --- independent review ----------------------------------------------------


def test_creating_the_cycle_already_makes_somebody_a_participant(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    cycle_row = _cycle_row(canonical_book, access, cycle)
    assert access.ctx.actor_user_id in participants.cycle_participants(
        canonical_book, access, cycle_row
    )


def _cycle_row(db: Session, access: IcaapAccess, cycle: IcaapCycleRead):
    from app.services.icaap import guards  # noqa: PLC0415

    return guards.get_cycle_or_404(db, access, cycle.id)


def test_a_preparer_cannot_record_the_review_of_their_own_work(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    risks.put_risk(
        canonical_book,
        access,
        cycle.id,
        "credit",
        IcaapRiskPut(
            likelihood_score=4,
            impact_score=3,
            materiality_rationale="Concentrated book.",
            reason="Score the risk.",
        ),
    )
    with pytest.raises(HTTPException) as caught:
        audit_reviews.create_review(canonical_book, access, cycle.id, _review())
    body = _detail(caught)
    assert body["error_code"] == "reviewer_not_independent"
    assert "risk_assessed" in body["reasons"]


def test_somebody_outside_the_preparation_may_record_it(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    reviewer: IcaapAccess,
) -> None:
    created = audit_reviews.create_review(canonical_book, reviewer, cycle.id, _review())
    assert created.status == "draft"
    assert created.recorded_by == REVIEWER


def test_finalising_seals_the_review_against_further_editing(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    reviewer: IcaapAccess,
) -> None:
    created = audit_reviews.create_review(
        canonical_book,
        reviewer,
        cycle.id,
        _review(
            findings=[
                IcaapAuditFindingWrite(
                    ref="IA-1",
                    severity="medium",
                    finding="The concentration methodology is not documented.",
                    status="open",
                )
            ]
        ),
    )
    finalised = audit_reviews.finalise_review(
        canonical_book, reviewer, cycle.id, created.id, IcaapReason(reason="Report issued.")
    )
    assert finalised.status == "finalised"
    assert finalised.open_findings_count == 1

    with pytest.raises(HTTPException) as caught:
        audit_reviews.update_review(
            canonical_book,
            reviewer,
            cycle.id,
            created.id,
            IcaapAuditReviewUpdate(
                base_rev=finalised.row_rev,
                overall_opinion="satisfactory",
                reason="Soften the opinion.",
            ),
        )
    assert _detail(caught)["error_code"] == "review_sealed"


def test_a_correction_is_a_superseding_review_leaving_the_original_intact(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    reviewer: IcaapAccess,
) -> None:
    first = audit_reviews.create_review(canonical_book, reviewer, cycle.id, _review())
    audit_reviews.finalise_review(
        canonical_book, reviewer, cycle.id, first.id, IcaapReason(reason="Issued.")
    )
    second = audit_reviews.create_review(
        canonical_book,
        reviewer,
        cycle.id,
        _review(supersedes_review_id=first.id, overall_opinion="satisfactory"),
    )
    audit_reviews.finalise_review(
        canonical_book, reviewer, cycle.id, second.id, IcaapReason(reason="Corrected.")
    )
    listing = audit_reviews.list_reviews(canonical_book, reviewer, cycle.id)
    by_id = {review.id: review for review in listing.reviews}
    assert by_id[first.id].status == "superseded"
    assert by_id[first.id].overall_opinion == "satisfactory_with_findings"
    assert by_id[second.id].status == "finalised"


# --- challenge log ---------------------------------------------------------


def _challenge(**overrides: Any) -> IcaapChallengeCreate:
    payload: dict[str, Any] = {
        "raised_in": "board_risk_committee",
        "raised_by_name": "A. Mensah, Chair",
        "raised_on": date(2026, 2, 12),
        "target_kind": "pillar2_item",
        "target_ref": "irrbb",
        "challenge_text": "Why does the interest rate add-on ignore the 450bp shocks?",
        "severity": "high",
    }
    payload.update(overrides)
    return IcaapChallengeCreate.model_validate(payload)


def test_a_challenge_stays_open_until_it_is_answered(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    raised = challenges.raise_challenge(canonical_book, access, cycle.id, _challenge())
    assert raised.challenge_no == 1
    assert raised.open is True

    answered = challenges.respond(
        canonical_book,
        access,
        cycle.id,
        raised.id,
        IcaapChallengeResponseCreate(
            outcome="accepted_changed",
            response_text="The shock set now includes both 450bp scenarios.",
            responder_function="Chief Risk Officer",
        ),
    )
    assert answered.open is False
    assert answered.responses[0].response_no == 1


def test_a_deferred_response_leaves_the_challenge_open(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """ "We answered it" and "we put it off" must not read the same."""
    raised = challenges.raise_challenge(canonical_book, access, cycle.id, _challenge())
    answered = challenges.respond(
        canonical_book,
        access,
        cycle.id,
        raised.id,
        IcaapChallengeResponseCreate(
            outcome="deferred",
            response_text="Deferred to the next cycle pending the standardised framework.",
            responder_function="Chief Risk Officer",
        ),
    )
    assert answered.open is True


def test_challenges_are_numbered_per_cycle_and_board_ones_are_counted(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    challenges.raise_challenge(canonical_book, access, cycle.id, _challenge())
    challenges.raise_challenge(
        canonical_book,
        access,
        cycle.id,
        _challenge(raised_in="senior_management", challenge_text="Is the appetite realistic?"),
    )
    listing = challenges.list_challenges(canonical_book, access, cycle.id)
    assert [entry.challenge_no for entry in listing.challenges] == [1, 2]
    assert listing.board_challenge_count == 1
    assert listing.open_challenge_count == 2


# --- supervisory add-ons ---------------------------------------------------


@pytest.fixture
def storage() -> InMemoryStorageClient:
    return InMemoryStorageClient()


def _record(db: Session, access: IcaapAccess, storage: InMemoryStorageClient, **overrides: Any):
    payload: dict[str, Any] = {
        "letter_reference": "BOG/BSD/2026/014",
        "letter_date": date(2026, 1, 20),
        "effective_from": date(2026, 1, 31),
        "applies_to_basis": "both",
        "basis": "pct_total_rwa",
        "basis_value": Decimal("2"),
        "table5_row": "credit_concentration",
        "component_key": None,
        "description": "Additional capital for single-name concentration.",
        "filename": "letter.pdf",
        "content": pdf_bytes("supervisory"),
        "supersedes_addon_id": None,
    }
    payload.update(overrides)
    return supervisory_addons.create_addon(db, access, storage, **payload)


def test_a_recorded_letter_starts_as_a_draft_and_is_not_yet_in_force(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    created = _record(canonical_book, access, storage)
    assert created.status == "draft"
    assert created.letter_media_type == "application/pdf"
    cycle_row = _cycle_row(canonical_book, access, cycle)
    assert supervisory_addons.active_for(canonical_book, access, cycle_row) == []


def test_the_person_who_recorded_a_letter_cannot_confirm_it(
    canonical_book: Session, access: IcaapAccess, storage: InMemoryStorageClient
) -> None:
    created = _record(canonical_book, access, storage)
    with pytest.raises(HTTPException) as caught:
        supervisory_addons.confirm_addon(
            canonical_book, access, created.id, IcaapReason(reason="Confirm my own.")
        )
    assert _detail(caught)["error_code"] == "self_approval"


def test_a_confirmed_letter_is_in_force_from_its_own_date(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    checker = _person(canonical_book, access, CHECKER, "addon.checker@example.test")
    created = _record(canonical_book, access, storage, effective_from=date(2025, 6, 30))
    confirmed = supervisory_addons.confirm_addon(
        canonical_book, checker, created.id, IcaapReason(reason="Letter verified.")
    )
    assert confirmed.status == "active"
    assert confirmed.confirmed_by == CHECKER
    cycle_row = _cycle_row(canonical_book, access, cycle)
    in_force = supervisory_addons.active_for(canonical_book, access, cycle_row)
    assert [entry.id for entry in in_force] == [created.id]


def test_an_add_on_that_takes_effect_after_the_as_of_date_is_not_in_force(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    checker = _person(canonical_book, access, CHECKER, "addon.checker@example.test")
    created = _record(canonical_book, access, storage, effective_from=date(2026, 6, 30))
    supervisory_addons.confirm_addon(
        canonical_book, checker, created.id, IcaapReason(reason="Letter verified.")
    )
    cycle_row = _cycle_row(canonical_book, access, cycle)
    assert supervisory_addons.active_for(canonical_book, access, cycle_row) == []


def test_a_superseding_letter_closes_the_one_it_replaces(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    storage: InMemoryStorageClient,
) -> None:
    checker = _person(canonical_book, access, CHECKER, "addon.checker@example.test")
    first = _record(canonical_book, access, storage, effective_from=date(2025, 6, 30))
    supervisory_addons.confirm_addon(
        canonical_book, checker, first.id, IcaapReason(reason="Verified.")
    )
    second = _record(
        canonical_book,
        access,
        storage,
        letter_reference="BOG/BSD/2026/031",
        effective_from=date(2025, 9, 30),
        basis_value=Decimal("3"),
        content=pdf_bytes("superseding"),
        supersedes_addon_id=first.id,
    )
    supervisory_addons.confirm_addon(
        canonical_book, checker, second.id, IcaapReason(reason="Replacement verified.")
    )
    listing = supervisory_addons.list_addons(canonical_book, access, include_inactive=True)
    by_id = {entry.id: entry for entry in listing.addons}
    assert by_id[first.id].status == "superseded"
    assert by_id[first.id].effective_to == date(2025, 9, 30)
    assert by_id[second.id].status == "active"
    cycle_row = _cycle_row(canonical_book, access, cycle)
    assert [
        entry.id for entry in supervisory_addons.active_for(canonical_book, access, cycle_row)
    ] == [second.id]


def test_an_add_on_is_never_publishable(
    canonical_book: Session, access: IcaapAccess, storage: InMemoryStorageClient
) -> None:
    """D-023 / M16: the regulator's private instruction is not the bank's to disclose."""
    from app.domain.icaap.blocks import BLOCK_CATALOGUE  # noqa: PLC0415

    assert BLOCK_CATALOGUE["supervisory_addons"].never_public is True
    listing = supervisory_addons.list_addons(canonical_book, access, include_inactive=True)
    assert listing.never_public is True
