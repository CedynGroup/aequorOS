"""Independent review and the Board challenge log."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbSession, IcaapAuditReview, IcaapEdit, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap_risk_capital import (
    IcaapAuditReviewCreate,
    IcaapAuditReviewListRead,
    IcaapAuditReviewRead,
    IcaapAuditReviewUpdate,
    IcaapChallengeCreate,
    IcaapChallengeListRead,
    IcaapChallengeRead,
    IcaapChallengeResponseCreate,
    IcaapReason,
)
from app.services.icaap import audit_reviews, challenges

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_CYCLE = "/banks/{bank_id}/icaap/cycles/{cycle_id}"


@router.get(
    f"{_CYCLE}/audit-reviews",
    response_model=IcaapAuditReviewListRead,
    operation_id="listIcaapAuditReviews",
    responses=_ERRORS,
)
def list_icaap_audit_reviews(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapAuditReviewListRead:
    _ = bank_id
    return audit_reviews.list_reviews(db, access, cycle_id)


@router.post(
    f"{_CYCLE}/audit-reviews",
    response_model=IcaapAuditReviewRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIcaapAuditReview",
    responses=_ERRORS,
)
def create_icaap_audit_review(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapAuditReviewCreate,
    db: DbSession,
    access: IcaapAuditReview,
) -> IcaapAuditReviewRead:
    """Record the independent review. A preparer of this ICAAP cannot."""
    _ = bank_id
    return audit_reviews.create_review(db, access, cycle_id, payload)


@router.put(
    f"{_CYCLE}/audit-reviews/{{review_id}}",
    response_model=IcaapAuditReviewRead,
    operation_id="updateIcaapAuditReview",
    responses=_ERRORS,
)
def update_icaap_audit_review(  # noqa: PLR0913 - the addressed review is five path parts
    bank_id: str,
    cycle_id: UUID,
    review_id: UUID,
    payload: IcaapAuditReviewUpdate,
    db: DbSession,
    access: IcaapAuditReview,
) -> IcaapAuditReviewRead:
    _ = bank_id
    return audit_reviews.update_review(db, access, cycle_id, review_id, payload)


@router.post(
    f"{_CYCLE}/audit-reviews/{{review_id}}/finalise",
    response_model=IcaapAuditReviewRead,
    operation_id="finaliseIcaapAuditReview",
    responses=_ERRORS,
)
def finalise_icaap_audit_review(  # noqa: PLR0913 - the addressed review is five path parts
    bank_id: str,
    cycle_id: UUID,
    review_id: UUID,
    payload: IcaapReason,
    db: DbSession,
    access: IcaapAuditReview,
) -> IcaapAuditReviewRead:
    """Seal the review. After this it can only be superseded."""
    _ = bank_id
    return audit_reviews.finalise_review(db, access, cycle_id, review_id, payload)


@router.get(
    f"{_CYCLE}/challenges",
    response_model=IcaapChallengeListRead,
    operation_id="listIcaapChallenges",
    responses=_ERRORS,
)
def list_icaap_challenges(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapChallengeListRead:
    _ = bank_id
    return challenges.list_challenges(db, access, cycle_id)


@router.post(
    f"{_CYCLE}/challenges",
    response_model=IcaapChallengeRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="raiseIcaapChallenge",
    responses=_ERRORS,
)
def raise_icaap_challenge(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapChallengeCreate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapChallengeRead:
    """Record a challenge as minuted. The record cannot be edited afterwards."""
    _ = bank_id
    return challenges.raise_challenge(db, access, cycle_id, payload)


@router.post(
    f"{_CYCLE}/challenges/{{challenge_id}}/responses",
    response_model=IcaapChallengeRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="respondIcaapChallenge",
    responses=_ERRORS,
)
def respond_icaap_challenge(  # noqa: PLR0913 - the addressed challenge is five path parts
    bank_id: str,
    cycle_id: UUID,
    challenge_id: UUID,
    payload: IcaapChallengeResponseCreate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapChallengeRead:
    _ = bank_id
    return challenges.respond(db, access, cycle_id, challenge_id, payload)
