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
from app.identity.public import Bank
from app.live.models.live import LiveFinding
from app.live.schemas.live import AlertItemRead, BankAlertsRead
from app.live.service import live_access
from app.policy.domain.regulatory_instruments import forecast_rule_applicable
from app.policy.public import rule_in_force

_ALERT_SEVERITIES = ("critical", "high")
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_OPEN_STATUSES = ("open", "needs_review")


def get_bank_alerts(
    db: Session, ctx: TenantContext, bank_id: str, *, limit: int = 50
) -> BankAlertsRead:
    bank = _get_bank_or_404(db, ctx, bank_id)

    # The one closed engine-to-module map decides what this caller may read;
    # the positive include list filters counts, the limit and every item in
    # SQL before anything is serialized.
    allowed = live_access.allowed_modules(
        db, ctx, bank, tuple(live_access.FRESHNESS_VIEW_MODULES), surface="bank_alerts"
    )
    query = select(LiveFinding).where(
        LiveFinding.organization_id == ctx.organization_id,
        LiveFinding.bank_id == bank.id,
        LiveFinding.status.in_(_OPEN_STATUSES),
        LiveFinding.severity.in_(_ALERT_SEVERITIES),
        LiveFinding.module.in_(allowed),
    )
    findings = list(db.scalars(query))
    if any(finding.module == "forecast" for finding in findings):
        liquidity_in_force = rule_in_force(db, ctx, bank, "LCR-NSFR")
        findings = [
            finding
            for finding in findings
            if finding.module != "forecast"
            or forecast_rule_applicable(finding.rule_id, liquidity_in_force)
        ]
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
