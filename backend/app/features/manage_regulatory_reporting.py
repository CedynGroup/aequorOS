"""Regulatory Reporting & Submission Hub API (docs/regulatory_reporting.md §6).

Export and channel submission are stubbed with 501 until the export/submission
wave ships artifact rendering and concrete channels; every other endpoint is
live. Credentials on channel configs are write-only: responses expose only the
fingerprint, never the material.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.regulatory_reporting import (
    ChannelConfigPut,
    ChannelConfigRead,
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    PackageSubmitCreate,
    RegulatoryPackageCreate,
    RegulatoryPackageListRead,
    RegulatoryPackageRead,
    ReportingObligationListRead,
    ReturnTemplateListRead,
    SubmissionEventListRead,
)
from app.services import regulatory_reporting

router = APIRouter(tags=["regulatory-reporting"])

type ChannelPath = Literal["orass_sandbox", "email", "manual"]
type PackageStatusFilter = Literal[
    "draft",
    "generated",
    "validated",
    "pending_approval",
    "approved",
    "submitted",
    "acknowledged",
    "rejected",
    "superseded",
]


@router.get(
    "/banks/{bank_id}/reporting-obligations",
    response_model=ReportingObligationListRead,
    operation_id="listReportingObligations",
)
def list_reporting_obligations(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    horizon_months: Annotated[int, Query(ge=1, le=24)] = 3,
) -> ReportingObligationListRead:
    return regulatory_reporting.list_obligations(db, ctx, bank_id, horizon_months)


@router.get(
    "/banks/{bank_id}/regulatory-packages",
    response_model=RegulatoryPackageListRead,
    operation_id="listRegulatoryPackages",
)
def list_regulatory_packages(  # noqa: PLR0913
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    return_code: Annotated[str | None, Query(max_length=40)] = None,
    reporting_date: Annotated[date | None, Query()] = None,
    package_status: Annotated[PackageStatusFilter | None, Query(alias="status")] = None,
    include_superseded: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RegulatoryPackageListRead:
    return regulatory_reporting.list_packages(
        db,
        ctx,
        bank_id,
        return_code=return_code,
        reporting_date=reporting_date,
        status=package_status,
        include_superseded=include_superseded,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/banks/{bank_id}/regulatory-packages",
    response_model=RegulatoryPackageRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createRegulatoryPackage",
)
def create_regulatory_package(
    bank_id: UUID,
    payload: RegulatoryPackageCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryPackageRead:
    return regulatory_reporting.generate_package(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}",
    response_model=RegulatoryPackageRead,
    operation_id="getRegulatoryPackage",
)
def get_regulatory_package(
    bank_id: UUID, package_id: UUID, db: DbSession, ctx: Tenant
) -> RegulatoryPackageRead:
    return regulatory_reporting.get_package(db, ctx, bank_id, package_id)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/validate",
    response_model=RegulatoryPackageRead,
    operation_id="validateRegulatoryPackage",
)
def validate_regulatory_package(
    bank_id: UUID, package_id: UUID, db: DbSession, ctx: MutationTenant
) -> RegulatoryPackageRead:
    return regulatory_reporting.validate_package(db, ctx, bank_id, package_id)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/request-approval",
    response_model=RegulatoryPackageRead,
    operation_id="requestPackageApproval",
)
def request_package_approval(
    bank_id: UUID,
    package_id: UUID,
    payload: PackageApprovalRequestCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryPackageRead:
    return regulatory_reporting.request_approval(db, ctx, bank_id, package_id, payload)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/decide-approval",
    response_model=RegulatoryPackageRead,
    operation_id="decidePackageApproval",
)
def decide_package_approval(
    bank_id: UUID,
    package_id: UUID,
    payload: PackageApprovalDecisionCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryPackageRead:
    return regulatory_reporting.decide_approval(db, ctx, bank_id, package_id, payload)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/export",
    operation_id="exportRegulatoryPackage",
)
def export_regulatory_package(
    bank_id: UUID,
    package_id: UUID,
    kind: Annotated[Literal["xlsx", "csv", "pdf"], Query()],
    db: DbSession,
    ctx: MutationTenant,
) -> None:
    _ = (bank_id, package_id, kind, db, ctx)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Export/submission wave pending: xlsx/csv/pdf artifact rendering ships in "
            "the export wave. The package snapshot is already available via "
            "getRegulatoryPackage."
        ),
    )


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/submit",
    operation_id="submitRegulatoryPackage",
)
def submit_regulatory_package(
    bank_id: UUID,
    package_id: UUID,
    payload: PackageSubmitCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> None:
    _ = (bank_id, package_id, payload, db, ctx)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Export/submission wave pending: the ORASS sandbox and email/manual "
            "channels ship in the submission wave. Approved packages remain queued "
            "under their current status."
        ),
    )


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/submission-events",
    response_model=SubmissionEventListRead,
    operation_id="listSubmissionEvents",
)
def list_submission_events(  # noqa: PLR0913
    bank_id: UUID,
    package_id: UUID,
    db: DbSession,
    ctx: Tenant,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SubmissionEventListRead:
    return regulatory_reporting.list_submission_events(
        db, ctx, bank_id, package_id, limit=limit, offset=offset
    )


@router.get(
    "/regulatory-reporting/templates",
    response_model=ReturnTemplateListRead,
    operation_id="listReturnTemplates",
)
def list_return_templates(ctx: Tenant) -> ReturnTemplateListRead:
    _ = ctx
    return regulatory_reporting.list_return_templates()


@router.get(
    "/banks/{bank_id}/regulatory-reporting/channel-configs/{channel}",
    response_model=ChannelConfigRead,
    operation_id="getChannelConfig",
)
def get_channel_config(
    bank_id: UUID, channel: ChannelPath, db: DbSession, ctx: Tenant
) -> ChannelConfigRead:
    return regulatory_reporting.get_channel_config(db, ctx, bank_id, channel)


@router.put(
    "/banks/{bank_id}/regulatory-reporting/channel-configs/{channel}",
    response_model=ChannelConfigRead,
    operation_id="putChannelConfig",
)
def put_channel_config(
    bank_id: UUID,
    channel: ChannelPath,
    payload: ChannelConfigPut,
    db: DbSession,
    ctx: MutationTenant,
) -> ChannelConfigRead:
    return regulatory_reporting.put_channel_config(db, ctx, bank_id, channel, payload)
