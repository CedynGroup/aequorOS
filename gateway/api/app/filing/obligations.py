"""Reporting calendars and filing obligations.

Due dates use a PLACEHOLDER rule: a configurable number of business days after the
reporting date per cadence (``GATEWAY_DUE_BUSINESS_DAYS``), with per-return overrides
(``GATEWAY_DUE_BUSINESS_DAYS_OVERRIDES``) and per-institution extensions stored in
``obligations``. Bank of Ghana has not given us its due-date rule; the portal shows it as
``[BoG due-date rule]``. Business days are Monday to Friday; public holidays are not known.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import add_business_days, is_business_day
from app.core.settings import Settings
from app.db.models import Institution, Obligation, ReturnDefinition, ReturnVersion, Submission
from app.returns.service import published_versions

ObligationStatus = Literal["not_due", "due", "filed", "filed_late", "overdue"]
CellState = Literal["on_time", "late", "outstanding", "not_due"]
FILING_STATUSES = ("live", "suspended")


class ObligationOut(BaseModel):
    return_code: str
    return_version: str
    reporting_date: str
    basis: Literal["solo", "consolidated"]
    due_at: str
    status: ObligationStatus
    receipt_id: str | None


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def is_reporting_date(cadence: str, day: date) -> bool:
    if cadence == "daily":
        return is_business_day(day)
    if cadence == "weekly":
        return day.weekday() == 4
    if day != _month_end(day.year, day.month):
        return False
    if cadence == "monthly":
        return True
    if cadence == "quarterly":
        return day.month in (3, 6, 9, 12)
    if cadence == "semiannual":
        return day.month in (6, 12)
    if cadence == "annual":
        return day.month == 12
    return False


def reporting_dates(cadence: str, start: date, end: date) -> list[date]:
    if cadence in ("daily", "weekly"):
        out: list[date] = []
        day = start
        while day <= end:
            if is_reporting_date(cadence, day):
                out.append(day)
            day += timedelta(days=1)
        return out
    out = []
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        candidate = _month_end(year, month)
        if start <= candidate <= end and is_reporting_date(cadence, candidate):
            out.append(candidate)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def previous_reporting_date(cadence: str, day: date) -> date:
    probe = day - timedelta(days=1)
    for _ in range(400):
        if is_reporting_date(cadence, probe):
            return probe
        probe -= timedelta(days=1)
    return probe


def due_date(settings: Settings, code: str, cadence: str, reporting_date: date) -> date:
    offset = settings.due_business_days_overrides.get(
        code, settings.due_business_days.get(cadence, 10)
    )
    return add_business_days(reporting_date, offset)


def due_at(settings: Settings, code: str, cadence: str, reporting_date: date) -> datetime:
    """End of the due day, GMT (UTC)."""
    return datetime.combine(
        due_date(settings, code, cadence, reporting_date), time(23, 59, 59), UTC
    )


def validation_failed(sub: Submission) -> int:
    validation = sub.validation
    if validation is None:
        return 0
    value: JsonValue = validation.get("failed")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def counts_as_filed(sub: Submission) -> bool:
    """A version that was not rejected (pending validation counts as filed)."""
    return sub.status != "rejected" and validation_failed(sub) == 0


@dataclass
class FilingRecord:
    """All versions for one (institution, return, reporting date, basis)."""

    versions: list[Submission]

    @property
    def current(self) -> Submission | None:
        live = [v for v in self.versions if v.status != "superseded"]
        if live:
            return max(live, key=lambda v: v.version)
        return max(self.versions, key=lambda v: v.version) if self.versions else None

    @property
    def first_filed(self) -> Submission | None:
        filed = [v for v in self.versions if counts_as_filed(v)]
        return min(filed, key=lambda v: v.received_at) if filed else None

    @property
    def currently_filed(self) -> bool:
        current = self.current
        return current is not None and counts_as_filed(current)


@dataclass
class ObligationRow:
    institution: Institution
    definition: ReturnDefinition
    version: ReturnVersion
    reporting_date: date
    basis: str
    due_at: datetime
    record: FilingRecord
    status: ObligationStatus
    extended: bool

    @property
    def receipt_id(self) -> str | None:
        current = self.record.current
        return current.receipt_id if current is not None else None

    @property
    def state(self) -> CellState:
        if self.status == "filed":
            return "on_time"
        if self.status == "filed_late":
            return "late"
        if self.status == "overdue":
            return "outstanding"
        return "not_due"

    @property
    def is_due(self) -> bool:
        return self.status in ("filed", "filed_late", "overdue")

    def out(self) -> ObligationOut:
        return ObligationOut(
            return_code=self.definition.code,
            return_version=self.version.version,
            reporting_date=self.reporting_date.isoformat(),
            basis="consolidated" if self.basis == "consolidated" else "solo",
            due_at=self.due_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            status=self.status,
            receipt_id=self.receipt_id,
        )


def _status(
    record: FilingRecord, due: datetime, reporting_date: date, now: datetime
) -> ObligationStatus:
    first = record.first_filed
    if first is not None and record.currently_filed:
        return "filed" if first.received_at <= due else "filed_late"
    if now > due:
        return "overdue"
    if now.date() > reporting_date:
        return "due"
    return "not_due"


def load_records(
    session: Session,
    institution_ids: Sequence[str],
    start: date,
    end: date,
    *,
    environment: str = "production",
) -> dict[tuple[str, str, date, str], FilingRecord]:
    records: dict[tuple[str, str, date, str], FilingRecord] = {}
    if not institution_ids:
        return records
    rows = session.scalars(
        select(Submission).where(
            Submission.institution_id.in_(list(institution_ids)),
            Submission.reporting_date >= start,
            Submission.reporting_date <= end,
            Submission.environment == environment,
        )
    )
    for sub in rows:
        key = (sub.institution_id, sub.return_code, sub.reporting_date, sub.basis)
        records.setdefault(key, FilingRecord([])).versions.append(sub)
    return records


def compute_obligations(
    session: Session,
    settings: Settings,
    institutions: Iterable[Institution],
    start: date,
    end: date,
    now: datetime,
    *,
    codes: set[str] | None = None,
    cadences: set[str] | None = None,
) -> list[ObligationRow]:
    """Obligations with a reporting date in ``[start, end]``, solo basis."""
    filers = [i for i in institutions if i.status in FILING_STATUSES]
    definitions = list(
        session.scalars(select(ReturnDefinition).order_by(ReturnDefinition.position))
    )
    if codes is not None:
        definitions = [d for d in definitions if d.code in codes]
    if cadences is not None:
        definitions = [d for d in definitions if d.cadence in cadences]
    ids = [i.id for i in filers]
    records = load_records(session, ids, start, end)
    extensions = (
        {
            (o.institution_id, o.return_code, o.reporting_date, o.basis): o
            for o in session.scalars(
                select(Obligation).where(
                    Obligation.institution_id.in_(ids),
                    Obligation.reporting_date >= start,
                    Obligation.reporting_date <= end,
                )
            )
        }
        if ids
        else {}
    )
    rows: list[ObligationRow] = []
    for definition in definitions:
        versions = published_versions(session, definition.id)
        if not versions:
            continue
        for reporting_date in reporting_dates(definition.cadence, start, end):
            version = next(
                (
                    v
                    for v in reversed(versions)
                    if v.first_reporting_date <= reporting_date
                    and (v.last_reporting_date is None or reporting_date <= v.last_reporting_date)
                ),
                None,
            )
            if version is None:
                continue
            default_due = due_at(settings, definition.code, definition.cadence, reporting_date)
            for institution in filers:
                if institution.type not in definition.applies_to:
                    continue
                if institution.live_since is not None and reporting_date < institution.live_since:
                    continue
                key = (institution.id, definition.code, reporting_date, "solo")
                extension = extensions.get(key)
                due = extension.due_at if extension is not None else default_due
                record = records.get(key, FilingRecord([]))
                rows.append(
                    ObligationRow(
                        institution=institution,
                        definition=definition,
                        version=version,
                        reporting_date=reporting_date,
                        basis="solo",
                        due_at=due,
                        record=record,
                        status=_status(record, due, reporting_date, now),
                        extended=extension is not None,
                    )
                )
    return rows


def grant_extension(
    session: Session,
    institution: Institution,
    return_code: str,
    reporting_date: date,
    due: datetime,
    reason: str,
    granted_by: str,
    granted_at: datetime,
) -> Obligation:
    row = Obligation(
        institution_id=institution.id,
        return_code=return_code,
        reporting_date=reporting_date,
        basis="solo",
        due_at=due,
        reason=reason,
        granted_by=granted_by,
        granted_at=granted_at,
    )
    session.add(row)
    session.flush()
    return row
