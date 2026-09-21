"""Package list/read views and the registry template listing."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import PackageWorkflowStage, RegulatoryPackage
from app.schemas.regulatory_reporting import (
    RegulatoryPackageListRead,
    RegulatoryPackageRead,
    ReturnTemplateListRead,
    ReturnTemplateRead,
)
from app.services.regulatory_reporting import family_access
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_package_or_404,
    read_package,
    read_summary,
)
from app.services.regulatory_reporting.registry import REGISTRY


def list_packages(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    return_code: str | None = None,
    return_family: str | None = None,
    reporting_date: date | None = None,
    reporting_date_from: date | None = None,
    reporting_date_to: date | None = None,
    status: str | None = None,
    basis: str | None = None,
    include_superseded: bool = True,
    limit: int = 25,
    offset: int = 0,
) -> RegulatoryPackageListRead:
    bank = get_bank_or_404(db, ctx, bank_id)
    conditions = (
        RegulatoryPackage.organization_id == ctx.organization_id,
        RegulatoryPackage.bank_id == bank.id,
    )
    # A package of a GATED family that this principal may not see is not merely
    # unopenable — it must not appear in the list at all, because a row showing
    # "ICAAP-REPORT, FY2026, submitted" is itself the disclosure. Applied as one
    # NOT-IN over the query rather than per row (ICAAP P3 §4.2).
    hidden = family_access.hidden_families(db, ctx, bank)
    if hidden:
        conditions += (RegulatoryPackage.return_family.notin_(sorted(hidden)),)
    if return_code is not None:
        conditions += (RegulatoryPackage.return_code == return_code,)
    if return_family is not None:
        conditions += (RegulatoryPackage.return_family == return_family,)
    if reporting_date is not None:
        conditions += (RegulatoryPackage.reporting_date == reporting_date,)
    if reporting_date_from is not None:
        conditions += (RegulatoryPackage.reporting_date >= reporting_date_from,)
    if reporting_date_to is not None:
        conditions += (RegulatoryPackage.reporting_date <= reporting_date_to,)
    if status is not None:
        conditions += (RegulatoryPackage.status == status,)
    if basis is not None:
        conditions += (RegulatoryPackage.basis == basis,)
    if not include_superseded:
        conditions += (RegulatoryPackage.status != "superseded",)
    total = db.scalar(select(func.count()).select_from(RegulatoryPackage).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(RegulatoryPackage)
            .where(*conditions)
            .order_by(
                RegulatoryPackage.reporting_date.desc(),
                RegulatoryPackage.return_code,
                RegulatoryPackage.version.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    # One query for every row's stage title. The chain is per-bank DATA — a
    # bank may call stage 3 "Validator" or "Compliance Sign-off" — so the title
    # is read, never inferred from the sequence number.
    waiting = {row.id: row.current_stage_seq for row in rows if row.current_stage_seq}
    titles: dict[UUID, str] = {}
    if waiting:
        stage_rows = db.execute(
            select(
                PackageWorkflowStage.package_id,
                PackageWorkflowStage.seq,
                PackageWorkflowStage.title,
            ).where(
                PackageWorkflowStage.organization_id == ctx.organization_id,
                PackageWorkflowStage.package_id.in_(waiting.keys()),
            )
        ).all()
        titles = {
            package_id: title
            for package_id, seq, title in stage_rows
            if waiting.get(package_id) == seq
        }

    return RegulatoryPackageListRead(
        bank_id=bank.id,
        packages=[read_summary(row, stage_title=titles.get(row.id)) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


def get_package(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID
) -> RegulatoryPackageRead:
    get_bank_or_404(db, ctx, bank_id)
    return read_package(db, get_package_or_404(db, ctx, bank_id, package_id))


def list_return_templates() -> ReturnTemplateListRead:
    return ReturnTemplateListRead(
        templates=[
            ReturnTemplateRead(
                code=definition.code,
                family=definition.family,
                title=definition.title,
                regulator=definition.regulator,
                directive_citation=definition.directive_citation,
                frequency=definition.frequency,
                generator=definition.generator,
                template_id=definition.template_id,
                fidelity=definition.fidelity,
                default_channel=definition.default_channel,
                supports_working_copy=(
                    definition.generator == "bog_form" or definition.supports_working_copy
                ),
            )
            for definition in REGISTRY.values()
        ]
    )
