"""The challenge log (¶45): what the Board asked, and what management did about it.

¶45 asks for evidence of Board challenge, not merely of Board approval. A
challenge is therefore a record of a question actually put — in a named forum,
on a date, by a named person who need not be a platform user, because Board
members generally are not.

Both tables are UNALTERABLE. A challenge that could be edited after the fact,
or a response that could be softened later, would be evidence of nothing. A
challenge stays OPEN until it has a response, and a response of ``deferred``
leaves it open, so "we answered it" and "we put it off" cannot be confused.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models.icaap import IcaapAttachment, IcaapCycle
from app.models.icaap_risk_capital import IcaapChallenge, IcaapChallengeResponse
from app.schemas.icaap_risk_capital import (
    IcaapChallengeCreate,
    IcaapChallengeListRead,
    IcaapChallengeRead,
    IcaapChallengeResponseCreate,
    IcaapChallengeResponseRead,
)
from app.services.audit import record_event
from app.services.icaap import guards

#: Forums whose challenge counts as Board-level evidence for ¶45.
BOARD_FORUMS = frozenset({"board", "board_risk_committee", "board_audit_committee"})
DEFERRED = "deferred"


def _challenges(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> list[IcaapChallenge]:
    return list(
        db.scalars(
            select(IcaapChallenge)
            .where(
                IcaapChallenge.organization_id == access.ctx.organization_id,
                IcaapChallenge.cycle_id == cycle.id,
            )
            .order_by(IcaapChallenge.challenge_no.asc())
        )
    )


def _responses(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> dict[UUID, list[IcaapChallengeResponse]]:
    out: dict[UUID, list[IcaapChallengeResponse]] = {}
    for row in db.scalars(
        select(IcaapChallengeResponse)
        .where(
            IcaapChallengeResponse.organization_id == access.ctx.organization_id,
            IcaapChallengeResponse.cycle_id == cycle.id,
        )
        .order_by(IcaapChallengeResponse.response_no.asc())
    ):
        out.setdefault(row.challenge_id, []).append(row)
    return out


def _is_open(responses: list[IcaapChallengeResponse]) -> bool:
    return not responses or responses[-1].outcome == DEFERRED


def _response_read(row: IcaapChallengeResponse) -> IcaapChallengeResponseRead:
    return IcaapChallengeResponseRead(
        id=row.id,
        response_no=row.response_no,
        outcome=row.outcome,  # pyright: ignore[reportArgumentType]
        response_text=row.response_text,
        change_references=list(row.change_references or []),
        responder_function=row.responder_function,
        responded_by=row.responded_by,
        created_at=row.created_at,
    )


def _read(row: IcaapChallenge, responses: list[IcaapChallengeResponse]) -> IcaapChallengeRead:
    return IcaapChallengeRead(
        id=row.id,
        challenge_no=row.challenge_no,
        round=row.round,
        raised_in=row.raised_in,  # pyright: ignore[reportArgumentType]
        raised_by_name=row.raised_by_name,
        raised_on=row.raised_on,
        meeting_reference=row.meeting_reference,
        target_kind=row.target_kind,  # pyright: ignore[reportArgumentType]
        target_ref=row.target_ref,
        challenge_text=row.challenge_text,
        severity=row.severity,  # pyright: ignore[reportArgumentType]
        minutes_attachment_id=row.minutes_attachment_id,
        recorded_by=row.recorded_by,
        created_at=row.created_at,
        responses=[_response_read(entry) for entry in responses],
        open=_is_open(responses),
    )


def list_challenges(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapChallengeListRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    rows = _challenges(db, access, cycle)
    responses = _responses(db, access, cycle)
    reads = [_read(row, responses.get(row.id, [])) for row in rows]
    return IcaapChallengeListRead(
        cycle_id=cycle.id,
        challenges=reads,
        challenge_count=len(reads),
        open_challenge_count=len([read for read in reads if read.open]),
        board_challenge_count=len([read for read in reads if read.raised_in in BOARD_FORUMS]),
    )


def _check_attachment(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, attachment_id: UUID | None
) -> None:
    if attachment_id is None:
        return
    found = db.scalar(
        select(IcaapAttachment).where(
            IcaapAttachment.id == attachment_id,
            IcaapAttachment.organization_id == access.ctx.organization_id,
            IcaapAttachment.cycle_id == cycle.id,
        )
    )
    if found is None:
        raise guards.unprocessable(
            "evidence_required", "Those minutes are not attached to this ICAAP."
        )


def _insert_challenge(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, payload: IcaapChallengeCreate
) -> IcaapChallenge:
    """Insert with the next number, retrying once if two people race.

    The number is a presentation detail, so a collision takes the next one
    rather than refusing a record of something that was actually said.
    """
    for attempt in (True, False):
        next_no = _next_challenge_no(db, access, cycle)
        row = IcaapChallenge(
            organization_id=cycle.organization_id,
            bank_id=cycle.bank_id,
            cycle_id=cycle.id,
            challenge_no=next_no,
            round=cycle.round,
            raised_in=payload.raised_in,
            raised_by_name=payload.raised_by_name,
            raised_on=payload.raised_on,
            meeting_reference=payload.meeting_reference,
            target_kind=payload.target_kind,
            target_ref=payload.target_ref,
            challenge_text=payload.challenge_text,
            severity=payload.severity,
            minutes_attachment_id=payload.minutes_attachment_id,
            recorded_by=guards.actor_id(access),
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            if attempt:
                # Two people recorded a challenge at once. The number is a
                # presentation detail, so take the next one rather than
                # refusing a record of something that was actually said.
                continue
            raise guards.conflict(
                "challenge_number_taken",
                "Another challenge was recorded at the same moment. Try again.",
            ) from None
        return row
    raise guards.conflict(  # pragma: no cover - the loop returns or raises
        "challenge_number_taken",
        "Another challenge was recorded at the same moment. Try again.",
    )


def raise_challenge(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapChallengeCreate
) -> IcaapChallengeRead:
    """Record a challenge. Numbering is per cycle and contiguous."""
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    _check_attachment(db, access, cycle, payload.minutes_attachment_id)
    row = _insert_challenge(db, access, cycle, payload)
    record_event(
        db,
        access.ctx,
        event_type="icaap.challenge.raised",
        entity_type="icaap_challenge",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "challenge_no": row.challenge_no,
            "raised_in": row.raised_in,
            "severity": row.severity,
        },
    )
    db.commit()
    db.refresh(row)
    return _read(row, [])


def _next_challenge_no(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> int:
    highest = db.scalar(
        select(func.max(IcaapChallenge.challenge_no)).where(
            IcaapChallenge.organization_id == access.ctx.organization_id,
            IcaapChallenge.cycle_id == cycle.id,
        )
    )
    return (highest or 0) + 1


def respond(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    challenge_id: UUID,
    payload: IcaapChallengeResponseCreate,
) -> IcaapChallengeRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    challenge = db.scalar(
        select(IcaapChallenge).where(
            IcaapChallenge.id == challenge_id,
            IcaapChallenge.organization_id == access.ctx.organization_id,
            IcaapChallenge.cycle_id == cycle.id,
        )
    )
    if challenge is None:
        guards.not_found()
    existing = _responses(db, access, cycle).get(challenge.id, [])
    row = IcaapChallengeResponse(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        challenge_id=challenge.id,
        response_no=len(existing) + 1,
        outcome=payload.outcome,
        response_text=payload.response_text,
        change_references=list(payload.change_references),
        responder_function=payload.responder_function,
        responded_by=guards.actor_id(access),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise guards.conflict(
            "challenge_number_taken",
            "Another response was recorded at the same moment. Try again.",
        ) from exc
    record_event(
        db,
        access.ctx,
        event_type="icaap.challenge.responded",
        entity_type="icaap_challenge_response",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "challenge_no": challenge.challenge_no,
            "outcome": row.outcome,
        },
    )
    db.commit()
    db.refresh(challenge)
    responses = _responses(db, access, cycle).get(challenge.id, [])
    return _read(challenge, responses)


def challenge_payload(read: IcaapChallengeListRead) -> dict[str, Any]:
    return {
        "challenges": [
            {
                "challenge_no": challenge.challenge_no,
                "raised_in": challenge.raised_in,
                "raised_on": challenge.raised_on.isoformat(),
                "severity": challenge.severity,
                "open": challenge.open,
                "responses": len(challenge.responses),
            }
            for challenge in read.challenges
        ]
    }


__all__ = [
    "BOARD_FORUMS",
    "DEFERRED",
    "challenge_payload",
    "list_challenges",
    "raise_challenge",
    "respond",
]
