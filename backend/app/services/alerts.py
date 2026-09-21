"""Bank alerts: the open critical/high current live findings.

Aggregates the ``live_findings`` the pipeline reconciles into a compact,
severity-ranked feed for the alerts bell. Read-only; the pipeline owns the
finding lifecycle (open on breach, superseded on clear).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module, Permission, Sensitivity
from app.models import Bank, LiveFinding
from app.schemas.live import AlertItemRead, BankAlertsRead
from app.services import scoped_authorization

_ALERT_SEVERITIES = ("critical", "high")
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_OPEN_STATUSES = ("open", "needs_review")


def get_bank_alerts(
    db: Session, ctx: TenantContext, bank_id: str, *, limit: int = 50
) -> BankAlertsRead:
    bank = _get_bank_or_404(db, ctx, bank_id)

    query = select(LiveFinding).where(
        LiveFinding.organization_id == ctx.organization_id,
        LiveFinding.bank_id == bank.id,
        LiveFinding.status.in_(_OPEN_STATUSES),
        LiveFinding.severity.in_(_ALERT_SEVERITIES),
    )
    for engine, module in (
        ("liquidity", Module.LIQUIDITY),
        ("irr", Module.IRRBB),
        ("fx", Module.FX),
        ("ftp", Module.FTP),
        ("forecast", Module.FORECASTING),
    ):
        decision = scoped_authorization.evaluate_bank_permission(
            db,
            ctx,
            bank,
            permission=Permission.VIEW,
            module=module,
            sensitivity=Sensitivity.AGGREGATED,
            surface="bank_alerts",
        )
        if decision is None or not decision.allowed:
            query = query.where(LiveFinding.module != engine)
    findings = list(db.scalars(query))
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


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
