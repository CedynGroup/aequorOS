"""``GET /portal/api/overview``: filing compliance for one reporting date."""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import iso
from app.core.context import AppContext
from app.db.models import ApiClient, ApiDailyStat, Institution, Query, ReturnDefinition, Submission
from app.filing.obligations import CellState, ObligationRow, compute_obligations
from app.portal.common import SubmissionRow, day_label, rows

HEATMAP_MAX_RETURNS = 10
AttentionKind = Literal["outstanding", "rejected", "queried", "late", "credential"]
LinkType = Literal["submission", "institution", "query", "onboarding"]


class Totals(BaseModel):
    due: int
    received: int
    accepted: int
    outstanding_past_due: int
    filed_late: int
    open_queries: int
    institutions: int
    returns: int


class HeatCell(BaseModel):
    return_code: str
    state: CellState
    receipt_id: str | None


class HeatRow(BaseModel):
    institution_code: str
    institution_name: str
    cells: list[HeatCell]
    received: int
    due: int


class Heatmap(BaseModel):
    return_codes: list[str]
    rows: list[HeatRow]


class AttentionLink(BaseModel):
    type: LinkType
    ref: str


class AttentionItem(BaseModel):
    kind: AttentionKind
    when: str
    title: str
    detail: str
    link: AttentionLink


class ChannelStatus(BaseModel):
    #: ``None`` until the API has served traffic in the window: nothing measured, nothing claimed.
    api_availability_30d: float | None
    #: ``None`` until a filing has been received through the API in the window.
    median_receipt_seconds: float | None
    filings_today: int
    certificates_expiring_30d: int


class Overview(BaseModel):
    reporting_date: str
    totals: Totals
    heatmap: Heatmap
    attention: list[AttentionItem]
    channel: ChannelStatus
    latest: list[SubmissionRow]


def _heatmap(obligations: list[ObligationRow], definitions: dict[str, ReturnDefinition]) -> Heatmap:
    counts: dict[str, int] = {}
    for row in obligations:
        counts[row.definition.code] = counts.get(row.definition.code, 0) + 1
    codes = sorted(counts, key=lambda c: (-counts[c], definitions[c].position))[
        :HEATMAP_MAX_RETURNS
    ]
    codes.sort(key=lambda c: definitions[c].position)
    by_institution: dict[str, list[ObligationRow]] = {}
    for row in obligations:
        by_institution.setdefault(row.institution.id, []).append(row)
    heat_rows: list[HeatRow] = []
    for items in by_institution.values():
        institution = items[0].institution
        by_code = {r.definition.code: r for r in items}
        cells: list[HeatCell] = []
        for code in codes:
            ob = by_code.get(code)
            cells.append(
                HeatCell(
                    return_code=code,
                    state=ob.state if ob else "not_due",
                    receipt_id=ob.receipt_id if ob else None,
                )
            )
        received = sum(1 for c in cells if c.state in ("on_time", "late"))
        due = received + sum(1 for c in cells if c.state == "outstanding")
        heat_rows.append(
            HeatRow(
                institution_code=institution.code,
                institution_name=institution.name,
                cells=cells,
                received=received,
                due=due,
            )
        )
    heat_rows.sort(
        key=lambda r: (
            -sum(1 for c in r.cells if c.state == "outstanding"),
            -sum(1 for c in r.cells if c.state == "late"),
            r.institution_name,
        )
    )
    return Heatmap(return_codes=codes, rows=heat_rows)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _attention(
    session: Session,
    ctx: AppContext,
    obligations: list[ObligationRow],
    reporting_date: date,
    now: datetime,
) -> list[AttentionItem]:
    items: list[AttentionItem] = []
    overdue: dict[str, list[ObligationRow]] = {}
    for row in obligations:
        if row.status == "overdue":
            overdue.setdefault(row.institution.id, []).append(row)
    ranked = sorted(overdue.values(), key=lambda rs: (-len(rs), rs[0].institution.name))
    for group in ranked[:2]:
        group.sort(key=lambda r: (r.due_at, r.definition.position))
        first = group[0]
        others = len(group) - 1
        items.append(
            AttentionItem(
                kind="outstanding",
                when=iso(first.due_at),
                title=f"{first.institution.name} — {first.definition.code} {first.definition.name}",
                detail=f"Not received for {day_label(reporting_date)}."
                + (f" {_plural(others, 'other return')} also outstanding." if others else ""),
                link=AttentionLink(type="institution", ref=first.institution.code),
            )
        )
    institutions = {i.id: i for i in session.scalars(select(Institution))}
    rejected = session.scalars(
        select(Submission)
        .where(
            Submission.status == "rejected",
            Submission.environment == "production",
            Submission.received_at >= now - timedelta(days=7),
        )
        .order_by(Submission.received_at.desc())
        .limit(2)
    ).all()
    for sub in rejected:
        institution = institutions[sub.institution_id]
        failed = 0
        message = ""
        if sub.validation is not None:
            raw_failed = sub.validation.get("failed")
            failed = raw_failed if isinstance(raw_failed, int) else 0
            issues = sub.validation.get("issues")
            if isinstance(issues, list):
                for issue in issues:
                    if isinstance(issue, dict) and issue.get("severity") == "error":
                        text = issue.get("message")
                        message = text if isinstance(text, str) else ""
                        break
        auto = session.scalars(
            select(Query).where(Query.submission_id == sub.id, Query.opened_by == "system")
        ).first()
        items.append(
            AttentionItem(
                kind="rejected",
                when=iso(sub.received_at),
                title=f"{institution.name} — {sub.return_code} failed {_plural(failed, 'validation rule')}",
                detail=(message + (" Refile requested automatically." if auto else "")).strip(),
                link=AttentionLink(type="submission", ref=sub.receipt_id),
            )
        )
    open_queries = session.scalars(
        select(Query)
        .where(
            Query.status == "awaiting_institution", Query.due_at.is_not(None), Query.due_at >= now
        )
        .order_by(Query.due_at)
        .limit(2)
    ).all()
    for query in open_queries:
        institution = institutions[query.institution_id]
        sub = session.get(Submission, query.submission_id)
        assert query.due_at is not None
        items.append(
            AttentionItem(
                kind="queried",
                when=iso(query.due_at),
                title=f"{institution.name} — {sub.return_code if sub else ''} query: {query.subject}",
                detail=f"Reply due {day_label(query.due_at.date())}. Opened by {query.opened_by_name}.",
                link=AttentionLink(type="query", ref=query.number),
            )
        )
    late = sorted(
        (r for r in obligations if r.status == "filed_late" and r.record.first_filed is not None),
        key=lambda r: r.record.first_filed.received_at if r.record.first_filed else now,
        reverse=True,
    )[:2]
    for row in late:
        first = row.record.first_filed
        current = row.record.current
        assert first is not None
        days = max(1, (first.received_at.date() - row.due_at.date()).days)
        status = "Accepted" if current and current.status == "accepted" else "Received"
        items.append(
            AttentionItem(
                kind="late",
                when=iso(first.received_at),
                title=f"{row.institution.name} — {row.definition.code} filed {_plural(days, 'day')} late",
                detail=f"{status}. Lateness recorded against the institution.",
                link=AttentionLink(type="submission", ref=first.receipt_id),
            )
        )
    expiring = session.scalars(
        select(ApiClient)
        .where(
            ApiClient.status == "active",
            ApiClient.environment == "production",
            ApiClient.certificate_not_after <= now + timedelta(days=30),
        )
        .order_by(ApiClient.certificate_not_after)
        .limit(2)
    ).all()
    for client in expiring:
        institution = institutions[client.institution_id]
        days = (client.certificate_not_after.date() - now.date()).days
        items.append(
            AttentionItem(
                kind="credential",
                when=iso(client.certificate_not_after),
                title=f"{institution.name} — client certificate "
                + (f"expires in {_plural(days, 'day')}" if days >= 0 else "has expired"),
                detail=f"API client {client.client_id}. Certificates are renewed by the channel operator.",
                link=AttentionLink(type="onboarding", ref=institution.code),
            )
        )
    return items


def channel_status(session: Session, now: datetime) -> ChannelStatus:
    since = now.date() - timedelta(days=30)
    stats = session.scalars(select(ApiDailyStat).where(ApiDailyStat.day > since)).all()
    total = sum(s.total for s in stats)
    errors = sum(s.server_errors for s in stats)
    availability = round(100.0 * (total - errors) / total, 1) if total else None
    receipt_ms = list(
        session.scalars(
            select(Submission.receipt_ms).where(
                Submission.channel == "api", Submission.received_at >= now - timedelta(days=30)
            )
        )
    )
    median = round(statistics.median(receipt_ms) / 1000.0, 1) if receipt_ms else None
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = session.scalar(
        select(func.count())
        .select_from(Submission)
        .where(Submission.received_at >= start, Submission.environment == "production")
    )
    expiring = session.scalar(
        select(func.count())
        .select_from(ApiClient)
        .where(
            ApiClient.status == "active",
            ApiClient.certificate_not_after <= now + timedelta(days=30),
        )
    )
    return ChannelStatus(
        api_availability_30d=availability,
        median_receipt_seconds=median,
        filings_today=int(today or 0),
        certificates_expiring_30d=int(expiring or 0),
    )


def overview(session: Session, ctx: AppContext, reporting_date: date) -> Overview:
    now = ctx.clock.now()
    institutions = list(session.scalars(select(Institution)))
    obligations = compute_obligations(
        session, ctx.settings, institutions, reporting_date, reporting_date, now
    )
    definitions = {d.code: d for d in session.scalars(select(ReturnDefinition))}
    due_rows = [r for r in obligations if r.is_due]
    received = [r for r in due_rows if r.status in ("filed", "filed_late")]
    accepted = [
        r
        for r in received
        if r.record.current is not None and r.record.current.status == "accepted"
    ]
    open_queries = session.scalar(
        select(func.count()).select_from(Query).where(Query.status != "closed")
    )
    latest = session.scalars(
        select(Submission)
        .where(Submission.environment == "production")
        .order_by(Submission.received_at.desc())
        .limit(8)
    ).all()
    return Overview(
        reporting_date=reporting_date.isoformat(),
        totals=Totals(
            due=len(due_rows),
            received=len(received),
            accepted=len(accepted),
            outstanding_past_due=sum(1 for r in due_rows if r.status == "overdue"),
            filed_late=sum(1 for r in due_rows if r.status == "filed_late"),
            open_queries=int(open_queries or 0),
            institutions=len({r.institution.id for r in obligations}),
            returns=len({r.definition.code for r in obligations}),
        ),
        heatmap=_heatmap(obligations, definitions),
        attention=_attention(session, ctx, obligations, reporting_date, now),
        channel=channel_status(session, now),
        latest=rows(session, latest),
    )
