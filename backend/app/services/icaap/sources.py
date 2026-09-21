"""Finding the sealed sources an ICAAP block may bind to.

Two rules, both deliberate.

**The period match is exact.** A cycle's as-of date is the regulator's year end;
a block binds the computed position for THAT date or nothing. There is no
"latest period on or before", because a book that is a month old filed as a
year-end position is a wrong filing that looks right.

**Only succeeded runs.** A queued, running or failed run is not a position.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models import BankReportingPeriod, RegulatoryRun


def period_for(db: Session, access: IcaapAccess, as_of: date) -> BankReportingPeriod | None:
    """The reporting period whose ``period_end`` IS the cycle's as-of date."""
    return db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == access.ctx.organization_id,
            BankReportingPeriod.bank_id == access.bank.id,
            BankReportingPeriod.period_end == as_of,
        )
    )


def latest_succeeded_run(
    db: Session,
    access: IcaapAccess,
    period: BankReportingPeriod,
    module: str,
    scenario: str,
) -> RegulatoryRun | None:
    """The newest succeeded run of one module and scenario for one period."""
    return db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == access.ctx.organization_id,
            RegulatoryRun.bank_id == access.bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == module,
            RegulatoryRun.scenario_code == scenario,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )


def run_by_id(db: Session, access: IcaapAccess, run_id: object) -> RegulatoryRun | None:
    return db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == run_id,
            RegulatoryRun.organization_id == access.ctx.organization_id,
            RegulatoryRun.bank_id == access.bank.id,
        )
    )


__all__ = ["latest_succeeded_run", "period_for", "run_by_id"]
