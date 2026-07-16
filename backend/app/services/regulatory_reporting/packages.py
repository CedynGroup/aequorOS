"""Package list/read views and the registry template listing."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RegulatoryPackage
from app.schemas.regulatory_reporting import (
    RegulatoryPackageListRead,
    RegulatoryPackageRead,
    ReturnTemplateListRead,
    ReturnTemplateRead,
)
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
    bank_id: UUID,
    *,
    return_code: str | None = None,
    reporting_date: date | None = None,
    status: str | None = None,
    include_superseded: bool = True,
    limit: int = 25,
    offset: int = 0,
) -> RegulatoryPackageListRead:
    bank = get_bank_or_404(db, ctx, bank_id)
    conditions = (
        RegulatoryPackage.organization_id == ctx.organization_id,
        RegulatoryPackage.bank_id == bank.id,
    )
    if return_code is not None:
        conditions += (RegulatoryPackage.return_code == return_code,)
    if reporting_date is not None:
        conditions += (RegulatoryPackage.reporting_date == reporting_date,)
    if status is not None:
        conditions += (RegulatoryPackage.status == status,)
    if not include_superseded:
        conditions += (RegulatoryPackage.status != "superseded",)
    total = (
        db.scalar(select(func.count()).select_from(RegulatoryPackage).where(*conditions)) or 0
    )
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
    return RegulatoryPackageListRead(
        bank_id=bank.id,
        packages=[read_summary(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


def get_package(
    db: Session, ctx: TenantContext, bank_id: UUID, package_id: UUID
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
            )
            for definition in REGISTRY.values()
        ]
    )
