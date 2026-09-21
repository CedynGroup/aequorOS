from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The signing vocabulary is owned by the attestation contract; reusing the alias
# keeps one enum on the wire rather than two that could drift apart.
from app.schemas.attestation import SigningRole

type ReturnFamily = Literal[
    "liquidity",
    "capital",
    "irrbb",
    "fx",
    "icaap_stress",
    "corporate",
    "large_exposures",
    "dbk",
    "stress",
    "bsd",
    "sdi",
    "credit",
    # The ICAAP filing family: the annual report, its paragraph 74 updates and
    # the paragraph 82 disclosure. "icaap_stress" stays what it is - the
    # Appendix II stress annex filed inside the report.
    "icaap",
]
type ReturnFrequency = Literal["weekly", "monthly", "quarterly", "semiannual", "annual", "daily"]
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
type ArtifactKind = Literal["xlsx", "csv", "pdf", "xlsx_working", "docx_working"]
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
#: Whether the bank has a computed position AS OF a reporting date. The
#: reporting date itself is the REGULATOR's (see ``regulatory_reporting.anchors``)
#: and exists whether or not data has been ingested for it, so this is reported
#: alongside every anchor rather than the anchor being hidden.
type AnchorDataStatus = Literal["computed", "awaiting_data"]


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
    #: The version of the return FAMILY's own ruleset, present only when a
    #: family hook contributed findings. Separate from ``rule_version`` so a
    #: change to one family's rules does not re-date every other return's
    #: report.
    family_rule_version: str | None = None


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
    # Carried on the summary so History/Approvals can render an attestation
    # column without an extra request per row.
    attestation_state: str = "unsigned"
    # ORASS parity: revision stamped at submit (1.0/1.1), snapshot seal, and
    # supervisor comments from a reject/decline decision.
    submission_revision: str | None
    snapshot_sha256: str | None
    regulator_comments: str | None
    #: A rehearsal package runs the full lifecycle but can never be filed
    #: (D-068). Every surface that can show a package must be able to say so —
    #: without this on the read model, the generic reporting workspace could not
    #: tell a rehearsal from a filing, which is the one confusion the whole
    #: rehearsal design exists to prevent.
    is_rehearsal: bool = False
    #: WHICH officer the return is waiting for. ``status`` cannot answer this:
    #: an Approver stage and a Validator stage both read ``pending_approval``,
    #: so a queue that labelled rows from the status alone called a return
    #: "Pending approval" while it sat with the Validator. ``None`` before a
    #: chain is pinned, and the title is the bank's own word for the stage —
    #: chains are per-bank data, so it is never inferred from the sequence.
    current_stage_seq: int | None = None
    current_stage_title: str | None = None
    #: Which round of review this return is in. Round 2 means it has been sent
    #: back at least once — the difference between a return nobody has looked
    #: at and one an officer returned for correction, which the lifecycle
    #: status cannot express because both read "generated".
    workflow_round: int = 1
    created_at: datetime
    updated_at: datetime


class DeclaredMethodologyRead(ClosedModel):
    """One "which figure does this return mean?" disclosure, typed.

    CF-1's answer to the audit's LCR question: BSD3's LCR and LMT Table 11's LCR
    are different methodologies by design, and forcing them to agree would break
    a correct engine. The registry has recorded that since ARCH-4 and the
    generator has written it into ``snapshot["provenance"]`` — where, until
    2026-08-22, nothing read it (audit D-20). ``snapshot`` is typed
    ``dict[str, Any]``, so an untyped blob is not a contract; this is.
    """

    metric_id: str
    methodology_id: str
    #: 'registered' when the authority registry knows this pair, else
    #: 'not_registered' — the registry's own sentinel, never a guess.
    registry_status: str
    authority_reference: str | None = None
    regime: str | None = None
    calculation_engine: str | None = None
    calculation_version: str | None = None
    advisory_designation: str | None = None
    expected_tolerance: str | None = None
    #: Other registered methodologies for the same metric. A non-empty list is
    #: the reader's cue that "the LCR" is ambiguous across surfaces.
    alternate_methodologies: list[str] = Field(default_factory=list)
    #: Present only when this methodology declares a documented divergence from
    #: an alternate: what differs, in which direction, and the rule relating the
    #: two (usually: they are NOT reconciled by equality).
    divergence: dict[str, Any] | None = None


class RegulatoryPackageRead(RegulatoryPackageSummaryRead):
    snapshot: dict[str, Any]
    source_runs: list[PackageSourceRunRead]
    validation_report: ValidationReportRead | None
    approvals: list[PackageApprovalRead]
    # Promoted out of the untyped snapshot so the disclosure has a reader
    # (audit 2026-08-22 D-20). Empty for a return that declares no methodology.
    declared_methodologies: list[DeclaredMethodologyRead] = Field(default_factory=list)


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


class ObligationAnnexRead(ClosedModel):
    """A return that is filed INSIDE another one's submission (D-011).

    Shown under its parent obligation rather than as a row of its own, because
    it is not a separate thing the bank owes the regulator.
    """

    return_code: str
    title: str
    filing_role: Literal["annex", "companion"]
    package_id: UUID | None = None
    package_status: PackageStatus | None = None
    package_version: int | None = None


class ReportingObligationRead(ClosedModel):
    return_code: str
    return_family: ReturnFamily
    title: str
    frequency: ReturnFrequency
    fidelity: FidelityGrade
    default_channel: ChannelCode
    reporting_date: date
    #: An OBLIGATION always has a due date. When a return's deadline is a
    #: governed value nobody has configured, the obligation is omitted from the
    #: calendar and the parameter is named in ``coverage_note`` — a deadline is
    #: never substituted or assumed (D-024), and a row with no due date would
    #: be an obligation with no deadline, which is not a thing.
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
    # Whether figures exist as of ``reporting_date``. ``awaiting_data`` is a
    # normal state for a future anchor and an actionable one for a past anchor;
    # either way the obligation is real and the deadline still runs.
    data_status: AnchorDataStatus = "awaiting_data"
    rag: ObligationRag
    #: Returns carried inside this one's submission. Empty for every return
    #: that has none, which is every return but the ICAAP report.
    annexes: list[ObligationAnnexRead] = Field(default_factory=list)


class ReportingObligationSummaryRead(ClosedModel):
    """Whole-horizon counts, independent of the requested result page."""

    overdue: int = Field(ge=0)
    due_soon: int = Field(ge=0)
    on_track: int = Field(ge=0)
    pending_reupload: int = Field(ge=0)


class ReportingObligationListRead(ClosedModel):
    bank_id: str
    as_of: date
    horizon_months: int
    # How far back the elapsed half of the window reaches. Echoed so a screen
    # can say what span it is showing rather than implying the list is complete.
    lookback_months: int
    obligations: list[ReportingObligationRead]
    summary: ReportingObligationSummaryRead
    total: int = Field(ge=0)
    limit: int = Field(ge=0)
    offset: int = Field(ge=0)
    has_more: bool
    # Why the list is empty when it is (audit 2026-08-22 D-20).
    # ``InstitutionEligibility.coverage_note()`` had produced this sentence since
    # ARCH-8 and had nowhere to go, so an SDI tenant received ``obligations: []``
    # with no explanation — the exact silence the eligibility authority was built
    # to end. Always ``None`` when the institution has obligations.
    coverage_note: str | None = None


class ReturnAnchorRead(ClosedModel):
    """One reporting date a return reports on, with what exists for it.

    ``reporting_date`` comes from the return definition — BoG's cadence — not
    from the bank's ingestion history, so this list is identical for two banks
    filing the same return and is never empty for an eligible return.
    """

    reporting_date: date
    #: Absent when the return's deadline is a governed value that has not been
    #: configured. A deadline is NEVER substituted or assumed (D-024).
    due_date: date | None = None
    due_time: str | None = None
    data_status: AnchorDataStatus
    # The most recent computed position BEFORE this anchor, when this anchor has
    # none. Reported so a screen can say what the bank does have; it is never
    # used as a substitute for the missing snapshot.
    nearest_computed_before: date | None = None
    package_id: UUID | None = None
    package_status: PackageStatus | None = None
    package_version: int | None = None
    rag: ObligationRag
    #: Whether a filing OBLIGATION exists on this date. False for an anchor
    #: that precedes the return's first in-force date: the date is offered so a
    #: bank can prepare and dry-run before its first live filing (which is the
    #: registry's own stated rule), but nothing is owed and no deadline runs, so
    #: ``rag`` is not a compliance signal for it.
    in_force: bool = True


class ReturnAnchorListRead(ClosedModel):
    bank_id: str
    return_code: str
    frequency: ReturnFrequency
    as_of: date
    horizon_months: int
    # The trailing half of the same window — how far back the elapsed reporting
    # dates reach. An overdue return is the one the bank still owes, so the
    # picker offers it; see ``regulatory_reporting.anchors``.
    lookback_months: int
    anchors: list[ReturnAnchorRead]
    # Set when the institution may not file this return at all (class,
    # jurisdiction, regulator, or a not-yet-commenced instrument), in the words
    # the eligibility authority uses everywhere else. ``anchors`` is empty then.
    ineligible_reason: str | None = None
    #: The date this return comes into force, where one is established — the
    #: registry's own literal or the governed parameter that carries it. Anchors
    #: earlier than this are listed with ``in_force=False`` rather than omitted.
    effective_from: date | None = None
    #: Set when the return's due dates come from a governed parameter that is
    #: not configured. The anchors are still listed (they are the regulator's
    #: dates); their ``due_date`` is absent rather than invented.
    deadline_note: str | None = None


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
    supports_working_copy: bool


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
    #: The regulator-side reference for a MANUAL submission - the portal
    #: receipt number, or the URL a paragraph 82 disclosure was published at.
    #: The platform cannot mint it, so a return whose registry entry declares
    #: ``requires_external_ref`` refuses submission without it.
    external_ref: str | None = Field(default=None, min_length=1, max_length=500)


type AttachmentGate = Literal["freeze", "submission", "optional"]
type AttachmentSource = Literal["package_upload", "icaap_cycle"]


class PackageAttachmentRead(ClosedModel):
    """One document filed WITH a return."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    package_id: UUID
    package_version: int
    kind: str
    title: str
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str
    source: AttachmentSource
    gate: AttachmentGate
    attributes: dict[str, Any] = Field(default_factory=dict)
    attached_by: UUID
    created_at: datetime
    withdrawn: bool = False
    withdrawn_at: datetime | None = None
    withdrawal_reason: str | None = None


class PackageAttachmentRequirementRead(ClosedModel):
    """One document this return needs, and whether it has it yet."""

    kind: str
    title: str
    gate: AttachmentGate
    #: Where the requirement comes from: the return family's own framework, or
    #: the institution's signing policy. A family requirement is NOT relaxable
    #: by dropping a signature slot.
    origin: Literal["family", "signing_policy"]
    required_count: int
    active_count: int
    satisfied: bool


class PackageAttachmentListRead(ClosedModel):
    package_id: UUID
    attachments: list[PackageAttachmentRead]
    requirements: list[PackageAttachmentRequirementRead]


class PackageAttachmentWithdraw(ClosedModel):
    reason: str = Field(min_length=1, max_length=2000)


class RegulatoryArtifactRead(ClosedModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    package_id: UUID
    kind: ArtifactKind
    object_path: str
    checksum_sha256: str
    size_bytes: int
    created_at: datetime


class FilingSetEntryRead(ClosedModel):
    """One file the submission would send, as the submission itself resolves it."""

    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    filename: str
    role: Literal["signed_record", "official_copy", "formula_copy", "data"]
    #: ``None`` when the file does not exist yet and is exported at submission.
    size_bytes: int | None
    generated_at_submission: bool
    #: Signatures carried by this file; ``None`` where signing does not apply.
    signature_count: int | None


class FilingSetPreviewRead(ClosedModel):
    """What a submission WOULD send, and the gates that already hold.

    Read-only: the route mints nothing and transitions nothing. It exists so the
    transmit confirmation can state the filing set the SUBMISSION resolves —
    which is not the package's artifact rows, because the signed revision
    replaces the unsigned export and a missing filing format is minted on the
    way out.
    """

    model_config = ConfigDict(extra="forbid")

    package_id: UUID
    filing_set: list[FilingSetEntryRead]
    satisfied: list[str]
    institution_code: str | None
    content_digest: str | None
    #: The revision this submission WOULD stamp ("1.0", "1.1", ...). Computed
    #: the same way the submission computes it; the stored
    #: ``submission_revision`` is null until a package has actually been filed,
    #: so a confirmation cannot read it from there.
    submission_revision: str
    is_first_filing: bool
    #: Why the set is not larger, when the reason is a RULE rather than work
    #: not yet done. A one-file filing can be entirely correct; an officer
    #: cannot tell that by counting files.
    omissions: list[str]


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


class ArtifactVersionSignatureRead(ClosedModel):
    """Who pinned an archived revision, from the signature record itself.

    Enough to label a download honestly — the officer, the capacity, and the
    moment — without the caller having to join the attestation status back onto
    the version list.
    """

    signature_id: UUID
    signing_role: SigningRole
    signer_id: str
    signer_display_name: str | None
    officer_title: str | None
    signed_at: datetime
    attestation_cycle: int


class RegulatoryArtifactVersionRead(ClosedModel):
    """One immutable archived render or signed revision of a package artifact.

    Distinct from :class:`RegulatoryArtifactRead`, which is the UPSERTED row per
    kind and therefore always the unsigned base export. These rows are appended,
    so the chain from that base through each officer's signature is readable.
    """

    id: UUID
    package_id: UUID
    kind: ArtifactKind
    object_path: str
    storage_version_id: str | None
    checksum_sha256: str
    size_bytes: int
    created_at: datetime
    #: None on the base export; the signature that covered these exact bytes
    #: on every signed revision.
    signed_by: ArtifactVersionSignatureRead | None
    #: The newest archived version of this kind, signed or not.
    is_latest: bool
    #: THE document: what download resolves to and what submission files. True
    #: on at most one version — the last signature of the current attestation
    #: cycle — and on none at all while the return is unsigned.
    is_filed: bool


class RegulatoryArtifactVersionListRead(ClosedModel):
    package_id: UUID
    versions: list[RegulatoryArtifactVersionRead]


class PackageVersionSignatureRead(ClosedModel):
    """One signature on a version of the chain, live or withdrawn.

    Distinct from :class:`ArtifactVersionSignatureRead`, which only ever names
    the CURRENT cycle because it labels the document that gets filed. This one
    spans every cycle: a void preserves its signatures rather than deleting
    them, so a superseded version can hold signatures that no longer certify
    anything. ``withdrawn`` is the difference, and it is carried here rather
    than left to the caller to derive from two cycle numbers.
    """

    signature_id: UUID
    signing_role: SigningRole
    signer_id: str
    signer_display_name: str | None
    officer_title: str | None
    signed_at: datetime
    attestation_cycle: int
    #: Signed under a cycle the package has since voided — evidence, never a
    #: current certification.
    withdrawn: bool


class PackageVersionChainEntryRead(ClosedModel):
    """One version of a supersession chain and everything it can still offer."""

    package_id: UUID
    version: int
    status: PackageStatus
    #: The one version that is not superseded, if any.
    is_current: bool
    attestation_state: str
    attestation_cycle: int
    voided_at: datetime | None
    void_reason: str | None
    reporting_date: date
    basis: ReturnBasis
    generated_at: datetime
    generated_by: UUID
    validation_passed: bool | None
    submission_revision: str | None
    snapshot_sha256: str | None
    signatures: list[PackageVersionSignatureRead]
    #: The upserted canonical exports (one per kind), and the append-only chain
    #: including every signed revision — the same two surfaces the current
    #: version's artifacts card resolves.
    artifacts: list[RegulatoryArtifactRead]
    artifact_versions: list[RegulatoryArtifactVersionRead]
    #: False when this version was never exported. The figures comparison stays
    #: available regardless — the snapshot is immutable and always present — so
    #: this gates only the download affordances.
    has_retrievable_files: bool


class PackageVersionChainRead(ClosedModel):
    bank_id: str
    return_code: str
    reporting_date: date
    basis: ReturnBasis
    current_package_id: UUID | None
    #: Newest version first.
    versions: list[PackageVersionChainEntryRead]


type SnapshotLineChange = Literal["added", "removed", "changed"]
type SnapshotSectionChange = Literal["added", "removed", "changed"]
type SnapshotSectionOrigin = Literal["section", "totals"]


class SnapshotLineDiffRead(ClosedModel):
    """One line item that differs between two snapshots."""

    code: str
    description: str
    change: SnapshotLineChange
    base_value: str | None
    target_value: str | None
    #: target − base, present only when both sides parse as decimals.
    delta: str | None
    #: Movement as a percentage of the base, present only when base is non-zero.
    delta_pct: str | None
    #: The section's cross-footed total rather than one of its rows.
    is_total: bool


class SnapshotSectionDiffRead(ClosedModel):
    code: str
    title: str
    origin: SnapshotSectionOrigin
    change: SnapshotSectionChange
    #: Only the lines that differ; identical figures are counted, not listed.
    lines: list[SnapshotLineDiffRead]
    unchanged_line_count: int


class PackageComparisonSideRead(ClosedModel):
    package_id: UUID
    version: int
    status: PackageStatus
    reporting_date: date
    basis: ReturnBasis
    generated_at: datetime
    snapshot_sha256: str | None
    content_digest: str | None


class PackageComparisonRead(ClosedModel):
    """A line-item figures diff between two versions of the same return."""

    base: PackageComparisonSideRead
    target: PackageComparisonSideRead
    identical: bool
    changed_count: int
    added_count: int
    removed_count: int
    #: Sections that differ, in the target's order, with base-only sections
    #: appended. Sections whose every figure matches are omitted.
    sections: list[SnapshotSectionDiffRead]
    unchanged_section_count: int


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
