"""Regulatory Reporting & Submission Hub API (docs/regulatory_reporting.md §6).

All endpoints are live, including export (artifact rendering via the exports
seam), artifact download (outputs tier), and channel submission (ORASS sandbox
simulator + BG/FMD/2026/07 email fallback + manual record). Credentials on
channel configs are write-only: responses expose only the fingerprint, never
the material.
"""

from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import DbSession, MutationTenant, Tenant
from app.features.ingest_data import IngestionStorage
from app.schemas.regulatory_reporting import (
    ChannelConfigPut,
    ChannelConfigRead,
    EmailFallbackInstructionsRead,
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    PackageSubmitCreate,
    RegulatoryArtifactRead,
    RegulatoryPackageCreate,
    RegulatoryPackageListRead,
    RegulatoryPackageRead,
    ReportingObligationListRead,
    ReturnTemplateListRead,
    SubmissionEventListRead,
    SubmissionPollRead,
)
from app.services import regulatory_reporting
from app.services.regulatory_reporting import workflow as reporting_workflow
from app.storage.client import StorageLocation, StorageNotFoundError

router = APIRouter(tags=["regulatory-reporting"])

_ARTIFACT_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "pdf": "application/pdf",
}

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
    response_model=RegulatoryArtifactRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="exportRegulatoryPackage",
)
def export_regulatory_package(
    bank_id: UUID,
    package_id: UUID,
    kind: Annotated[Literal["xlsx", "csv", "pdf"], Query()],
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryArtifactRead:
    return reporting_workflow.export_package_artifact(db, ctx, bank_id, package_id, kind)


@router.get(
    "/banks/{bank_id}/regulatory-artifacts/{artifact_id}/download",
    response_class=StreamingResponse,
    operation_id="downloadRegulatoryArtifact",
)
def download_regulatory_artifact(
    bank_id: UUID,
    artifact_id: UUID,
    db: DbSession,
    ctx: Tenant,
    storage: IngestionStorage,
) -> StreamingResponse:
    """Stream one exported artifact from the outputs tier."""
    artifact, slug = reporting_workflow.prepare_artifact_download(db, ctx, bank_id, artifact_id)
    location = StorageLocation(
        institution_slug=slug, tier="outputs", object_path=artifact.object_path
    )
    try:
        _descriptor, stream = storage.read(location)
    except StorageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The artifact's stored object was not found in the outputs tier.",
        ) from exc
    filename = PurePosixPath(artifact.object_path).name
    return StreamingResponse(
        stream,
        media_type=_ARTIFACT_MEDIA_TYPES.get(artifact.kind, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/submit",
    response_model=RegulatoryPackageRead,
    operation_id="submitRegulatoryPackage",
)
def submit_regulatory_package(
    bank_id: UUID,
    package_id: UUID,
    payload: PackageSubmitCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryPackageRead:
    """Submit an approved package via the requested (or registry-default) channel."""
    return reporting_workflow.submit_package_via_channel(
        db, ctx, bank_id, package_id, channel_override=payload.channel
    )


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/poll",
    response_model=SubmissionPollRead,
    operation_id="pollRegulatorySubmission",
)
def poll_regulatory_submission(
    bank_id: UUID,
    package_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> SubmissionPollRead:
    """Poll the latest channel submission; records regulator decisions."""
    return reporting_workflow.poll_submission(db, ctx, bank_id, package_id)


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/email-fallback-instructions",
    response_model=EmailFallbackInstructionsRead,
    operation_id="getEmailFallbackInstructions",
)
def get_email_fallback_instructions(
    bank_id: UUID,
    package_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> EmailFallbackInstructionsRead:
    """Preview the BG/FMD/2026/07 downtime email bundle without submitting."""
    return reporting_workflow.email_fallback_instructions(db, ctx, bank_id, package_id)


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
