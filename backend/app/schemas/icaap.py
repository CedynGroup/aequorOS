"""ICAAP workspace contracts (P1): frameworks, cycles, sections, blocks, evidence."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type IcaapCycleKind = Literal["annual", "material_change", "regulator_request", "rehearsal"]
type IcaapBasis = Literal["solo", "consolidated"]
type IcaapDueDateBasis = Literal["framework", "bank_set", "regulator_set", "timely"]
type IcaapRequirementStatus = Literal["open", "met", "not_applicable"]
type IcaapBlockStatus = Literal[
    "unbound",
    "fresh",
    "stale",
    "as_of_mismatch",
    "source_withdrawn",
    "source_missing",
    "pinned",
]
type IcaapSeverity = Literal["blocking", "warning", "info"]
type IcaapScope = Literal["cycle", "section", "requirement", "block", "attachment"]
type IcaapRag = Literal["green", "amber", "red", "none"]
type IcaapDraftContent = Literal["working", "committed"]


# --- framework -------------------------------------------------------------


class IcaapCitationRead(ClosedModel):
    cite_id: str
    doc: str
    ref: str
    label: str


class IcaapRequirementItemRead(ClosedModel):
    id: str
    text: str
    citations: list[IcaapCitationRead]
    evidence: list[str]
    waivable: bool
    applies_when: str | None = None
    param_refs: list[str] = Field(default_factory=list)


class IcaapSectionDefRead(ClosedModel):
    key: str
    letter: str
    order: int
    title: str
    citation: IcaapCitationRead
    source_status: str
    guidance: str
    data_blocks: list[str]
    ai_draftable: bool
    public_disclosure: bool
    requirements: list[IcaapRequirementItemRead]


class IcaapPillar2ComponentRead(ClosedModel):
    key: str
    table5_row: str | None = None
    p29_class: str
    p29_basis: str
    allowed_methods: list[str]


class IcaapRiskCategoryRead(ClosedModel):
    key: str
    number: int
    title: str
    title_status: str
    citation: IcaapCitationRead
    pillar1_coverage: str
    custom: bool
    components: list[IcaapPillar2ComponentRead]
    sub_requirements: list[IcaapRequirementItemRead]


class IcaapAttachmentRequirementRead(ClosedModel):
    kind: str
    title: str
    gate: str
    min_count: int
    max_count: int | None = None
    media_types: list[str]
    applies_when: str | None = None
    section_keys: list[str]
    citations: list[IcaapCitationRead]


class IcaapStageTemplateRead(ClosedModel):
    seq: int
    key: str
    title: str
    decision: str
    freeze_on_approve: bool


class IcaapDeadlineRead(ClosedModel):
    as_of: str
    fy_end_month_day: str
    months_after_fy_end: int | None = None
    #: Which governed parameter supplied the months (D-024).
    months_param_code: str
    months_confirmation_status: str | None = None


class IcaapFrameworkSummaryRead(ClosedModel):
    code: str
    version: str
    title: str
    short_title: str
    regulator: str
    status: str
    digest: str
    effective_from: date
    first_as_of_date: date
    filing_return_code: str | None = None


class IcaapFrameworkListRead(ClosedModel):
    frameworks: list[IcaapFrameworkSummaryRead]


class IcaapFrameworkRead(IcaapFrameworkSummaryRead):
    jurisdiction: str
    first_as_of_basis: str
    first_as_of_note: str | None = None
    institution_classes: list[str]
    deadline: IcaapDeadlineRead
    sections: list[IcaapSectionDefRead]
    risk_categories: list[IcaapRiskCategoryRead]
    attachments: list[IcaapAttachmentRequirementRead]
    stages: list[IcaapStageTemplateRead]
    documents: list[dict[str, Any]]
    notes: list[dict[str, Any]]


# --- block catalogue -------------------------------------------------------


class IcaapFactSpecRead(ClosedModel):
    key: str
    label: str
    kind: str


class IcaapBlockTypeRead(ClosedModel):
    type: str
    title: str
    phase: str
    available: bool
    manual: bool
    dynamic_facts: bool
    facts: list[IcaapFactSpecRead]


class IcaapBlockTypeListRead(ClosedModel):
    block_types: list[IcaapBlockTypeRead]


# --- cycles ----------------------------------------------------------------


class IcaapCycleCreate(ClosedModel):
    fiscal_year: int = Field(ge=1990, le=2200)
    cycle_kind: IcaapCycleKind
    basis: IcaapBasis
    framework_code: str = Field(min_length=1, max_length=60)
    framework_version: str = Field(min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=200)
    subsidiaries_declared: bool = False
    #: Ignored for annual and rehearsal cycles: their as-of date is the
    #: framework's own year end, never a client's choice.
    as_of_date: date | None = None
    change_trigger: str | None = Field(default=None, max_length=40)
    change_description: str | None = Field(default=None, max_length=4000)
    regulator_request_ref: str | None = Field(default=None, max_length=120)
    requested_due_date: date | None = None
    reason: str = Field(min_length=1, max_length=2000)


class IcaapCycleUpdate(ClosedModel):
    title: str | None = Field(default=None, max_length=200)
    subsidiaries_declared: bool | None = None
    due_date: date | None = None
    reason: str = Field(min_length=1, max_length=2000)


class IcaapCycleArchive(ClosedModel):
    reason: str = Field(min_length=1, max_length=2000)


class IcaapCycleRebase(ClosedModel):
    framework_code: str = Field(min_length=1, max_length=60)
    framework_version: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=2000)


class IcaapSectionSummaryRead(ClosedModel):
    key: str
    letter: str
    order: int
    title: str
    source_status: str
    committed_version_no: int | None = None
    has_uncommitted_changes: bool
    requirement_counts: dict[str, int]
    working_updated_at: datetime | None = None


class IcaapCycleSummaryRead(ClosedModel):
    id: UUID
    bank_id: str
    fiscal_year: int
    as_of_date: date
    cycle_kind: IcaapCycleKind
    basis: IcaapBasis
    title: str
    status: str
    round: int
    due_date: date | None = None
    due_date_basis: IcaapDueDateBasis
    framework: IcaapFrameworkSummaryRead
    framework_digest_matches: bool
    editable: bool
    created_at: datetime
    updated_at: datetime


class IcaapCycleRead(IcaapCycleSummaryRead):
    subsidiaries_declared: bool
    current_stage_seq: int | None = None
    change_trigger: str | None = None
    change_description: str | None = None
    regulator_request_ref: str | None = None
    package_id: UUID | None = None
    supersedes_cycle_id: UUID | None = None
    rebased_from_cycle_id: UUID | None = None
    created_by: UUID
    sections: list[IcaapSectionSummaryRead]


class IcaapCycleListRead(ClosedModel):
    cycles: list[IcaapCycleSummaryRead]


# --- sections --------------------------------------------------------------


class IcaapRequirementRead(ClosedModel):
    item: IcaapRequirementItemRead
    status: IcaapRequirementStatus
    reason: str | None = None
    #: False when the item's condition does not hold for this cycle, in which
    #: case it counts as satisfied without anyone marking it.
    applicable: bool
    updated_by: UUID | None = None
    updated_at: datetime | None = None
    #: The requirement text with every ``{param:<code>}`` resolved (D-024 A4).
    resolved_text: str


class IcaapSectionListRead(ClosedModel):
    sections: list[IcaapSectionSummaryRead]


class IcaapSectionRead(IcaapSectionSummaryRead):
    cycle_id: UUID
    citation: IcaapCitationRead
    guidance: str
    working_doc: dict[str, Any]
    working_rev: int
    working_updated_by: UUID | None = None
    requirements: list[IcaapRequirementRead]
    expected_block_types: list[str]
    editable: bool
    carried_from: dict[str, Any] | None = None


class IcaapSectionWorkingSave(ClosedModel):
    doc: dict[str, Any]
    base_rev: int = Field(ge=0)


class IcaapSectionCommit(ClosedModel):
    base_rev: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=2000)


class IcaapRequirementStateUpdate(ClosedModel):
    status: IcaapRequirementStatus
    reason: str | None = Field(default=None, max_length=2000)


class IcaapSectionVersionSummaryRead(ClosedModel):
    id: UUID
    section_key: str
    version_no: int
    round: int
    doc_sha256: str
    commit_note: str | None = None
    committed_by: UUID
    created_at: datetime


class IcaapSectionVersionRead(IcaapSectionVersionSummaryRead):
    doc: dict[str, Any]
    plain_text: str
    fact_refs: list[dict[str, Any]]
    block_refs: list[str]
    editor_schema_version: str
    #: How much of this committed text was drafted with AI assistance, and by
    #: which model. ``None`` when the section carries no AI marker at all, so a
    #: wholly human-written section records nothing rather than a zero.
    ai_provenance: dict[str, Any] | None = None


class IcaapSectionVersionListRead(ClosedModel):
    versions: list[IcaapSectionVersionSummaryRead]


# --- data blocks -----------------------------------------------------------


class IcaapFactValueRead(ClosedModel):
    key: str
    label: str
    kind: str
    #: Always a string or null — never a float, and null is "Not available",
    #: never 0.
    value: str | None = None
    unit: str | None = None
    currency: str | None = None


class IcaapBlockBindingRead(ClosedModel):
    id: UUID
    seq: int
    resolver: str
    resolver_version: str
    source_kind: str
    source_ref: dict[str, Any]
    source_key: str
    source_as_of: date | None = None
    payload: dict[str, Any] | None = None
    facts: dict[str, IcaapFactValueRead]
    payload_sha256: str
    evidence_attachment_id: UUID | None = None
    reason: str | None = None
    bound_by: UUID
    created_at: datetime


class IcaapDataBlockRead(ClosedModel):
    id: UUID
    cycle_id: UUID
    block_type: str
    block_key: str
    title: str | None = None
    params: dict[str, Any]
    status: IcaapBlockStatus
    status_detail: str | None = None
    pin_reason: str | None = None
    pinned_at: datetime | None = None
    retired_at: datetime | None = None
    current_binding: IcaapBlockBindingRead | None = None
    spec: IcaapBlockTypeRead


class IcaapDataBlockListRead(ClosedModel):
    blocks: list[IcaapDataBlockRead]


class IcaapDataBlockCreate(ClosedModel):
    block_type: str = Field(min_length=1, max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)
    title: str | None = Field(default=None, max_length=200)


class IcaapDataBlockRefresh(ClosedModel):
    reason: str | None = Field(default=None, max_length=2000)


class IcaapDataBlockPin(ClosedModel):
    reason: str = Field(min_length=10, max_length=2000)


class IcaapDataBlockRetire(ClosedModel):
    reason: str = Field(min_length=1, max_length=2000)


class IcaapManualColumn(ClosedModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]{0,59}$")
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["text", "amount", "ratio_pct", "count"]


class IcaapManualRow(ClosedModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]{0,59}$")
    label: str = Field(min_length=1, max_length=200)
    fact_key: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    #: A blank cell stays blank. It is never read as zero.
    cells: dict[str, str | None] = Field(default_factory=dict)


class IcaapManualTablePut(ClosedModel):
    columns: list[IcaapManualColumn] = Field(min_length=1, max_length=20)
    rows: list[IcaapManualRow] = Field(max_length=200)
    evidence_attachment_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)


class IcaapFactChangeRead(ClosedModel):
    key: str
    before: str | None = None
    after: str | None = None


class IcaapBlockRefreshRead(ClosedModel):
    outcome: Literal["bound", "unchanged", "unavailable"]
    reason: str | None = None
    block: IcaapDataBlockRead
    changed_facts: list[IcaapFactChangeRead]


class IcaapBlockBindingListRead(ClosedModel):
    bindings: list[IcaapBlockBindingRead]


# --- attachments -----------------------------------------------------------


class IcaapAttachmentRead(ClosedModel):
    id: UUID
    cycle_id: UUID
    kind: str
    title: str
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str
    section_key: str | None = None
    uploaded_by: UUID
    created_at: datetime
    withdrawn: bool
    withdrawn_at: datetime | None = None
    withdrawal_reason: str | None = None


class IcaapAttachmentRequirementStatusRead(ClosedModel):
    kind: str
    title: str
    gate: str
    min_count: int
    active_count: int
    applicable: bool
    satisfied: bool
    media_types: list[str]


class IcaapAttachmentListRead(ClosedModel):
    attachments: list[IcaapAttachmentRead]
    requirements: list[IcaapAttachmentRequirementStatusRead]


class IcaapAttachmentWithdraw(ClosedModel):
    reason: str = Field(min_length=1, max_length=2000)


# --- readiness -------------------------------------------------------------


class IcaapReadinessItemRead(ClosedModel):
    code: str
    severity: IcaapSeverity
    scope: IcaapScope
    ref: str | None = None
    message: str


class IcaapDeadlineStatusRead(ClosedModel):
    due_date: date | None = None
    days_remaining: int | None = None
    rag: IcaapRag
    basis: IcaapDueDateBasis


class IcaapSectionReadinessRead(ClosedModel):
    key: str
    blocking: int
    warnings: int


class IcaapReadinessRead(ClosedModel):
    cycle_id: UUID
    ready_for_freeze: bool
    framework_status: str
    pending_primary_text_sections: list[str]
    deadline: IcaapDeadlineStatusRead
    items: list[IcaapReadinessItemRead]
    sections: list[IcaapSectionReadinessRead]
    counts: dict[str, dict[str, int]]


# --- P3: review chain, freeze, filing, ¶74 clone, ¶82 disclosure -----------

type IcaapStageDecisionKind = Literal["prepare", "review", "approve", "attest"]
type IcaapStageState = Literal["pending", "current", "done", "returned"]
type IcaapForwardDecision = Literal["reviewed", "approved", "returned"]
type IcaapWorkflowTemplateStatus = Literal[
    "draft", "pending_approval", "approved", "rejected", "superseded"
]
type IcaapDisclosureStatus = Literal[
    "draft", "pending_approval", "approved", "published", "rejected", "superseded"
]
type IcaapCloneMode = Literal["revision", "update"]


class IcaapStageInput(ClosedModel):
    """One stage of a proposed review chain."""

    seq: int = Field(ge=1, le=20)
    stage_key: str = Field(min_length=2, max_length=60)
    title: str = Field(min_length=1, max_length=200)
    decision_kind: IcaapStageDecisionKind
    officer_titles: list[str] = Field(default_factory=list, max_length=10)
    freeze_on_approve: bool = False


class IcaapWorkflowTemplateCreate(ClosedModel):
    stages: list[IcaapStageInput] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=10, max_length=2000)


class IcaapWorkflowTemplateUpdate(ClosedModel):
    stages: list[IcaapStageInput] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=10, max_length=2000)


class IcaapWorkflowTemplateSubmit(ClosedModel):
    reason: str = Field(min_length=10, max_length=2000)


class IcaapWorkflowTemplateDecision(ClosedModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=10, max_length=2000)


class IcaapWorkflowTemplateRead(ClosedModel):
    id: UUID
    bank_id: str
    version: int
    status: IcaapWorkflowTemplateStatus
    stages: list[IcaapStageInput]
    framework_code: str | None = None
    framework_version: str | None = None
    reason: str
    proposed_by: UUID
    submitted_at: datetime | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    superseded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class IcaapWorkflowTemplateListRead(ClosedModel):
    templates: list[IcaapWorkflowTemplateRead]
    #: The chain a cycle submitted today would pin, whatever its source.
    effective_stages: list[IcaapStageInput]
    effective_source: Literal["framework_default", "bank_template"]


class IcaapStageDecisionRead(ClosedModel):
    id: UUID
    stage_seq: int
    stage_key: str
    round: int
    decision: str
    return_to_seq: int | None = None
    review_digest: str
    package_id: UUID | None = None
    comment: str | None = None
    decided_by: UUID
    decided_by_name: str
    officer_title: str | None = None
    created_at: datetime
    #: False once a later return reopened this stage.
    still_stands: bool


class IcaapStageRead(ClosedModel):
    seq: int
    stage_key: str
    title: str
    decision_kind: IcaapStageDecisionKind
    officer_titles: list[str]
    freeze_on_approve: bool
    state: IcaapStageState
    decisions: list[IcaapStageDecisionRead]


class IcaapStageViewerRead(ClosedModel):
    """What THIS caller may do, and the plain-language reason when they may not."""

    can_submit: bool
    can_decide: bool
    can_return: bool
    can_freeze: bool
    blocked_reason: str | None = None


class IcaapStagesRead(ClosedModel):
    cycle_id: UUID
    status: str
    round: int
    current_stage_seq: int | None = None
    awaiting_freeze: bool
    source: Literal["framework_default", "bank_template"]
    review_digest: str
    stages: list[IcaapStageRead]
    viewer: IcaapStageViewerRead


class IcaapSubmitForReview(ClosedModel):
    review_digest: str = Field(min_length=64, max_length=64)
    note: str | None = Field(default=None, max_length=2000)


class IcaapStageDecisionCreate(ClosedModel):
    decision: IcaapForwardDecision
    round: int = Field(ge=1)
    review_digest: str = Field(min_length=64, max_length=64)
    return_to_seq: int | None = Field(default=None, ge=1, le=20)
    comment: str | None = Field(default=None, max_length=4000)


class IcaapReturnCreate(ClosedModel):
    """Sending a FROZEN ICAAP back — the same basis checks as a stage decision.

    ``round`` and ``review_digest`` were absent until 2026-09-20 (independent
    audit F1), which made this the only ICAAP decision that could be taken from
    a stale page — on the act that voids the Board's signatures. They carry the
    same meaning and produce the same refusals here as on
    :class:`IcaapStageDecisionCreate`: ``stage_round_moved`` and
    ``review_basis_changed``.
    """

    return_to_seq: int = Field(ge=1, le=20)
    round: int = Field(ge=1)
    review_digest: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=10, max_length=4000)


class IcaapFreezeCreate(ClosedModel):
    review_digest: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=10, max_length=2000)


class IcaapPreflightItemRead(ClosedModel):
    code: str
    severity: IcaapSeverity
    scope: IcaapScope
    ref: str | None = None
    message: str


class IcaapPreflightRead(ClosedModel):
    cycle_id: UUID
    ready: bool
    review_digest: str
    items: list[IcaapPreflightItemRead]


class IcaapPackageSummaryRead(ClosedModel):
    id: UUID
    return_code: str
    return_family: str
    reporting_date: date
    basis: str
    status: str
    version: int
    content_digest: str | None = None
    generated_at: datetime


class IcaapFreezeRead(ClosedModel):
    cycle: IcaapCycleSummaryRead
    package: IcaapPackageSummaryRead


class IcaapCloneCreate(ClosedModel):
    mode: IcaapCloneMode
    cycle_kind: IcaapCycleKind | None = None
    as_of_date: date | None = None
    change_trigger: str | None = Field(default=None, max_length=40)
    change_description: str | None = Field(default=None, max_length=4000)
    regulator_request_ref: str | None = Field(default=None, max_length=120)
    requested_due_date: date | None = None
    reason: str = Field(min_length=10, max_length=2000)


class IcaapFilingSlotRead(ClosedModel):
    role: str
    required: bool
    line: str | None = None
    statement: str | None = None
    signed_by: str | None = None
    signed_at: datetime | None = None
    blocked_by: str | None = None


class IcaapFilingAttachmentRead(ClosedModel):
    kind: str
    title: str
    gate: str
    min_count: int
    present: int
    applies: bool
    satisfied: bool


class IcaapFilingRead(ClosedModel):
    cycle_id: UUID
    package: IcaapPackageSummaryRead | None = None
    attestation_state: str
    slots: list[IcaapFilingSlotRead]
    attachments: list[IcaapFilingAttachmentRead]
    submittable: bool
    blockers: list[IcaapPreflightItemRead]


class IcaapDisclosurePut(ClosedModel):
    selected_section_keys: list[str] = Field(default_factory=list, max_length=60)
    reason: str = Field(min_length=10, max_length=2000)


class IcaapDisclosureSubmit(ClosedModel):
    reason: str = Field(min_length=10, max_length=2000)


class IcaapDisclosureDecision(ClosedModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=10, max_length=2000)


class IcaapDisclosureWithheldRead(ClosedModel):
    section_key: str
    block_key: str
    block_type: str
    node_type: str
    fact_key: str | None = None


class IcaapDisclosureSectionRead(ClosedModel):
    key: str
    title: str
    selected: bool
    #: False when the framework marks the section as not for publication.
    selectable: bool


class IcaapDisclosureRead(ClosedModel):
    id: UUID | None = None
    cycle_id: UUID
    status: IcaapDisclosureStatus
    source_package_id: UUID | None = None
    package_id: UUID | None = None
    available: bool
    unavailable_reason: str | None = None
    sections: list[IcaapDisclosureSectionRead]
    withheld: list[IcaapDisclosureWithheldRead]
    proposed_by: UUID | None = None
    proposed_at: datetime | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    published_url: str | None = None
    published_on: date | None = None
