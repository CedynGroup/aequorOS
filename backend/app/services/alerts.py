"""Bank alerts: the open critical/high live findings for the current period.

Aggregates the ``live_findings`` the pipeline reconciles into a compact,
severity-ranked feed for the alerts bell. Read-only; the pipeline owns the
finding lifecycle (open on breach, superseded on clear).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, LiveFinding
from app.schemas.live import AlertItemRead, BankAlertsRead

_ALERT_SEVERITIES = ("critical", "high")
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_OPEN_STATUSES = ("open", "needs_review")


def get_bank_alerts(
    db: Session, ctx: TenantContext, bank_id: UUID, *, limit: int = 50
) -> BankAlertsRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _latest_period(db, ctx, bank)
    if period is None:
        return BankAlertsRead(bank_id=bank.id, total=0, by_severity={}, by_module={}, items=[])

    findings = list(
        db.scalars(
            select(LiveFinding).where(
                LiveFinding.organization_id == ctx.organization_id,
                LiveFinding.bank_id == bank.id,
                LiveFinding.reporting_period_id == period.id,
                LiveFinding.status.in_(_OPEN_STATUSES),
                LiveFinding.severity.in_(_ALERT_SEVERITIES),
            )
        )
    )
    findings.sort(
        key=lambda finding: (
            _SEVERITY_RANK.get(finding.severity, 99),
            finding.created_at,
        )
    )

    by_severity: dict[str, int] = {}
    by_module: dict[str, int] = {}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        by_module[finding.module] = by_module.get(finding.module, 0) + 1

    items = [
        AlertItemRead(
            finding_id=finding.id,
            module=finding.module,  # type: ignore[arg-type]
            severity=finding.severity,  # type: ignore[arg-type]
            rule_id=finding.rule_id,
            message=finding.message,
            metric=finding.metric,
            created_at=finding.created_at,
        )
        for finding in findings[:limit]
    ]
    return BankAlertsRead(
        bank_id=bank.id,
        total=len(findings),
        by_severity=by_severity,
        by_module=by_module,
        items=items,
    )


def _latest_period(db: Session, ctx: TenantContext, bank: Bank) -> BankReportingPeriod | None:
    return db.scalar(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
