"""``GET /portal/api/calendar``: due dates from the published definitions (placeholder rule)."""

from __future__ import annotations

import calendar as cal
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import AppContext
from app.db.models import Institution, Query, ReturnDefinition
from app.filing.obligations import ObligationRow, compute_obligations
from app.returns.service import CADENCES

Tone = Literal["past", "warning", "future"]


class CalendarEvent(BaseModel):
    title: str
    detail: str
    tone: Tone
    received: int | None
    due: int


class CalendarDay(BaseModel):
    date: str
    events: list[CalendarEvent]


class Upcoming(BaseModel):
    label: str
    count_label: str
    detail: str


class CadenceCount(BaseModel):
    cadence: str
    returns: int


class CalendarOut(BaseModel):
    month: str
    days: list[CalendarDay]
    upcoming: list[Upcoming]
    cadences: list[CadenceCount]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _group_title(rows: list[ObligationRow], overrides: set[str]) -> tuple[str, str]:
    """``(title, what)`` for a group of obligations due on the same day."""
    definition = rows[0].definition
    reporting = rows[0].reporting_date
    codes = {r.definition.code for r in rows}
    if definition.cadence == "weekly":
        return "Weekly returns", _plural(len(codes), "return")
    if len(codes) == 1 and definition.code in overrides:
        return definition.code, f"{reporting.day} {reporting:%b}"
    return f"{definition.cadence.capitalize()} · {reporting.day} {reporting:%b}", _plural(
        len(codes), "return"
    )


def _groups(
    rows: list[ObligationRow], settings_overrides: set[str]
) -> dict[date, list[list[ObligationRow]]]:
    grouped: dict[tuple[date, str, date, bool, str], list[ObligationRow]] = {}
    for row in rows:
        if row.definition.cadence == "daily":
            continue
        override = row.definition.code in settings_overrides
        key = (
            row.due_at.date(),
            row.definition.cadence,
            row.reporting_date,
            override,
            row.definition.code if override else "",
        )
        grouped.setdefault(key, []).append(row)
    out: dict[date, list[list[ObligationRow]]] = {}
    for key, items in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][4])):
        out.setdefault(key[0], []).append(items)
    return out


def calendar_view(session: Session, ctx: AppContext, month: str) -> CalendarOut:
    year, month_number = (int(p) for p in month.split("-"))
    first = date(year, month_number, 1)
    last = date(year, month_number, cal.monthrange(year, month_number)[1])
    now = ctx.clock.now()
    institutions = list(session.scalars(select(Institution)))
    # Reporting dates up to ~3 months before a due date can fall in this month.
    rows = compute_obligations(
        session,
        ctx.settings,
        institutions,
        first - timedelta(days=100),
        max(last, (now + timedelta(days=10)).date()),
        now,
    )
    overrides = set(ctx.settings.due_business_days_overrides)
    groups = _groups(rows, overrides)
    days: list[CalendarDay] = []
    day = first
    while day <= last:
        events: list[CalendarEvent] = []
        for items in groups.get(day, []):
            due = len(items)
            received = sum(1 for r in items if r.status in ("filed", "filed_late"))
            past = day <= now.date()
            if day < now.date():
                tone: Tone = "past" if received == due else "warning"
            elif day == now.date():
                tone = "warning"
            else:
                tone = "future"
            title, what = _group_title(items, overrides)
            detail = f"{what} · {received} / {due}" if past else f"{what} · due"
            events.append(
                CalendarEvent(
                    title=title,
                    detail=detail,
                    tone=tone,
                    received=received if past else None,
                    due=due,
                )
            )
        days.append(CalendarDay(date=day.isoformat(), events=events))
        day += timedelta(days=1)
    upcoming: list[Upcoming] = []
    horizon = now.date() + timedelta(days=10)
    for due_day in sorted(d for d in groups if now.date() < d <= horizon):
        for items in groups[due_day]:
            codes = sorted({r.definition.code for r in items})
            upcoming.append(
                Upcoming(
                    label=f"{due_day:%a} {due_day.day} {due_day:%b}",
                    count_label=_plural(len(items), "filing"),
                    detail=f"{items[0].definition.cadence.capitalize()}: {', '.join(codes)}",
                )
            )
    queries = session.scalars(
        select(Query)
        .where(
            Query.status == "awaiting_institution",
            Query.due_at.is_not(None),
            Query.due_at > now,
            Query.due_at <= now + timedelta(days=10),
        )
        .order_by(Query.due_at)
    ).all()
    names = {i.id: i.name for i in institutions}
    for query in queries:
        assert query.due_at is not None
        upcoming.append(
            Upcoming(
                label=f"{query.due_at:%a} {query.due_at.day} {query.due_at:%b}",
                count_label="1 query",
                detail=f"{names.get(query.institution_id, '')} reply on {query.subject}",
            )
        )
    upcoming.sort(key=lambda u: _sort_key(u.label, now))
    daily = [
        d
        for d in session.scalars(
            select(ReturnDefinition).where(ReturnDefinition.cadence == "daily")
        )
    ]
    if daily:
        filers = sum(
            1
            for i in institutions
            if i.status in ("live", "suspended") and any(i.type in d.applies_to for d in daily)
        )
        upcoming.append(
            Upcoming(
                label="Every business day",
                count_label=_plural(filers, "filing"),
                detail=f"{', '.join(d.code for d in daily)} by the published cut-off",
            )
        )
    definitions = list(session.scalars(select(ReturnDefinition)))
    cadences = [
        CadenceCount(cadence=c, returns=sum(1 for d in definitions if d.cadence == c))
        for c in CADENCES
    ]
    return CalendarOut(
        month=f"{year:04d}-{month_number:02d}", days=days, upcoming=upcoming, cadences=cadences
    )


def _sort_key(label: str, now: datetime) -> date:
    try:
        parsed = datetime.strptime(f"{label} {now.year}", "%a %d %b %Y").date()
    except ValueError:
        return date.max
    if parsed < now.date() - timedelta(days=180):
        parsed = parsed.replace(year=now.year + 1)
    return parsed
