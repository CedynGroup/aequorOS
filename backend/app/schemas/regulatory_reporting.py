from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

type ReturnFamily = Literal[
    "liquidity", "capital", "irrbb", "fx", "icaap_stress", "corporate", "large_exposures", "dbk"
]
type ReturnFrequency = Literal["monthly", "quarterly", "semiannual", "annual", "daily"]
type ReturnBasis = Literal["solo", "consolidated"]
type PackageStatus = Literal[
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
type ArtifactKind = Literal["xlsx", "csv", "pdf"]
type ChannelCode = Literal["orass_api", "orass_sandbox", "email", "manual"]
type SubmissionEventType = Literal[
    "submitted", "status_poll", "acknowledged", "rejected", "declined"
]
type ApprovalAction = Literal["requested", "approved", "rejected"]
type ResubmissionStatus = Literal["requested", "granted", "denied"]
type ApprovalDecision = Literal["approved", "rejected"]
type ValidationSeverity = Literal["INFO", "WARNING", "ERROR"]
type FidelityGrade = Literal["CONFIRMED", "PARTIAL", "REPRESENTATIVE"]
type ObligationRag = Literal["overdue", "due_soon", "on_track"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegulatoryPackageCreate(ClosedModel):
    return_code: str = Field(min_length=1, max_length=40)
    reporting_date: date
    basis: ReturnBasis = "solo"
    notes: str | None = Field(default=None, max_length=2000)


class PackageSourceRunRead(ClosedModel):
    module: str
    run_id: UUID
    input_hash: str
    engine_version: str


class ValidationFindingRead(ClosedModel):
    rule: str
    severity: ValidationSeverity
    detail: str


class ValidationReportRead(ClosedModel):
    rule_version: str
    validated_at: datetime
    passed: bool
    error_count: int
    warning_count: int
    info_count: int
    findings: list[ValidationFindingRead]


class PackageApprovalRead(ClosedModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    action: ApprovalAction
    actor_user_id: UUID
    reason: str | None
    occurred_at: datetime


class RegulatoryPackageSummaryRead(ClosedModel):
    id: UUID
    bank_id: str
    return_family: ReturnFamily
    return_code: str
    reporting_date: date
    frequency: ReturnFrequency
    basis: ReturnBasis
    status: PackageStatus
    version: int
    supersedes_id: UUID | None
    generated_by: UUID
    generated_at: datetime
    validation_passed: bool | None
    notes: str | None
    # ORASS parity: revision stamped at submit (1.0/1.1), snapshot seal, and
    # supervisor comments from a reject/decline decision.
    submission_revision: str | None
    snapshot_sha256: str | None
    regulator_comments: str | None
    created_at: datetime
    updated_at: datetime


class RegulatoryPackageRead(RegulatoryPackageSummaryRead):
    snapshot: dict[str, Any]
    source_runs: list[PackageSourceRunRead]
    validation_report: ValidationReportRead | None
    approvals: list[PackageApprovalRead]


class RegulatoryPackageListRead(ClosedModel):
    bank_id: str
    packages: list[RegulatoryPackageSummaryRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class PackageApprovalRequestCreate(ClosedModel):
    reason: str | None = Field(default=None, max_length=2000)


class PackageApprovalDecisionCreate(ClosedModel):
    action: ApprovalDecision
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_rejection_reason(self) -> PackageApprovalDecisionCreate:
        if self.action == "rejected" and not (self.reason or "").strip():
            msg = "A reason is required when rejecting a package."
            raise ValueError(msg)
        return self


class SubmissionEventRead(ClosedModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    package_id: UUID
    channel: ChannelCode
    event: SubmissionEventType
    external_ref: str | None
    detail: dict[str, Any]
    occurred_at: datetime


class SubmissionEventListRead(ClosedModel):
    package_id: UUID
    events: list[SubmissionEventRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class ReportingObligationRead(ClosedModel):
    return_code: str
    return_family: ReturnFamily
    title: str
    frequency: ReturnFrequency
    fidelity: FidelityGrade
    default_channel: ChannelCode
    reporting_date: date
    due_date: date
    # Cut-off time-of-day on the due date (e.g. daily DBK "10:00"); None for
    # returns whose deadline is a calendar day only.
    due_time: str | None = None
    # Obligations are enumerated on the solo basis only for now (the calendar is
    # not doubled per basis); generated packages still carry solo/consolidated
    # basis independently.
    basis: ReturnBasis = "solo"
    package_id: UUID | None
    package_status: PackageStatus | None
    package_version: int | None
    rag: ObligationRag


class ReportingObligationListRead(ClosedModel):
    bank_id: str
    as_of: date
    horizon_months: int
    obligations: list[ReportingObligationRead]


class ReturnTemplateRead(ClosedModel):
    code: str
    family: ReturnFamily
    title: str
    regulator: str
    directive_citation: str
    frequency: ReturnFrequency
    generator: str
    template_id: str
    fidelity: FidelityGrade
    default_channel: ChannelCode


class ReturnTemplateListRead(ClosedModel):
    templates: list[ReturnTemplateRead]


class ChannelConfigPut(ClosedModel):
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] | None = None


class ChannelConfigRead(ClosedModel):
    channel: ChannelCode
    config: dict[str, Any]
    has_credentials: bool
    credential_fingerprint: str | None
    created_at: datetime
    updated_at: datetime


class ReportingSettingsPut(ClosedModel):
    """Per-bank deadline overrides: ``{return_code: day_of_month}``.

    Corrects the registry's placeholder monthly deadlines (e.g. BSD2 day-14,
    FX-NOP day-10) at onboarding once ORASS confirms the real day. Day values
    are 1..31 and are clamped to the target month's length by ``monthly_day``.
    """

    deadline_overrides: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_days(self) -> ReportingSettingsPut:
        for return_code, day in self.deadline_overrides.items():
            if not 1 <= day <= 31:
                msg = (
                    f"Deadline override for '{return_code}' must be a day of month "
                    f"between 1 and 31, got {day}."
                )
                raise ValueError(msg)
        return self


class ReportingSettingsRead(ClosedModel):
    bank_id: str
    deadline_overrides: dict[str, int]
    created_at: datetime
    updated_at: datetime


class PackageSubmitCreate(ClosedModel):
    """Channel selection for submitRegulatoryPackage; omitted -> registry default."""

    channel: ChannelCode | None = None


class RegulatoryArtifactRead(ClosedModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    package_id: UUID
    kind: ArtifactKind
    object_path: str
    checksum_sha256: str
    size_bytes: int
    created_at: datetime


class SubmissionPollRead(ClosedModel):
    """One poll cycle: the regulator-side status, the recorded poll event,
    and the package after any resulting transition."""

    poll_status: Literal["pending", "acknowledged", "rejected", "declined"]
    event: SubmissionEventRead
    package: RegulatoryPackageRead


class ResubmissionRequestCreate(ClosedModel):
    """ORASS "Request Resubmission": a reason is mandatory (LRT guide §5.3)."""

    reason: str = Field(min_length=1, max_length=2000)


class ResubmissionDecisionCreate(ClosedModel):
    """Manual grant/deny for email/manual-channel submissions."""

    decision: Literal["granted", "denied"]
    note: str | None = Field(default=None, max_length=2000)


class ResubmissionRequestRead(ClosedModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    package_id: UUID
    reason: str
    status: ResubmissionStatus
    requested_by: UUID
    decided_at: datetime | None
    detail: dict[str, Any]
    consumed_by_package_id: UUID | None
    occurred_at: datetime


class ResubmissionRequestListRead(ClosedModel):
    package_id: UUID
    requests: list[ResubmissionRequestRead]


class RegulatoryArtifactListRead(ClosedModel):
    package_id: UUID
    artifacts: list[RegulatoryArtifactRead]


class EmailRecipientGuidanceRead(ClosedModel):
    confirmed_consultation_address: str
    confirmed_consultation_note: str
    downtime_return_address: str | None
    downtime_return_note: str


class EmailFallbackAttachmentRead(ClosedModel):
    kind: ArtifactKind
    filename: str
    object_path: str
    size_bytes: int
    checksum_sha256: str


class EmailFallbackInstructionsRead(ClosedModel):
    """Send-ready email fallback bundle (BG/FMD/2026/07 downtime workflow)."""

    package_id: UUID
    subject: str
    recipient_guidance: EmailRecipientGuidanceRead
    attachments: list[EmailFallbackAttachmentRead]
    penalty_reminder: str
    pending_orass_reupload: bool
    instructions: str
