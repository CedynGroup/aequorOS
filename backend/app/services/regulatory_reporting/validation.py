"""Package validation pipeline (docs/regulatory_reporting.md §5, ``validation.py``).

Three deterministic rules run over the generated snapshot:

1. **Completeness** — every declared (non-optional) section carries rows and
   the snapshot header fields are present.
2. **Internal consistency** — every section total that declares
   ``equals_sum_of_rows`` cross-foots exactly against its row values.
3. **Prior-period movement** — headline ``totals`` are compared against the
   latest submitted/acknowledged package of the same return at an earlier
   reporting date; swings above 25% are flagged as WARNING.

Each finding is ``{rule, severity, detail}`` with severity INFO/WARNING/ERROR.
The report is persisted onto ``validation_report``; a clean run (no ERROR)
flips ``generated -> validated``, otherwise the package stays (or returns to)
``generated`` with the errors listed. ERROR findings block approval requests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RegulatoryPackage
from app.schemas.regulatory_reporting import RegulatoryPackageRead
from app.services.audit import record_event
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_package_or_404,
    read_package,
)

RULE_VERSION = "regulatory-package-validation-v1.0.0"
COMPLETENESS_RULE = "package.sections_complete"
CONSISTENCY_RULE = "package.totals_consistent"
MOVEMENT_RULE = "package.prior_period_movement"
MOVEMENT_THRESHOLD_PCT = Decimal("25")
_MOVEMENT_STATUSES = ("submitted", "acknowledged")
_VALIDATABLE_STATUSES = ("generated", "validated")
_CONSISTENCY_TOLERANCE = Decimal("0.0001")
_HUNDRED = Decimal("100")


def _finding(rule: str, severity: str, detail: str) -> dict[str, str]:
    return {"rule": rule, "severity": severity, "detail": detail}


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _completeness_findings(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for field in ("reporting_date", "institution", "sections", "totals"):
        if not snapshot.get(field):
            findings.append(
                _finding(
                    COMPLETENESS_RULE,
                    "ERROR",
                    f"The snapshot is missing its '{field}' block.",
                )
            )
    sections = snapshot.get("sections") or []
    populated = 0
    for section in sections:
        if section.get("rows"):
            populated += 1
            continue
        severity = "INFO" if section.get("optional") else "ERROR"
        qualifier = "optional " if section.get("optional") else ""
        findings.append(
            _finding(
                COMPLETENESS_RULE,
                severity,
                f"The {qualifier}section '{section.get('code')}' has no rows.",
            )
        )
    if not any(finding["severity"] == "ERROR" for finding in findings):
        findings.append(
            _finding(
                COMPLETENESS_RULE,
                "INFO",
                f"All required sections contain data ({populated} of {len(sections)} "
                "sections populated).",
            )
        )
    return findings


def _consistency_findings(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    declared = 0
    for section in snapshot.get("sections") or []:
        total = section.get("total")
        if not total or not total.get("equals_sum_of_rows"):
            continue
        declared += 1
        total_value = _decimal_or_none(total.get("value"))
        row_values = [_decimal_or_none(row.get("value")) for row in section.get("rows") or []]
        if total_value is None or any(value is None for value in row_values):
            findings.append(
                _finding(
                    CONSISTENCY_RULE,
                    "ERROR",
                    f"Section '{section.get('code')}' declares a cross-foot total but "
                    "carries non-numeric values.",
                )
            )
            continue
        row_sum = sum((value for value in row_values if value is not None), Decimal("0"))
        if abs(row_sum - total_value) > _CONSISTENCY_TOLERANCE:
            findings.append(
                _finding(
                    CONSISTENCY_RULE,
                    "ERROR",
                    f"Section '{section.get('code')}' total {total_value} does not equal "
                    f"the sum of its rows {row_sum}.",
                )
            )
    if not any(finding["severity"] == "ERROR" for finding in findings):
        findings.append(
            _finding(
                CONSISTENCY_RULE,
                "INFO",
                f"All {declared} declared section totals cross-foot against their rows.",
            )
        )
    return findings


def _prior_package(db: Session, package: RegulatoryPackage) -> RegulatoryPackage | None:
    return db.scalar(
        select(RegulatoryPackage)
        .where(
            RegulatoryPackage.organization_id == package.organization_id,
            RegulatoryPackage.bank_id == package.bank_id,
            RegulatoryPackage.return_code == package.return_code,
            RegulatoryPackage.reporting_date < package.reporting_date,
            RegulatoryPackage.status.in_(_MOVEMENT_STATUSES),
        )
        .order_by(
            RegulatoryPackage.reporting_date.desc(),
            RegulatoryPackage.version.desc(),
        )
        .limit(1)
    )


def _movement_findings(db: Session, package: RegulatoryPackage) -> list[dict[str, str]]:
    prior = _prior_package(db, package)
    if prior is None:
        return [
            _finding(
                MOVEMENT_RULE,
                "INFO",
                "No prior submitted or acknowledged package of this return exists; "
                "the movement check has nothing to compare against.",
            )
        ]
    prior_totals = {
        row.get("code"): _decimal_or_none(row.get("value"))
        for row in prior.snapshot.get("totals") or []
    }
    findings: list[dict[str, str]] = []
    for row in package.snapshot.get("totals") or []:
        code = row.get("code")
        current = _decimal_or_none(row.get("value"))
        previous = prior_totals.get(code)
        if current is None or previous is None or previous == 0:
            continue
        movement_pct = abs((current - previous) / previous) * _HUNDRED
        if movement_pct > MOVEMENT_THRESHOLD_PCT:
            findings.append(
                _finding(
                    MOVEMENT_RULE,
                    "WARNING",
                    f"'{code}' moved {movement_pct.quantize(Decimal('0.01'))}% versus the "
                    f"{prior.reporting_date.isoformat()} package "
                    f"({previous} -> {current}); movements above "
                    f"{MOVEMENT_THRESHOLD_PCT}% need explanation.",
                )
            )
    if not findings:
        findings.append(
            _finding(
                MOVEMENT_RULE,
                "INFO",
                f"No headline total moved more than {MOVEMENT_THRESHOLD_PCT}% versus the "
                f"{prior.reporting_date.isoformat()} package.",
            )
        )
    return findings


def run_validation_rules(db: Session, package: RegulatoryPackage) -> list[dict[str, str]]:
    """Pure rule pipeline over one package snapshot; returns ordered findings."""
    return [
        *_completeness_findings(package.snapshot),
        *_consistency_findings(package.snapshot),
        *_movement_findings(db, package),
    ]


def validate_package(
    db: Session, ctx: TenantContext, bank_id: UUID, package_id: UUID
) -> RegulatoryPackageRead:
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    if package.status not in _VALIDATABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Only generated or validated packages can be validated; this package "
                f"is '{package.status}'."
            ),
        )

    findings = run_validation_rules(db, package)
    error_count = sum(1 for finding in findings if finding["severity"] == "ERROR")
    warning_count = sum(1 for finding in findings if finding["severity"] == "WARNING")
    info_count = sum(1 for finding in findings if finding["severity"] == "INFO")
    passed = error_count == 0
    package.validation_report = {
        "rule_version": RULE_VERSION,
        "validated_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "findings": findings,
    }
    package.status = "validated" if passed else "generated"
    record_event(
        db,
        ctx,
        event_type="regulatory_package.validated",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "return_code": package.return_code,
            "reporting_date": package.reporting_date.isoformat(),
            "version": package.version,
            "passed": passed,
            "error_count": error_count,
            "warning_count": warning_count,
        },
    )
    db.commit()
    return read_package(db, package)
