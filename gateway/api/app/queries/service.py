"""Queries and resubmission requests: supervisor questions to an institution about a filing.

Status: ``awaiting_institution`` → (institution responds) ``awaiting_supervisor`` →
(supervisor writes again) ``awaiting_institution`` … → ``closed``. Opening a query on a
filing in review moves it to ``queried``; the institution's answer moves it back to
``in_review``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import SYSTEM, Actor, append
from app.auth.staff import StaffPrincipal, require_decider
from app.core.clock import iso, iso_or_none
from app.core.context import AppContext
from app.core.errors import ApiError
from app.core.sequences import query_number
from app.db.models import Institution, Query, QueryMessage, Submission

QueryKind = Literal["query", "resubmission"]
QueryStatus = Literal["awaiting_institution", "awaiting_supervisor", "closed"]
AuthorKind = Literal["supervisor", "institution", "system"]


class QueryMessageOut(BaseModel):
    id: str
    author: AuthorKind
    author_label: str
    body: str
    created_at: str


class InstitutionQuery(BaseModel):
    number: str
    kind: QueryKind
    subject: str
    status: QueryStatus
    receipt_id: str
    due_at: str | None
    opened_at: str
    acknowledged_at: str | None
    messages: list[QueryMessageOut]


class ResponseIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    resubmission_receipt_id: str | None = None


class OpenQueryIn(BaseModel):
    receipt_id: str
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    kind: QueryKind
    due_at: datetime


class StaffMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    require_resubmission: bool = False
    due_at: datetime | None = None


def _kind(value: str) -> QueryKind:
    return "resubmission" if value == "resubmission" else "query"


def _status(value: str) -> QueryStatus:
    if value == "awaiting_supervisor":
        return "awaiting_supervisor"
    if value == "closed":
        return "closed"
    return "awaiting_institution"


def _author(value: str) -> AuthorKind:
    if value == "institution":
        return "institution"
    if value == "system":
        return "system"
    return "supervisor"


def message_out(message: QueryMessage) -> QueryMessageOut:
    return QueryMessageOut(
        id=message.id,
        author=_author(message.author),
        author_label=message.author_label,
        body=message.body,
        created_at=iso(message.created_at),
    )


def messages_of(session: Session, query_id: str) -> list[QueryMessage]:
    return list(
        session.scalars(
            select(QueryMessage)
            .where(QueryMessage.query_id == query_id)
            .order_by(QueryMessage.created_at)
        )
    )


def can_supervise(
    principal: StaffPrincipal, institution: Institution, submission: Submission | None
) -> bool:
    if principal.has("senior_supervisor"):
        return True
    if not principal.has("supervisor"):
        return False
    return institution.supervisor_subject == principal.subject or (
        submission is not None and submission.reviewer_subject == principal.subject
    )


def require_supervision(
    principal: StaffPrincipal, institution: Institution, submission: Submission | None
) -> None:
    require_decider(principal, "supervisor", "senior_supervisor")
    if not can_supervise(principal, institution, submission):
        raise ApiError("forbidden", "This institution is not assigned to you.")


def open_query(
    session: Session,
    ctx: AppContext,
    *,
    submission: Submission,
    institution: Institution,
    kind: QueryKind,
    subject: str,
    body: str,
    due_at: datetime | None,
    actor: Actor,
    author: AuthorKind,
    author_label: str,
    author_role: str | None,
    at: datetime | None = None,
) -> Query:
    now = at or ctx.clock.now()
    query = Query(
        number=query_number(session, now),
        institution_id=institution.id,
        submission_id=submission.id,
        receipt_id=submission.receipt_id,
        kind=kind,
        subject=subject,
        status="awaiting_institution",
        due_at=due_at,
        opened_at=now,
        opened_by=actor.ref,
        opened_by_name=author_label,
        delivered_at=None,
        acknowledged_at=None,
        closed_at=None,
        closed_by=None,
        last_activity_at=now,
    )
    session.add(query)
    session.flush()
    session.add(
        QueryMessage(
            query_id=query.id,
            author=author,
            author_label=author_label,
            author_role=author_role,
            author_ref=actor.ref,
            body=body,
            require_resubmission=kind == "resubmission",
            resubmission_receipt_id=None,
            created_at=now,
        )
    )
    previous_status = submission.status
    if submission.status == "in_review":
        submission.status = "queried"
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="query",
        at=now,
        action="Requested resubmission" if kind == "resubmission" else "Opened query",
        object_type="query",
        object_ref=query.number,
        institution_code=institution.code,
        detail={
            "receipt_id": submission.receipt_id,
            "subject": subject,
            "kind": kind,
            "due_at": iso_or_none(due_at),
            "submission_status": {"from": previous_status, "to": submission.status},
        },
    )
    session.flush()
    return query


def staff_open_query(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    body: OpenQueryIn,
    *,
    at: datetime | None = None,
) -> Query:
    submission = session.scalars(
        select(Submission).where(Submission.receipt_id == body.receipt_id)
    ).first()
    if submission is None:
        raise ApiError("not_found", f"No filing {body.receipt_id}.")
    institution = session.get(Institution, submission.institution_id)
    assert institution is not None
    require_supervision(principal, institution, submission)
    return open_query(
        session,
        ctx,
        submission=submission,
        institution=institution,
        kind=body.kind,
        subject=body.subject,
        body=body.body,
        due_at=body.due_at,
        actor=principal.actor,
        author="supervisor",
        author_label=principal.name,
        author_role="Supervisor",
        at=at,
    )


def load_query(session: Session, number: str) -> tuple[Query, Institution, Submission]:
    query = session.scalars(select(Query).where(Query.number == number)).first()
    if query is None:
        raise ApiError("not_found", f"No query {number}.")
    institution = session.get(Institution, query.institution_id)
    submission = session.get(Submission, query.submission_id)
    assert institution is not None
    assert submission is not None
    return query, institution, submission


def staff_message(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    number: str,
    body: StaffMessageIn,
    *,
    at: datetime | None = None,
) -> QueryMessage:
    query, institution, submission = load_query(session, number)
    require_supervision(principal, institution, submission)
    if query.status == "closed":
        raise ApiError("conflict", f"Query {number} is closed.")
    now = at or ctx.clock.now()
    message = QueryMessage(
        query_id=query.id,
        author="supervisor",
        author_label=principal.name,
        author_role="Supervisor",
        author_ref=principal.subject,
        body=body.body,
        require_resubmission=body.require_resubmission,
        resubmission_receipt_id=None,
        created_at=now,
    )
    session.add(message)
    query.status = "awaiting_institution"
    query.last_activity_at = now
    if body.require_resubmission:
        query.kind = "resubmission"
    if body.due_at is not None:
        query.due_at = body.due_at
    if submission.status == "in_review":
        submission.status = "queried"
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="query",
        at=now,
        action="Wrote to the institution",
        object_type="query",
        object_ref=number,
        institution_code=institution.code,
        detail={
            "require_resubmission": body.require_resubmission,
            "due_at": iso_or_none(body.due_at),
        },
    )
    session.flush()
    return message


def close_query(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    number: str,
    *,
    at: datetime | None = None,
) -> None:
    query, institution, submission = load_query(session, number)
    require_supervision(principal, institution, submission)
    if query.status == "closed":
        raise ApiError("conflict", f"Query {number} is already closed.")
    now = at or ctx.clock.now()
    query.status = "closed"
    query.closed_at = now
    query.closed_by = principal.subject
    query.last_activity_at = now
    if submission.status == "queried" and not _other_open_queries(session, submission.id, query.id):
        submission.status = "in_review"
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="query",
        at=now,
        action="Closed query",
        object_type="query",
        object_ref=number,
        institution_code=institution.code,
    )


def _other_open_queries(session: Session, submission_id: str, except_id: str) -> bool:
    return (
        session.scalars(
            select(Query).where(
                Query.submission_id == submission_id,
                Query.status != "closed",
                Query.id != except_id,
            )
        ).first()
        is not None
    )


def institution_view(session: Session, query: Query) -> InstitutionQuery:
    return InstitutionQuery(
        number=query.number,
        kind=_kind(query.kind),
        subject=query.subject,
        status=_status(query.status),
        receipt_id=query.receipt_id,
        due_at=iso_or_none(query.due_at),
        opened_at=iso(query.opened_at),
        acknowledged_at=iso_or_none(query.acknowledged_at),
        messages=[message_out(m) for m in messages_of(session, query.id)],
    )


def list_for_institution(
    session: Session,
    ctx: AppContext,
    institution_id: str,
    institution_code: str,
    actor: Actor,
    *,
    include_closed: bool,
    limit: int,
    cursor: str | None,
) -> tuple[list[InstitutionQuery], str | None]:
    statement = select(Query).where(Query.institution_id == institution_id)
    if not include_closed:
        statement = statement.where(Query.status != "closed")
    if cursor:
        statement = statement.where(Query.number < cursor)
    rows = list(session.scalars(statement.order_by(Query.number.desc()).limit(limit + 1)))
    page = rows[:limit]
    now = ctx.clock.now()
    for query in page:
        if query.delivered_at is None:
            query.delivered_at = now
            append(
                session,
                ctx.keys,
                ctx.clock,
                actor=actor,
                category="query",
                action="Query delivered",
                object_type="query",
                object_ref=query.number,
                institution_code=institution_code,
            )
    next_cursor = page[-1].number if len(rows) > limit and page else None
    return [institution_view(session, q) for q in page], next_cursor


def _own_query(session: Session, institution_id: str, number: str) -> Query:
    query = session.scalars(
        select(Query).where(Query.number == number, Query.institution_id == institution_id)
    ).first()
    if query is None:
        raise ApiError("not_found", f"No query {number}.")
    return query


def acknowledge(
    session: Session,
    ctx: AppContext,
    institution_id: str,
    institution_code: str,
    actor: Actor,
    number: str,
    *,
    at: datetime | None = None,
) -> None:
    query = _own_query(session, institution_id, number)
    now = at or ctx.clock.now()
    if query.delivered_at is None:
        query.delivered_at = now
    if query.acknowledged_at is None:
        query.acknowledged_at = now
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=actor,
            category="query",
            at=now,
            action="Query acknowledged by the institution's system",
            object_type="query",
            object_ref=number,
            institution_code=institution_code,
        )


def respond(
    session: Session,
    ctx: AppContext,
    institution: Institution,
    actor: Actor,
    number: str,
    body: ResponseIn,
    *,
    author_role: str | None = None,
    at: datetime | None = None,
) -> QueryMessage:
    query = _own_query(session, institution.id, number)
    if query.status == "closed":
        raise ApiError("conflict", f"Query {number} is closed.")
    now = at or ctx.clock.now()
    if body.resubmission_receipt_id is not None:
        original = session.get(Submission, query.submission_id)
        corrected = session.scalars(
            select(Submission).where(Submission.receipt_id == body.resubmission_receipt_id)
        ).first()
        if (
            corrected is None
            or original is None
            or corrected.institution_id != institution.id
            or corrected.return_code != original.return_code
            or corrected.reporting_date != original.reporting_date
            or corrected.basis != original.basis
        ):
            raise ApiError(
                "invalid_request",
                "resubmission_receipt_id must be a filing of the same return, reporting date and basis.",
            )
    message = QueryMessage(
        query_id=query.id,
        author="institution",
        author_label=institution.name,
        author_role=author_role,
        author_ref=actor.ref,
        body=body.body,
        require_resubmission=False,
        resubmission_receipt_id=body.resubmission_receipt_id,
        created_at=now,
    )
    session.add(message)
    query.status = "awaiting_supervisor"
    query.last_activity_at = now
    if query.delivered_at is None:
        query.delivered_at = now
    submission = session.get(Submission, query.submission_id)
    if submission is not None and submission.status == "queried":
        submission.status = "in_review"
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="query",
        at=now,
        action="Institution replied",
        object_type="query",
        object_ref=number,
        institution_code=institution.code,
        detail={"resubmission_receipt_id": body.resubmission_receipt_id},
    )
    session.flush()
    return message


def open_automatic_resubmission(
    session: Session,
    ctx: AppContext,
    submission: Submission,
    institution: Institution,
    failed_messages: list[str],
    due_at: datetime,
    *,
    at: datetime | None = None,
) -> Query:
    count = len(failed_messages)
    subject = f"{submission.return_code} failed {count} validation rule{'s' if count != 1 else ''}"
    body = (
        f"Filing {submission.receipt_id} for {submission.reporting_date:%d %b %Y} was rejected at "
        "validation. Please correct the return and file an amendment that supersedes it.\n\n"
        + "\n".join(f"• {m}" for m in failed_messages)
    )
    return open_query(
        session,
        ctx,
        submission=submission,
        institution=institution,
        kind="resubmission",
        subject=subject,
        body=body,
        due_at=due_at,
        actor=SYSTEM,
        author="system",
        author_label="Automatic",
        author_role=None,
        at=at,
    )
