"""Institutions on the channel (read-only for every staff role), supervisory notes and the
read-only onboarding register. Enrolment itself is the operator's (``/ops/api``)."""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.chain import append
from app.auth.staff import StaffPrincipal
from app.core.clock import iso
from app.core.context import AppContext
from app.core.errors import ApiError
from app.db.models import (
    ApiClient,
    Institution,
    InstitutionContact,
    Query,
    SigningKey,
    StaffUser,
    Submission,
    SupervisoryNote,
)
from app.enrolment.service import display_status
from app.filing.obligations import CellState, ObligationOut, ObligationRow, compute_obligations
from app.portal.common import Channel, channel_of, institution_by_code, record_view
from app.queries.service import require_supervision

Stage = Literal["enrolled", "live", "suspended"]


class InstitutionListItem(BaseModel):
    code: str
    name: str
    type: str
    status: Stage
    channel: Channel | None
    software_declared: str | None
    on_time_12m: float | None
    outstanding: int


class InstitutionList(BaseModel):
    items: list[InstitutionListItem]


class LastFiling(BaseModel):
    at: str
    label: str


class InstitutionStats(BaseModel):
    on_time_12m: float
    peer_median_on_time_12m: float
    outstanding: int
    late_this_year: int
    filings_this_year: int
    open_queries: int
    last_filing: LastFiling | None


class HistoryCell(BaseModel):
    reporting_date: str
    state: CellState
    receipt_id: str | None


class HistoryRow(BaseModel):
    return_code: str
    cells: list[HistoryCell]


class History(BaseModel):
    months: list[str]
    rows: list[HistoryRow]


class Contact(BaseModel):
    role: str
    name: str


class ApiClientOut(BaseModel):
    client_id: str
    certificate_not_after: str


class SigningKeyOut(BaseModel):
    kid: str
    status: str


class Credentials(BaseModel):
    portal_users: int
    mfa: bool
    api_client: ApiClientOut | None
    signing_keys: list[SigningKeyOut]


class Note(BaseModel):
    id: str
    author_name: str
    body: str
    created_at: str


class InstitutionDetail(BaseModel):
    code: str
    name: str
    type: str
    channel: Channel | None
    flags: list[str]
    stats: InstitutionStats
    history: History
    obligations: list[ObligationOut]
    contacts: list[Contact]
    credentials: Credentials
    supervisor_name: str | None
    notes: list[Note]


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class Stages(BaseModel):
    stage: Stage
    count: int


class RegisterRow(BaseModel):
    code: str
    name: str
    type: str
    channel: Channel | None
    software_declared: str | None
    enrolled_at: str
    client_id: str | None
    certificate_not_after: str | None
    certificate_expiring: bool
    signing_kid: str | None
    status: Stage


class Onboarding(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    stages: list[Stages]
    register_: list[RegisterRow] = Field(alias="register")


def _year_window(now: datetime) -> tuple[date, date]:
    return (now - timedelta(days=365)).date(), (now + timedelta(days=31)).date()


def on_time_rate(rows: list[ObligationRow]) -> float | None:
    due = [r for r in rows if r.status in ("filed", "filed_late", "overdue")]
    if not due:
        return None
    return round(100.0 * sum(1 for r in due if r.status == "filed") / len(due), 1)


def _channel(institution: Institution) -> Channel | None:
    return channel_of(institution.channel) if institution.channel else None


def list_institutions(session: Session, ctx: AppContext, q: str | None) -> InstitutionList:
    now = ctx.clock.now()
    statement = select(Institution).order_by(Institution.name)
    if q:
        like = f"%{q.strip()}%"
        statement = statement.where(Institution.name.ilike(like) | Institution.code.ilike(like))
    institutions = list(session.scalars(statement))
    start, _ = _year_window(now)
    obligations = compute_obligations(session, ctx.settings, institutions, start, now.date(), now)
    by_institution: dict[str, list[ObligationRow]] = {}
    for row in obligations:
        by_institution.setdefault(row.institution.id, []).append(row)
    items: list[InstitutionListItem] = []
    for institution in institutions:
        rows = by_institution.get(institution.id, [])
        items.append(
            InstitutionListItem(
                code=institution.code,
                name=institution.name,
                type=institution.type,
                status=display_status(session, institution),
                channel=_channel(institution),
                software_declared=institution.software_declared,
                on_time_12m=on_time_rate(rows),
                outstanding=sum(1 for r in rows if r.status == "overdue"),
            )
        )
    return InstitutionList(items=items)


def _month_label(day: date) -> str:
    return f"{day:%Y-%m}"


def institution_detail(
    session: Session, ctx: AppContext, principal: StaffPrincipal, code: str
) -> InstitutionDetail:
    institution = institution_by_code(session, code)
    now = ctx.clock.now()
    start, end = _year_window(now)
    obligations = compute_obligations(session, ctx.settings, [institution], start, end, now)
    past = [r for r in obligations if r.reporting_date <= now.date()]
    peers = [i for i in session.scalars(select(Institution).where(Institution.status == "live"))]
    peer_rows = compute_obligations(session, ctx.settings, peers, start, now.date(), now)
    peer_rates: dict[str, list[ObligationRow]] = {}
    for row in peer_rows:
        peer_rates.setdefault(row.institution.id, []).append(row)
    rates = [r for r in (on_time_rate(v) for v in peer_rates.values()) if r is not None]
    year_start = date(now.year, 1, 1)
    this_year = [
        r
        for r in past
        if r.reporting_date >= year_start - timedelta(days=31)
        and r.record.first_filed
        and r.record.first_filed.received_at.date() >= year_start
    ]
    last = session.scalars(
        select(Submission)
        .where(Submission.institution_id == institution.id, Submission.environment == "production")
        .order_by(Submission.received_at.desc())
        .limit(1)
    ).first()
    open_queries = session.scalar(
        select(func.count())
        .select_from(Query)
        .where(Query.institution_id == institution.id, Query.status != "closed")
    )
    monthly = sorted({r.reporting_date for r in past if r.definition.cadence == "monthly"})[-11:]
    codes: list[str] = []
    for row in sorted(past, key=lambda r: r.definition.position):
        if row.definition.cadence == "monthly" and row.definition.code not in codes:
            codes.append(row.definition.code)
    by_key = {(r.definition.code, r.reporting_date): r for r in past}
    history = History(
        months=[_month_label(d) for d in monthly],
        rows=[
            HistoryRow(
                return_code=code_,
                cells=[
                    HistoryCell(
                        reporting_date=d.isoformat(),
                        state=by_key[(code_, d)].state if (code_, d) in by_key else "not_due",
                        receipt_id=by_key[(code_, d)].receipt_id if (code_, d) in by_key else None,
                    )
                    for d in monthly
                ],
            )
            for code_ in codes
        ],
    )
    soon = now + timedelta(days=30)
    upcoming = sorted(
        (
            r
            for r in obligations
            if r.status == "overdue" or (r.status in ("due", "not_due") and r.due_at <= soon)
        ),
        key=lambda r: (r.due_at, r.definition.position),
    )
    client = session.scalars(
        select(ApiClient)
        .where(
            ApiClient.institution_id == institution.id,
            ApiClient.status == "active",
            ApiClient.environment == "production",
        )
        .order_by(ApiClient.created_at.desc())
    ).first()
    keys = session.scalars(
        select(SigningKey)
        .where(SigningKey.institution_id == institution.id)
        .order_by(SigningKey.registered_at)
    ).all()
    contacts = session.scalars(
        select(InstitutionContact).where(InstitutionContact.institution_id == institution.id)
    ).all()
    notes = session.scalars(
        select(SupervisoryNote)
        .where(SupervisoryNote.institution_id == institution.id)
        .order_by(SupervisoryNote.created_at.desc())
    ).all()
    supervisor = (
        session.get(StaffUser, institution.supervisor_subject)
        if institution.supervisor_subject
        else None
    )
    last_filing = None
    if last is not None:
        channel_text = "API" if last.channel == "api" else "portal upload"
        last_filing = LastFiling(
            at=iso(last.received_at), label=f"{last.return_code} v{last.version} · {channel_text}"
        )
    record_view(
        session,
        ctx,
        principal,
        action="Opened institution record",
        object_type="institution",
        object_ref=institution.code,
        institution_code=institution.code,
    )
    return InstitutionDetail(
        code=institution.code,
        name=institution.name,
        type=institution.type,
        channel=_channel(institution),
        flags=list(institution.flags),
        stats=InstitutionStats(
            on_time_12m=on_time_rate(past) or 0.0,
            peer_median_on_time_12m=round(statistics.median(rates), 1) if rates else 0.0,
            outstanding=sum(1 for r in past if r.status == "overdue"),
            late_this_year=sum(1 for r in this_year if r.status == "filed_late"),
            filings_this_year=len(this_year),
            open_queries=int(open_queries or 0),
            last_filing=last_filing,
        ),
        history=history,
        obligations=[r.out() for r in upcoming],
        contacts=[Contact(role=c.role, name=c.name) for c in contacts],
        credentials=Credentials(
            portal_users=institution.portal_users,
            mfa=institution.portal_mfa,
            api_client=ApiClientOut(
                client_id=client.client_id, certificate_not_after=iso(client.certificate_not_after)
            )
            if client
            else None,
            signing_keys=[SigningKeyOut(kid=k.kid, status=k.status) for k in keys],
        ),
        supervisor_name=supervisor.short_name if supervisor else institution.supervisor_subject,
        notes=[
            Note(id=n.id, author_name=n.author_name, body=n.body, created_at=iso(n.created_at))
            for n in notes
        ],
    )


def add_note(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    code: str,
    body: str,
    *,
    at: datetime | None = None,
) -> Note:
    institution = institution_by_code(session, code)
    require_supervision(principal, institution, None)
    now = at or ctx.clock.now()
    note = SupervisoryNote(
        institution_id=institution.id,
        author_subject=principal.subject,
        author_name=principal.short_name,
        body=body,
        created_at=now,
    )
    session.add(note)
    session.flush()
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="decision",
        at=now,
        action="Added supervisory note",
        object_type="institution",
        object_ref=institution.code,
        institution_code=institution.code,
        detail={"note_id": note.id},
    )
    return Note(
        id=note.id, author_name=note.author_name, body=note.body, created_at=iso(note.created_at)
    )


def onboarding(session: Session, ctx: AppContext) -> Onboarding:
    now = ctx.clock.now()
    institutions = list(session.scalars(select(Institution).order_by(Institution.name)))
    counts: dict[str, int] = {"enrolled": 0, "live": 0, "suspended": 0}
    register: list[RegisterRow] = []
    for institution in institutions:
        stage = display_status(session, institution)
        counts[stage] += 1
        client = session.scalars(
            select(ApiClient)
            .where(ApiClient.institution_id == institution.id, ApiClient.status == "active")
            .order_by(ApiClient.environment.desc(), ApiClient.created_at.desc())
        ).first()
        key = session.scalars(
            select(SigningKey)
            .where(SigningKey.institution_id == institution.id, SigningKey.status == "active")
            .order_by(SigningKey.registered_at.desc())
        ).first()
        register.append(
            RegisterRow(
                code=institution.code,
                name=institution.name,
                type=institution.type,
                channel=_channel(institution),
                software_declared=institution.software_declared,
                enrolled_at=iso(institution.registered_at),
                client_id=client.client_id if client else None,
                certificate_not_after=iso(client.certificate_not_after) if client else None,
                certificate_expiring=bool(
                    client
                    and client.environment == "production"
                    and client.certificate_not_after <= now + timedelta(days=30)
                ),
                signing_kid=key.kid if key else None,
                status=stage,
            )
        )
    return Onboarding(
        stages=[
            Stages(stage="enrolled", count=counts["enrolled"]),
            Stages(stage="live", count=counts["live"]),
            Stages(stage="suspended", count=counts["suspended"]),
        ],
        register=register,
    )


def not_found(code: str) -> ApiError:
    return ApiError("not_found", f"No institution {code}.")
