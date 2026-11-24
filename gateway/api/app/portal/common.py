"""Shared supervisor-API pieces: submission rows, display names and view auditing."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import append
from app.auth.staff import StaffPrincipal, staff_display_names
from app.core.clock import iso
from app.core.context import AppContext
from app.core.errors import ApiError
from app.db.models import Institution, ReturnDefinition, Submission
from app.filing.obligations import due_date, reporting_dates
from app.filing.service import SubmissionState, state_of

Channel = Literal["api", "portal_upload"]
AUTOMATIC = "Automatic"


class RowValidation(BaseModel):
    passed: int
    total: int
    failed: int


class SubmissionRow(BaseModel):
    receipt_id: str
    received_at: str
    institution_code: str
    institution_name: str
    return_code: str
    return_name: str
    reporting_date: str
    version: int
    channel: Channel
    software_declared: str | None
    validation: RowValidation | None
    status: SubmissionState
    reviewer_name: str | None


def channel_of(value: str | None) -> Channel:
    return "portal_upload" if value == "portal_upload" else "api"


def int_field(data: dict[str, JsonValue] | None, name: str) -> int:
    if data is None:
        return 0
    value = data.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class Lookups:
    """Batch lookups of institutions, return names and staff names for a page of rows."""

    def __init__(self, session: Session, subs: Sequence[Submission]) -> None:
        ids = {s.institution_id for s in subs}
        self.institutions: dict[str, Institution] = (
            {i.id: i for i in session.scalars(select(Institution).where(Institution.id.in_(ids)))}
            if ids
            else {}
        )
        self.return_names: dict[str, str] = {
            d.code: d.name for d in session.scalars(select(ReturnDefinition))
        }
        subjects = {
            s.reviewer_subject
            for s in subs
            if s.reviewer_subject and s.reviewer_subject != "system"
        }
        self.names: dict[str, str] = staff_display_names(session, subjects)


def reviewer_name(sub: Submission, names: dict[str, str]) -> str | None:
    if sub.reviewer_subject == "system" or (
        sub.decided_by == "system" and not sub.reviewer_subject
    ):
        return AUTOMATIC
    if sub.reviewer_subject:
        return names.get(sub.reviewer_subject, sub.reviewer_subject)
    return None


def submission_row(sub: Submission, lookups: Lookups) -> SubmissionRow:
    institution = lookups.institutions.get(sub.institution_id)
    validation = None
    if sub.validation is not None:
        validation = RowValidation(
            passed=int_field(sub.validation, "passed"),
            total=int_field(sub.validation, "total"),
            failed=int_field(sub.validation, "failed"),
        )
    return SubmissionRow(
        receipt_id=sub.receipt_id,
        received_at=iso(sub.received_at),
        institution_code=institution.code if institution else "",
        institution_name=institution.name if institution else "",
        return_code=sub.return_code,
        return_name=lookups.return_names.get(sub.return_code, sub.return_code),
        reporting_date=sub.reporting_date.isoformat(),
        version=sub.version,
        channel=channel_of(sub.channel),
        software_declared=sub.software_declared,
        validation=validation,
        status=state_of(sub.status),
        reviewer_name=reviewer_name(sub, lookups.names),
    )


def rows(session: Session, subs: Sequence[Submission]) -> list[SubmissionRow]:
    lookups = Lookups(session, subs)
    return [submission_row(s, lookups) for s in subs]


def record_view(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    *,
    action: str,
    object_type: str,
    object_ref: str,
    institution_code: str | None = None,
    detail: dict[str, JsonValue] | None = None,
) -> None:
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="view",
        action=action,
        object_type=object_type,
        object_ref=object_ref,
        institution_code=institution_code,
        detail=detail,
    )


def institution_by_code(session: Session, code: str) -> Institution:
    institution = session.scalars(select(Institution).where(Institution.code == code)).first()
    if institution is None:
        raise ApiError("not_found", f"No institution {code}.")
    return institution


def submission_by_receipt(session: Session, receipt_id: str) -> Submission:
    sub = session.scalars(
        select(Submission).where(
            Submission.receipt_id == receipt_id, Submission.environment == "production"
        )
    ).first()
    if sub is None:
        raise ApiError("not_found", f"No filing {receipt_id}.")
    return sub


def default_cycle_date(ctx: AppContext, now: datetime) -> date:
    """The latest month-end whose (placeholder) monthly due date has passed."""
    candidates = reporting_dates("monthly", (now - timedelta(days=120)).date(), now.date())
    for candidate in reversed(candidates):
        if due_date(ctx.settings, "", "monthly", candidate) < now.date():
            return candidate
    return candidates[0] if candidates else now.date()


def day_label(day: date) -> str:
    return f"{day.day} {day:%b %Y}"


def short_day(day: date) -> str:
    return f"{day.day} {day:%b}"


def count_by(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out
