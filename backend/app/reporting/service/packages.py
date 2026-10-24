"""Package list/read views and the registry template listing."""

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.regulatory_instruments import instrument_status_on
from app.policy.public import regulatory_parameters
from app.reporting.models.filing_workflow import PackageWorkflowStage
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.schemas.packages import (
    RegulatoryPackageListRead,
    RegulatoryPackageRead,
    ReturnTemplateListRead,
    ReturnTemplateRead,
)
from app.reporting.service import family_access
from app.reporting.service.common import (
    get_bank_or_404,
    get_package_or_404,
    read_package,
    read_summary,
)
from app.reporting.service.eligibility import resolve_eligibility
from app.reporting.service.registry import REGISTRY


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
    # Hidden families must be excluded before counts and pagination: even a
    # package's existence and filing status disclose protected return state.
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
    bank = get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    family_access.require_view(db, ctx, bank, package)
    return read_package(db, package)


def list_return_templates(
    db: Session, ctx: TenantContext, bank_id: str | None = None, *, as_of: date | None = None
) -> ReturnTemplateListRead:
    today = as_of or date.today()
    bank = get_bank_or_404(db, ctx, bank_id) if bank_id is not None else None
    eligibility = resolve_eligibility(db, ctx, bank, as_of=today) if bank is not None else None
    resolver = (
        cast(regulatory_parameters.PrefetchedParameterResolver, eligibility.parameter_resolver)
        if eligibility is not None
        else None
    )
    templates: list[ReturnTemplateRead] = []
    for definition in REGISTRY.values():
        resolved = (
            resolver.try_resolve(definition.effective_from_parameter, as_of=today)
            if resolver is not None and definition.effective_from_parameter
            else None
        )
        approval = (
            next(
                (
                    row
                    for row in resolver.rows
                    if resolved is not None and str(row.id) == resolved.parameter_id
                ),
                None,
            )
            if resolver is not None
            else None
        )
        default_date = definition.effective_from
        for spec in regulatory_parameters.ICAAP_FILING_SEED_PARAMETERS:
            if spec.param_code == definition.effective_from_parameter and spec.value_json:
                raw_date: object = spec.value_json.get("date")
                if isinstance(raw_date, str):
                    default_date = date.fromisoformat(raw_date)
        templates.append(
            ReturnTemplateRead(
                source_url=definition.source_url,
                default_effective_from=default_date,
                commencement_parameter=definition.effective_from_parameter,
                commencement_approved_by=approval.approved_by if approval is not None else None,
                commencement_approved_at=approval.approved_at if approval is not None else None,
                calculations=[
                    f"{metric}: {method}" for metric, method in definition.declared_methodologies
                ]
                + [f"limit parameter: {code}" for code in definition.limit_parameters],
                code=definition.code,
                family=definition.family,
                title=definition.title,
                regulator=definition.regulator,
                directive_citation=definition.directive_citation,
                instrument_status=(
                    eligibility.instrument_status(definition, today)
                    if eligibility is not None
                    else instrument_status_on(
                        definition.instrument_status, definition.effective_from, today
                    )
                ),
                effective_from=(
                    eligibility.effective_from(definition)
                    if eligibility is not None
                    else definition.effective_from
                ),
                frequency=definition.frequency,
                generator=definition.generator,
                template_id=definition.template_id,
                fidelity=definition.fidelity,
                default_channel=definition.default_channel,
                supports_working_copy=(
                    definition.generator == "bog_form" or definition.supports_working_copy
                ),
            )
        )
    return ReturnTemplateListRead(templates=templates)
