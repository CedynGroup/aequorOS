"""Regulatory Reporting & Submission Hub API (docs/regulatory_reporting.md §6).

All endpoints are live, including export (artifact rendering via the exports
seam), artifact download (outputs tier), and channel submission (ORASS sandbox
simulator + BG/FMD/2026/07 email fallback + manual record). Credentials on
channel configs are write-only: responses expose only the fingerprint, never
the material.

Two artifact surfaces, and the difference decides what a bank files. The
``regulatory-artifacts`` routes serve the CANONICAL UNSIGNED export — one row
per kind, upserted. The ``artifact-versions`` routes serve the append-only
chain, including every signed revision an officer's certification pinned. Once
a return is certified the signed revision is the document (see
``services/regulatory_reporting/artifact_versions.py``); the base export stays
retrievable as the pre-signature engine output.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import ApproverTenant, DbSession, MutationTenant, Tenant
from app.features.ingest_data import IngestionStorage
from app.schemas.regulatory_reporting import (
    ChannelConfigPut,
    ChannelConfigRead,
    EmailFallbackInstructionsRead,
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    PackageComparisonRead,
    PackageSubmitCreate,
    PackageVersionChainRead,
    RegulatoryArtifactListRead,
    RegulatoryArtifactRead,
    RegulatoryArtifactVersionListRead,
    RegulatoryPackageCreate,
    RegulatoryPackageListRead,
    RegulatoryPackageRead,
    ReportingObligationListRead,
    ReportingSettingsPut,
    ReportingSettingsRead,
    ResubmissionDecisionCreate,
    ResubmissionRequestCreate,
    ResubmissionRequestListRead,
    ResubmissionRequestRead,
    ReturnAnchorListRead,
    ReturnTemplateListRead,
    SubmissionEventListRead,
    SubmissionPollRead,
)
from app.schemas.report_comparison import ReportComparisonRead, ReportComparisonRequest
from app.services import regulatory_reporting, report_comparison
from app.services.regulatory_reporting import artifact_versions, version_chain
from app.services.regulatory_reporting import workflow as reporting_workflow
from app.storage.client import StorageLocation, StorageNotFoundError

router = APIRouter(tags=["regulatory-reporting"])

_ARTIFACT_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsx_working": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "pdf": "application/pdf",
}

type ChannelPath = Literal["orass_api", "orass_sandbox", "email", "manual"]
type BasisFilter = Literal["solo", "consolidated"]
type PackageStatusFilter = Literal[
    "draft",
    "generated",
    "validated",
    "pending_approval",
    "approved",
    "submitted",
    "acknowledged",
    "rejected",
    "declined",
    "superseded",
]


@router.get(
    "/banks/{bank_id}/reporting-obligations",
    response_model=ReportingObligationListRead,
    operation_id="listReportingObligations",
)
def list_reporting_obligations(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    horizon_months: Annotated[int, Query(ge=1, le=24)] = 3,
) -> ReportingObligationListRead:
    return regulatory_reporting.list_obligations(db, ctx, bank_id, horizon_months)


@router.get(
    "/banks/{bank_id}/return-anchors",
    response_model=ReturnAnchorListRead,
    operation_id="listReturnAnchors",
)
def list_return_anchors(
    bank_id: str,
    return_code: str,
    db: DbSession,
    ctx: Tenant,
    horizon_months: Annotated[int, Query(ge=1, le=24)] = 3,
) -> ReturnAnchorListRead:
    """The reporting dates this return reports on, and what exists for each.

    The dates are the REGULATOR's, derived from the return definition, so this
    is the list a preparer picks a reporting date from — not the bank's ingested
    reporting periods, which are a consequence of data arrival rather than a
    filing calendar (``services/regulatory_reporting/anchors.py``).
    """
    return regulatory_reporting.list_return_anchors(
        db, ctx, bank_id, return_code, horizon_months
    )


@router.get(
    "/banks/{bank_id}/regulatory-packages",
    response_model=RegulatoryPackageListRead,
    operation_id="listRegulatoryPackages",
)
def list_regulatory_packages(  # noqa: PLR0913
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    return_code: Annotated[str | None, Query(max_length=40)] = None,
    return_family: Annotated[str | None, Query(max_length=20)] = None,
    reporting_date: Annotated[date | None, Query()] = None,
    reporting_date_from: Annotated[date | None, Query()] = None,
    reporting_date_to: Annotated[date | None, Query()] = None,
    package_status: Annotated[PackageStatusFilter | None, Query(alias="status")] = None,
    basis: Annotated[BasisFilter | None, Query()] = None,
    include_superseded: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RegulatoryPackageListRead:
    return regulatory_reporting.list_packages(
        db,
        ctx,
        bank_id,
        return_code=return_code,
        return_family=return_family,
        reporting_date=reporting_date,
        reporting_date_from=reporting_date_from,
        reporting_date_to=reporting_date_to,
        status=package_status,
        basis=basis,
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
    bank_id: str,
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
    bank_id: str, package_id: UUID, db: DbSession, ctx: Tenant
) -> RegulatoryPackageRead:
    return regulatory_reporting.get_package(db, ctx, bank_id, package_id)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/validate",
    response_model=RegulatoryPackageRead,
    operation_id="validateRegulatoryPackage",
)
def validate_regulatory_package(
    bank_id: str, package_id: UUID, db: DbSession, ctx: MutationTenant
) -> RegulatoryPackageRead:
    return regulatory_reporting.validate_package(db, ctx, bank_id, package_id)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/request-approval",
    response_model=RegulatoryPackageRead,
    operation_id="requestPackageApproval",
)
def request_package_approval(
    bank_id: str,
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
    bank_id: str,
    package_id: UUID,
    payload: PackageApprovalDecisionCreate,
    db: DbSession,
    ctx: ApproverTenant,
) -> RegulatoryPackageRead:
    return regulatory_reporting.decide_approval(db, ctx, bank_id, package_id, payload)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/export",
    response_model=RegulatoryArtifactRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="exportRegulatoryPackage",
)
def export_regulatory_package(
    bank_id: str,
    package_id: UUID,
    kind: Annotated[
        Literal["xlsx", "xlsx_official", "xlsx_working", "csv", "pdf"],
        Query(
            description=(
                "pdf = values-only submission package (the BoG filing format); "
                "xlsx / xlsx_official = sealed values-only Excel (governance twin of the PDF); "
                "xlsx_working = ALM/Finance working copy with the template's live formulas "
                "(official BoG BSD forms only; never filed); csv = generic sections."
            )
        ),
    ],
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryArtifactRead:
    # "xlsx_official" is the explicit name for the sealed export; it is stored
    # under the historical kind "xlsx" so existing artifacts/signatures keep working.
    resolved: reporting_workflow.ArtifactKind = "xlsx" if kind == "xlsx_official" else kind
    return reporting_workflow.export_package_artifact(db, ctx, bank_id, package_id, resolved)


@router.get(
    "/banks/{bank_id}/regulatory-artifacts/{artifact_id}/download",
    response_class=StreamingResponse,
    operation_id="downloadRegulatoryArtifact",
)
def download_regulatory_artifact(
    bank_id: str,
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


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/artifact-versions",
    response_model=RegulatoryArtifactVersionListRead,
    operation_id="listPackageArtifactVersions",
)
def list_package_artifact_versions(
    bank_id: str,
    package_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> RegulatoryArtifactVersionListRead:
    """Every archived render and signed revision, oldest first.

    The artifact list above is the upserted row per kind — always the unsigned
    base export. This is the chain: the base, then one revision per officer,
    each naming the signature that pinned it.
    """
    return artifact_versions.list_versions(db, ctx, bank_id, package_id)


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/version-chain",
    response_model=PackageVersionChainRead,
    operation_id="getPackageVersionChain",
)
def get_package_version_chain(
    bank_id: str,
    package_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> PackageVersionChainRead:
    """The whole supersession chain, each version with what it can still offer.

    The package list carries statuses and timestamps only, which cannot answer
    what is asked about a superseded filing: who certified it, which file went
    to the regulator, and whether any file survives at all. This adds the
    signatures (withdrawn cycles flagged), both artifact surfaces, and the
    ``has_retrievable_files`` verdict a never-exported version needs.
    """
    return version_chain.get_version_chain(db, ctx, bank_id, package_id)


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/comparison",
    response_model=PackageComparisonRead,
    operation_id="comparePackageVersions",
)
def compare_package_versions(
    bank_id: str,
    package_id: UUID,
    against: Annotated[UUID, Query(description="The package to compare against.")],
    db: DbSession,
    ctx: Tenant,
) -> PackageComparisonRead:
    """Line-item figures diff: the path package is the base, ``against`` the target.

    Computed server-side against the two immutable snapshots, so it is
    available for every version — including one that was never exported — and
    so the comparison an examiner is shown is the one the platform computed.
    """
    return version_chain.compare_versions(db, ctx, bank_id, package_id, against)


@router.get(
    "/banks/{bank_id}/reports/comparison",
    response_model=ReportComparisonRead,
    operation_id="compareReports",
)
def compare_reports(
    bank_id: str,
    query: Annotated[ReportComparisonRequest, Query()],
    db: DbSession,
    ctx: Tenant,
) -> ReportComparisonRead:
    """Line-item delta over two immutable runs of the same module, with favorability.

    ``version`` mode compares two run versions of one reporting period (an
    original filing vs a resubmission); ``period`` mode compares the latest run
    of two reporting periods. Each numeric line carries its absolute and
    percentage delta, direction, and a favorable/adverse/neutral judgment from the
    governed favorable-direction registry. Returns 404 when a run, period or
    version is missing, and 422 when the two sides are not comparable.
    """
    return report_comparison.build_comparison(db, ctx, bank_id, query)


@router.get(
    "/banks/{bank_id}/regulatory-artifact-versions/{version_id}/download",
    response_class=StreamingResponse,
    operation_id="downloadRegulatoryArtifactVersion",
)
def download_regulatory_artifact_version(
    bank_id: str,
    version_id: UUID,
    db: DbSession,
    ctx: Tenant,
    storage: IngestionStorage,
) -> StreamingResponse:
    """Stream one archived revision, with its checksum re-verified first."""
    version, payload = artifact_versions.read_version_bytes(db, ctx, bank_id, version_id, storage)
    filename = PurePosixPath(version.object_path).name
    return StreamingResponse(
        io.BytesIO(payload),
        media_type=_ARTIFACT_MEDIA_TYPES.get(version.kind, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/email-fallback.eml",
    response_class=StreamingResponse,
    operation_id="downloadEmailFallbackEml",
)
def download_email_fallback_eml(
    bank_id: str,
    package_id: UUID,
    db: DbSession,
    ctx: Tenant,
    storage: IngestionStorage,
) -> StreamingResponse:
    """The BG/FMD/2026/07 downtime bundle as a send-ready .eml (RFC 822).

    Subject, body, and attachments come from the same bundle the email
    channel records; the operator opens it in their mail client, confirms the
    recipient (the official downtime address is institution-configured), and
    sends. No SMTP happens server-side.
    """
    import io  # noqa: PLC0415
    from email.message import EmailMessage  # noqa: PLC0415

    from app.services.ingestion import bank_slug  # noqa: PLC0415

    bundle = reporting_workflow.email_fallback_instructions(db, ctx, bank_id, package_id)
    message = EmailMessage()
    message["Subject"] = bundle.subject
    recipient = bundle.recipient_guidance.downtime_return_address
    if recipient:
        message["To"] = recipient
    message.set_content(bundle.instructions + "\n\n" + bundle.penalty_reminder)

    bank = reporting_workflow.get_bank_or_404(db, ctx, bank_id)
    slug = bank_slug(db, bank)
    for attachment in bundle.attachments:
        location = StorageLocation(
            institution_slug=slug, tier="outputs", object_path=attachment.object_path
        )
        try:
            _descriptor, stream = storage.read(location)
        except StorageNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"The {attachment.kind} artifact's stored object was not found; "
                    "re-export the package before downloading the email bundle."
                ),
            ) from exc
        payload = b"".join(stream)
        maintype, _, subtype = _ARTIFACT_MEDIA_TYPES.get(
            attachment.kind, "application/octet-stream"
        ).partition("/")
        message.add_attachment(
            payload, maintype=maintype, subtype=subtype, filename=attachment.filename
        )

    raw = message.as_bytes()
    package_ref = str(package_id)[:8]
    return StreamingResponse(
        io.BytesIO(raw),
        media_type="message/rfc822",
        headers={
            "Content-Disposition": (f'attachment; filename="orass-downtime-{package_ref}.eml"')
        },
    )


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/submit",
    response_model=RegulatoryPackageRead,
    operation_id="submitRegulatoryPackage",
)
def submit_regulatory_package(
    bank_id: str,
    package_id: UUID,
    payload: PackageSubmitCreate,
    db: DbSession,
    ctx: ApproverTenant,
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
    bank_id: str,
    package_id: UUID,
    db: DbSession,
    ctx: ApproverTenant,
) -> SubmissionPollRead:
    """Poll the latest channel submission; records regulator decisions."""
    return reporting_workflow.poll_submission(db, ctx, bank_id, package_id)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/request-resubmission",
    response_model=ResubmissionRequestRead,
    operation_id="requestPackageResubmission",
    status_code=status.HTTP_201_CREATED,
)
def request_package_resubmission(
    bank_id: str,
    package_id: UUID,
    payload: ResubmissionRequestCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> ResubmissionRequestRead:
    """File an ORASS-style resubmission request for a submitted/acknowledged return."""
    return reporting_workflow.request_resubmission(db, ctx, bank_id, package_id, payload)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/resubmission-requests/{request_id}/decide",
    response_model=ResubmissionRequestRead,
    operation_id="decidePackageResubmission",
)
def decide_package_resubmission(  # noqa: PLR0913
    bank_id: str,
    package_id: UUID,
    request_id: UUID,
    payload: ResubmissionDecisionCreate,
    db: DbSession,
    ctx: ApproverTenant,
) -> ResubmissionRequestRead:
    """Record a manual grant/deny (email/manual submissions the regulator decides offline)."""
    return reporting_workflow.decide_resubmission(
        db, ctx, bank_id, package_id, request_id, decision=payload.decision, note=payload.note
    )


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/resubmission-requests",
    response_model=ResubmissionRequestListRead,
    operation_id="listResubmissionRequests",
)
def list_resubmission_requests(
    bank_id: str,
    package_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> ResubmissionRequestListRead:
    return reporting_workflow.list_resubmission_requests(db, ctx, bank_id, package_id)


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/artifacts",
    response_model=RegulatoryArtifactListRead,
    operation_id="listPackageArtifacts",
)
def list_package_artifacts(
    bank_id: str,
    package_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> RegulatoryArtifactListRead:
    """Persisted artifact list for a package (never session-local)."""
    artifacts = reporting_workflow.list_package_artifacts(db, ctx, bank_id, package_id)
    return RegulatoryArtifactListRead(
        package_id=package_id,
        artifacts=[RegulatoryArtifactRead.model_validate(artifact) for artifact in artifacts],
    )


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/email-fallback-instructions",
    response_model=EmailFallbackInstructionsRead,
    operation_id="getEmailFallbackInstructions",
)
def get_email_fallback_instructions(
    bank_id: str,
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
    bank_id: str,
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
    bank_id: str, channel: ChannelPath, db: DbSession, ctx: Tenant
) -> ChannelConfigRead:
    return regulatory_reporting.get_channel_config(db, ctx, bank_id, channel)


@router.put(
    "/banks/{bank_id}/regulatory-reporting/channel-configs/{channel}",
    response_model=ChannelConfigRead,
    operation_id="putChannelConfig",
)
def put_channel_config(
    bank_id: str,
    channel: ChannelPath,
    payload: ChannelConfigPut,
    db: DbSession,
    ctx: MutationTenant,
) -> ChannelConfigRead:
    return regulatory_reporting.put_channel_config(db, ctx, bank_id, channel, payload)


@router.get(
    "/banks/{bank_id}/regulatory-reporting/settings",
    response_model=ReportingSettingsRead,
    operation_id="getReportingSettings",
)
def get_reporting_settings(bank_id: str, db: DbSession, ctx: Tenant) -> ReportingSettingsRead:
    """The per-bank deadline-override map (empty when never configured)."""
    return regulatory_reporting.get_reporting_settings(db, ctx, bank_id)


@router.put(
    "/banks/{bank_id}/regulatory-reporting/settings",
    response_model=ReportingSettingsRead,
    operation_id="putReportingSettings",
)
def put_reporting_settings(
    bank_id: str,
    payload: ReportingSettingsPut,
    db: DbSession,
    ctx: MutationTenant,
) -> ReportingSettingsRead:
    """Set per-bank monthly-deadline overrides ({return_code: day_of_month})."""
    return regulatory_reporting.put_reporting_settings(db, ctx, bank_id, payload)
