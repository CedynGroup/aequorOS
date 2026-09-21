"""ICAAP AI drafting: request a draft, read it, insert it or discard it."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, Response, status

from app.api.deps import DbSession, IcaapAiDraft, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap import IcaapSectionRead
from app.schemas.icaap_ai import (
    IcaapAiDraftAccept,
    IcaapAiDraftReject,
    IcaapAiSuggestionListRead,
    IcaapAiSuggestionRead,
)
from app.services.icaap import ai_drafting

router = APIRouter(tags=["icaap"])

_SECTION_KEY = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,59}$")]
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
}
_BASE = "/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/ai-drafts"


@router.post(
    _BASE,
    response_model=IcaapAiSuggestionRead,
    operation_id="requestIcaapAiDraft",
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
)
def request_icaap_ai_draft(  # noqa: PLR0913 - three path parts plus the response
    bank_id: str,
    cycle_id: UUID,
    section_key: _SECTION_KEY,
    response: Response,
    db: DbSession,
    access: IcaapAiDraft,
) -> IcaapAiSuggestionRead:
    _ = bank_id
    suggestion, created = ai_drafting.enqueue(db, access, cycle_id, section_key)
    if not created:
        # Debounced into the request already in flight: nothing new was queued,
        # so 202 would overstate what happened.
        response.status_code = status.HTTP_200_OK
    return suggestion


@router.get(
    _BASE,
    response_model=IcaapAiSuggestionListRead,
    operation_id="listIcaapAiDrafts",
    responses=_ERRORS,
)
def list_icaap_ai_drafts(
    bank_id: str,
    cycle_id: UUID,
    section_key: _SECTION_KEY,
    db: DbSession,
    access: IcaapView,
) -> IcaapAiSuggestionListRead:
    _ = bank_id
    return ai_drafting.list_drafts(db, access, cycle_id, section_key)


@router.get(
    _BASE + "/{suggestion_id}",
    response_model=IcaapAiSuggestionRead,
    operation_id="getIcaapAiDraft",
    responses=_ERRORS,
)
def get_icaap_ai_draft(  # noqa: PLR0913 - the addressed draft is four path parts
    bank_id: str,
    cycle_id: UUID,
    section_key: _SECTION_KEY,
    suggestion_id: UUID,
    db: DbSession,
    access: IcaapView,
) -> IcaapAiSuggestionRead:
    _ = bank_id
    return ai_drafting.get_draft(db, access, cycle_id, section_key, suggestion_id)


@router.post(
    _BASE + "/{suggestion_id}/accept",
    response_model=IcaapSectionRead,
    operation_id="acceptIcaapAiDraft",
    responses=_ERRORS,
)
def accept_icaap_ai_draft(  # noqa: PLR0913 - four path parts plus the body
    bank_id: str,
    cycle_id: UUID,
    section_key: _SECTION_KEY,
    suggestion_id: UUID,
    payload: IcaapAiDraftAccept,
    db: DbSession,
    access: IcaapAiDraft,
) -> IcaapSectionRead:
    _ = bank_id
    return ai_drafting.accept(db, access, cycle_id, section_key, suggestion_id, payload)


@router.post(
    _BASE + "/{suggestion_id}/reject",
    response_model=IcaapAiSuggestionRead,
    operation_id="rejectIcaapAiDraft",
    responses=_ERRORS,
)
def reject_icaap_ai_draft(  # noqa: PLR0913 - four path parts plus the body
    bank_id: str,
    cycle_id: UUID,
    section_key: _SECTION_KEY,
    suggestion_id: UUID,
    payload: IcaapAiDraftReject,
    db: DbSession,
    access: IcaapAiDraft,
) -> IcaapAiSuggestionRead:
    _ = bank_id
    return ai_drafting.reject(db, access, cycle_id, section_key, suggestion_id, payload)
