"""Operator support sessions: no standing access. A request is approved or denied by a
BoG access approver, expires unanswered after 60 minutes and ends at its time limit."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.chain import SYSTEM, Actor, append
from app.auth.staff import ALL_ROLES, Role, StaffPrincipal, require_decider
from app.core.clock import iso
from app.core.context import AppContext
from app.core.errors import ApiError
from app.core.sequences import operator_session_number
from app.db.models import OperatorAccessRequest, StaffUser

UNANSWERED_MINUTES = 60
ROLE_CAN: dict[Role, str] = {
    "supervisor": "Review, accept, query and request resubmission for assigned institutions",
    "senior_supervisor": "Everything a supervisor can, for all institutions; reassign work",
    "returns_policy": "Draft and approve return definitions, with a second approver",
    "access_approver": "Approve or deny operator support sessions",
    "auditor": "Read-only access to everything, including audit log export",
}


class AccessRequestIn(BaseModel):
    operator_name: str = Field(min_length=1, max_length=200)
    operator_title: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=500)
    excludes: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)
    incident_ref: str | None = Field(default=None, max_length=80)


class NumberOut(BaseModel):
    number: str


class ApproveIn(BaseModel):
    duration_minutes: Literal[30, 60, 120]


class DenyIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class RoleRow(BaseModel):
    group: str
    role: Role
    can: str
    members: int | None


class PendingRow(BaseModel):
    number: str
    operator_name: str
    operator_title: str
    scope: str
    excludes: str
    reason: str
    incident_ref: str | None
    requested_at: str
    expires_at: str


class SessionRow(BaseModel):
    number: str
    operator_name: str
    status: Literal["active", "ended", "denied", "expired"]
    detail: str


class AccessView(BaseModel):
    roles: list[RoleRow]
    pending: list[PendingRow]
    sessions: list[SessionRow]


def _short(name: str) -> str:
    parts = name.split()
    return f"{parts[0][0]}. {parts[-1]}" if len(parts) >= 2 else name


def _operator_actor(row: OperatorAccessRequest) -> Actor:
    return Actor(
        "operator",
        row.operator_id,
        f"{_short(row.operator_name)}, operator",
        "Support · approved session",
    )


def request_access(
    session: Session,
    ctx: AppContext,
    actor: Actor,
    body: AccessRequestIn,
    *,
    at: datetime | None = None,
) -> str:
    now = at or ctx.clock.now()
    number = operator_session_number(session, now)
    session.add(
        OperatorAccessRequest(
            number=number,
            operator_id=actor.ref,
            operator_email="",
            operator_name=body.operator_name,
            operator_title=body.operator_title,
            scope=body.scope,
            excludes=body.excludes,
            reason=body.reason,
            incident_ref=body.incident_ref,
            status="pending",
            requested_at=now,
            decided_at=None,
            decided_by=None,
            decided_by_name=None,
            duration_minutes=None,
            starts_at=None,
            ends_at=None,
            deny_reason=None,
        )
    )
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="operator_access",
        at=now,
        action="Requested support access",
        object_type="operator_session",
        object_ref=number,
        detail={
            "operator_name": body.operator_name,
            "operator_title": body.operator_title,
            "scope": body.scope,
            "excludes": body.excludes,
            "reason": body.reason,
            "incident_ref": body.incident_ref,
        },
    )
    session.flush()
    return number


def _load(session: Session, number: str) -> OperatorAccessRequest:
    row = session.scalars(
        select(OperatorAccessRequest).where(OperatorAccessRequest.number == number)
    ).first()
    if row is None:
        raise ApiError("not_found", f"No support request {number}.")
    return row


def _apply_time(
    session: Session, ctx: AppContext, row: OperatorAccessRequest, now: datetime
) -> bool:
    changed = False
    if row.status == "pending" and now >= row.requested_at + timedelta(minutes=UNANSWERED_MINUTES):
        row.status = "expired"
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=SYSTEM,
            category="operator_access",
            action="Support request expired unanswered",
            object_type="operator_session",
            object_ref=row.number,
            detail={"unanswered_minutes": UNANSWERED_MINUTES},
        )
        changed = True
    elif row.status == "active" and row.ends_at is not None and now >= row.ends_at:
        row.status = "ended"
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=SYSTEM,
            category="operator_access",
            action="Support session ended",
            object_type="operator_session",
            object_ref=row.number,
            detail={"ended_at": iso(row.ends_at)},
        )
        changed = True
    return changed


def _check_open(
    session: Session, ctx: AppContext, row: OperatorAccessRequest, now: datetime
) -> None:
    _apply_time(session, ctx, row, now)
    if row.status != "pending":
        raise ApiError(
            "conflict", f"Support request {row.number} is no longer awaiting a decision."
        )


def _not_requester(principal: StaffPrincipal, row: OperatorAccessRequest) -> None:
    if (
        principal.subject == row.operator_id
        or principal.name.strip().casefold() == row.operator_name.strip().casefold()
        or (row.operator_email and principal.email.casefold() == row.operator_email.casefold())
    ):
        raise ApiError(
            "forbidden", "Nobody can approve an operator session they requested or will use."
        )


def approve(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    number: str,
    minutes: int,
    *,
    at: datetime | None = None,
) -> None:
    require_decider(principal, "access_approver")
    row = _load(session, number)
    now = at or ctx.clock.now()
    _not_requester(principal, row)
    _check_open(session, ctx, row, now)
    row.status = "active"
    row.decided_at = now
    row.decided_by = principal.subject
    row.decided_by_name = principal.short_name
    row.duration_minutes = minutes
    row.starts_at = now
    row.ends_at = now + timedelta(minutes=minutes)
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="operator_access",
        at=now,
        action="Approved support session",
        object_type="operator_session",
        object_ref=number,
        detail={"duration_minutes": minutes, "operator_name": row.operator_name},
    )
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=_operator_actor(row),
        category="operator_access",
        at=now,
        action="Support session started",
        object_type="operator_session",
        object_ref=number,
        detail={
            "operator": f"{_short(row.operator_name)}, {row.operator_title}",
            "approved_by": principal.short_name,
            "approved_at": iso(now),
            "scope": row.scope,
            "excludes": row.excludes,
            "limit_minutes": minutes,
            "reason": row.reason,
            "incident_ref": row.incident_ref,
        },
    )


def deny(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    number: str,
    reason: str,
    *,
    at: datetime | None = None,
) -> None:
    require_decider(principal, "access_approver")
    row = _load(session, number)
    now = at or ctx.clock.now()
    _not_requester(principal, row)
    _check_open(session, ctx, row, now)
    row.status = "denied"
    row.decided_at = now
    row.decided_by = principal.subject
    row.decided_by_name = principal.short_name
    row.deny_reason = reason
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="operator_access",
        at=now,
        action="Denied support access",
        object_type="operator_session",
        object_ref=number,
        detail={"reason": reason},
    )


def expire_requests(ctx: AppContext) -> int:
    now = ctx.clock.now()
    count = 0
    with ctx.db.session() as session:
        rows = session.scalars(
            select(OperatorAccessRequest).where(
                OperatorAccessRequest.status.in_(("pending", "active"))
            )
        ).all()
        for row in rows:
            if _apply_time(session, ctx, row, now):
                count += 1
    return count


def _detail(row: OperatorAccessRequest) -> str:
    approver = row.decided_by_name or "an approver"
    if row.status == "active" and row.starts_at is not None:
        return f"Approved by {approver} · {row.duration_minutes} min from {row.starts_at:%H:%M}"
    if row.status == "ended" and row.starts_at is not None:
        return f"Approved by {approver} · {row.duration_minutes} min on {row.starts_at.day} {row.starts_at:%b}"
    if row.status == "denied":
        return f"Requested {row.requested_at.day} {row.requested_at:%b} · {row.deny_reason or 'denied'}"
    return f"Not answered within {UNANSWERED_MINUTES} minutes"


def _session_status(value: str) -> Literal["active", "ended", "denied", "expired"]:
    if value == "active":
        return "active"
    if value == "ended":
        return "ended"
    if value == "denied":
        return "denied"
    return "expired"


def access_view(session: Session, ctx: AppContext) -> AccessView:
    now = ctx.clock.now()
    for row in session.scalars(
        select(OperatorAccessRequest).where(OperatorAccessRequest.status.in_(("pending", "active")))
    ).all():
        _apply_time(session, ctx, row, now)
    groups = {role: group for group, role in ctx.settings.group_roles.items()}
    members: dict[str, int] = {}
    for user in session.scalars(select(StaffUser)):
        for role in user.roles:
            members[role] = members.get(role, 0) + 1
    roles = [
        RoleRow(
            group=groups.get(role, ""), role=role, can=ROLE_CAN[role], members=members.get(role, 0)
        )
        for role in ALL_ROLES
        if role in groups
    ]
    pending_rows = session.scalars(
        select(OperatorAccessRequest)
        .where(OperatorAccessRequest.status == "pending")
        .order_by(OperatorAccessRequest.requested_at)
    ).all()
    recent = session.scalars(
        select(OperatorAccessRequest)
        .where(OperatorAccessRequest.status != "pending")
        .order_by(OperatorAccessRequest.requested_at.desc())
        .limit(10)
    ).all()
    return AccessView(
        roles=roles,
        pending=[
            PendingRow(
                number=row.number,
                operator_name=row.operator_name,
                operator_title=row.operator_title,
                scope=row.scope,
                excludes=row.excludes,
                reason=row.reason,
                incident_ref=row.incident_ref,
                requested_at=iso(row.requested_at),
                expires_at=iso(row.requested_at + timedelta(minutes=UNANSWERED_MINUTES)),
            )
            for row in pending_rows
        ],
        sessions=[
            SessionRow(
                number=row.number,
                operator_name=row.operator_name,
                status=_session_status(row.status),
                detail=_detail(row),
            )
            for row in recent
        ],
    )


def count_requests(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(OperatorAccessRequest)) or 0)
