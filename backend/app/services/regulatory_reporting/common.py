"""Shared tenant-scoped lookups and read builders for the reporting hub."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryPackage, RegulatoryPackageApproval
from app.schemas.regulatory_reporting import (
    PackageApprovalRead,
    PackageSourceRunRead,
    RegulatoryPackageRead,
    RegulatoryPackageSummaryRead,
    ValidationReportRead,
)


def require_actor(ctx: TenantContext) -> UUID:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header is required."
        )
    return ctx.actor_user_id


def get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def get_period_for_reporting_date_or_404(
    db: Session, ctx: TenantContext, bank: Bank, reporting_date: date
) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.period_end == reporting_date,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No reporting period ends on {reporting_date.isoformat()} for this bank. "
                "Create the reporting period and compute its regulatory inputs before "
                "generating the return."
            ),
        )
    return period


def get_package_or_404(
    db: Session, ctx: TenantContext, bank_id: UUID, package_id: UUID
) -> RegulatoryPackage:
    package = db.scalar(
        select(RegulatoryPackage).where(
            RegulatoryPackage.id == package_id,
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank_id,
        )
    )
    if package is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory package not found."
        )
    return package


def _validation_passed(package: RegulatoryPackage) -> bool | None:
    report = package.validation_report
    if report is None:
        return None
    return bool(report.get("passed"))


def read_summary(package: RegulatoryPackage) -> RegulatoryPackageSummaryRead:
    return RegulatoryPackageSummaryRead(
        id=package.id,
        bank_id=package.bank_id,
        return_family=package.return_family,  # type: ignore[arg-type]
        return_code=package.return_code,
        reporting_date=package.reporting_date,
        frequency=package.frequency,  # type: ignore[arg-type]
        status=package.status,  # type: ignore[arg-type]
        version=package.version,
        supersedes_id=package.supersedes_id,
        generated_by=package.generated_by,
        generated_at=package.generated_at,
        validation_passed=_validation_passed(package),
        notes=package.notes,
        created_at=package.created_at,
        updated_at=package.updated_at,
    )


def read_package(db: Session, package: RegulatoryPackage) -> RegulatoryPackageRead:
    approvals = list(
        db.scalars(
            select(RegulatoryPackageApproval)
            .where(
                RegulatoryPackageApproval.package_id == package.id,
                RegulatoryPackageApproval.organization_id == package.organization_id,
            )
            .order_by(
                RegulatoryPackageApproval.occurred_at,
                RegulatoryPackageApproval.id,
            )
        )
    )
    report_payload: dict[str, Any] | None = package.validation_report
    return RegulatoryPackageRead(
        **read_summary(package).model_dump(),
        snapshot=package.snapshot,
        source_runs=[PackageSourceRunRead(**entry) for entry in package.source_runs],
        validation_report=(
            ValidationReportRead(**report_payload) if report_payload is not None else None
        ),
        approvals=[PackageApprovalRead.model_validate(row) for row in approvals],
    )
