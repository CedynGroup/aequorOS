"""ICAAP risk and capital contracts (P2): register, appetite, Pillar 2, reconciliation.

Two conventions run through every read here.

**``None`` is not zero.** A Pillar 2 figure nobody has quantified is ``null``
and renders "Not modelled"; a zero is a measured zero somebody justified. The
difference is the whole point of Table 5, so no field defaults an amount to 0.

**Every threshold the UI shows comes from the API**, with its provenance
attached (``IcaapParameterUseRead``). D-024 forbids a number in code, and a
dashboard literal would be exactly that with an extra layer between it and the
console.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.icaap import ClosedModel

type IcaapVerdict = Literal["unassessed", "material", "not_material"]
type IcaapVerdictSource = Literal["matrix", "override"]
type IcaapRiskTreatment = Literal[
    "undecided", "quantified", "fully_covered_by_pillar1", "not_capitalised", "not_material"
]
type IcaapMeasureKind = Literal["quantitative", "qualitative"]
type IcaapDirection = Literal["floor", "ceiling"]
type IcaapAppetiteUnit = Literal["percent", "ratio", "amount", "count", "multiplier", "years"]
type IcaapAppetiteValueSource = Literal["block_fact", "manual"]
type IcaapP2Source = Literal[
    "icaap_method", "capital_plan", "stress_overlay", "supervisory", "judgemental"
]
type IcaapP2InputMode = Literal["bound_blocks", "manual_with_evidence"]
type IcaapP2Status = Literal[
    "not_computed", "computed", "interim_non_sf", "incomplete", "not_computable", "not_capitalised"
]
type IcaapBasisKind = Literal[
    "pct_total_rwa", "pct_credit_rwa", "pct_pillar1_credit_capital", "absolute"
]
type IcaapCapitalTier = Literal["cet1", "at1", "tier2", "deduction", "other"]
type IcaapResourceOrigin = Literal["regulatory_component", "manual"]
type IcaapUnitKind = Literal["business_line", "legal_entity", "risk_type"]
type IcaapDriverKind = Literal["rwa_share", "exposure_share", "manual_pct"]
type IcaapReviewKind = Literal[
    "internal_audit", "external_audit", "independent_validation", "other_independent"
]
type IcaapReviewOpinion = Literal[
    "satisfactory", "satisfactory_with_findings", "needs_improvement", "unsatisfactory"
]
type IcaapReviewStatus = Literal["draft", "finalised", "superseded"]
type IcaapChallengeForum = Literal[
    "board",
    "board_risk_committee",
    "board_audit_committee",
    "senior_management",
    "chief_risk_officer",
    "internal_audit",
    "other",
]
type IcaapChallengeOutcome = Literal[
    "accepted_changed", "accepted_no_change", "rejected_with_rationale", "deferred"
]
type IcaapChallengeTarget = Literal[
    "cycle", "section", "risk", "appetite_metric", "pillar2_item", "reconciliation", "stress"
]
type IcaapSeverityLevel = Literal["high", "medium", "low"]
type IcaapAddonStatus = Literal["draft", "active", "superseded", "withdrawn"]
type IcaapConsistencyStatus = Literal["consistent", "inconsistent", "not_comparable", "both_absent"]

_KEY = r"^[a-z][a-z0-9_]{1,79}$"
_COMPARISON_KEY = r"^[a-z0-9_:]{1,120}$"


# --- shared ----------------------------------------------------------------


class IcaapReason(ClosedModel):
    """Why this happened. Every ICAAP mutation records one."""

    reason: str = Field(min_length=1, max_length=2000)


class IcaapRetire(ClosedModel):
    reason: str = Field(min_length=1, max_length=2000)


class IcaapExplanation(ClosedModel):
    explanation: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)


class IcaapParameterUseRead(ClosedModel):
    """A governed figure a result rested on, with everything a reader is owed."""

    param_code: str
    value: str | None = None
    value_json: dict[str, Any] | None = None
    unit: str | None = None
    confirmation_status: str | None = None
    #: True when the citation begins ``REPRESENTATIVE:`` — an invented starting
    #: point, not a published benchmark.
    representative: bool = False
    source_citation: str | None = None
    effective_from: date | None = None
    parameter_id: str | None = None
    resolved: bool = True
    roles: list[str] = Field(default_factory=list)


class IcaapParameterUseListRead(ClosedModel):
    as_of: date
    parameters: list[IcaapParameterUseRead]
    missing: list[str]


# --- risk register ---------------------------------------------------------


class IcaapMaterialityLevelRead(ClosedModel):
    score: int
    key: str
    label: str


class IcaapMaterialityBandRead(ClosedModel):
    key: str
    label: str
    min_score: int
    max_score: int


class IcaapMaterialityCellRead(ClosedModel):
    likelihood: int
    impact: int
    score: int
    rating_key: str | None = None
    material: bool


class IcaapMaterialityMatrixRead(ClosedModel):
    likelihood_levels: list[IcaapMaterialityLevelRead]
    impact_levels: list[IcaapMaterialityLevelRead]
    bands: list[IcaapMaterialityBandRead]
    material_min_score: int
    material_min_impact: int
    cells: list[IcaapMaterialityCellRead]
    thresholds_digest: str
    parameters: list[IcaapParameterUseRead]


class IcaapRiskComponentRead(ClosedModel):
    component_key: str
    table5_row: str | None = None
    p29_class: str
    allowed_methods: list[str]
    item_id: UUID | None = None
    item_key: str | None = None
    method: str | None = None
    method_status: str | None = None
    baseline_amount: Decimal | None = None
    stressed_amount: Decimal | None = None


class IcaapRiskRead(ClosedModel):
    risk_key: str
    category_key: str
    title: str
    is_custom: bool
    description: str | None = None
    likelihood_score: int | None = None
    impact_score: int | None = None
    controls_summary: str | None = None
    materiality_score: int | None = None
    rating_key: str | None = None
    matrix_verdict: IcaapVerdict
    verdict: IcaapVerdict
    verdict_source: IcaapVerdictSource
    override_reason: str | None = None
    materiality_rationale: str | None = None
    pillar1_coverage: str
    p29_class: str
    pillar2_treatment: IcaapRiskTreatment
    pillar1_coverage_rationale: str | None = None
    owner_function: str | None = None
    row_rev: int
    #: False when the governed materiality thresholds have changed since this
    #: row was assessed — the verdict may no longer be the current policy's.
    thresholds_current: bool
    components: list[IcaapRiskComponentRead]
    stored: bool
    retired_at: datetime | None = None
    updated_at: datetime | None = None


class IcaapRiskRegisterSummaryRead(ClosedModel):
    category_count: int
    assessed_risk_count: int
    material_risk_count: int
    unassessed_risk_count: int


class IcaapRiskRegisterRead(ClosedModel):
    cycle_id: UUID
    risks: list[IcaapRiskRead]
    matrix: IcaapMaterialityMatrixRead
    summary: IcaapRiskRegisterSummaryRead


class IcaapRiskPut(ClosedModel):
    """``base_rev`` ``None`` creates the row; an integer requires that revision."""

    base_rev: int | None = Field(default=None, ge=0)
    likelihood_score: int | None = Field(default=None, ge=1)
    impact_score: int | None = Field(default=None, ge=1)
    override: Literal["material", "not_material"] | None = None
    override_reason: str | None = Field(default=None, max_length=4000)
    materiality_rationale: str | None = Field(default=None, max_length=4000)
    controls_summary: str | None = Field(default=None, max_length=4000)
    description: str | None = Field(default=None, max_length=4000)
    pillar2_treatment: IcaapRiskTreatment | None = None
    pillar1_coverage_rationale: str | None = Field(default=None, max_length=4000)
    owner_function: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)


class IcaapCustomRiskCreate(ClosedModel):
    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    owner_function: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)


# --- risk appetite ---------------------------------------------------------


class IcaapAppetiteMetricDefRead(ClosedModel):
    key: str
    label: str
    unit: str
    direction: IcaapDirection
    default_risk_key: str
    source_block_type: str | None = None
    source_fact_key: str | None = None
    regulatory_param_code: str | None = None
    board_register_code: str | None = None


class IcaapRegulatoryReferenceRead(ClosedModel):
    param_code: str
    value: Decimal
    direction: IcaapDirection
    confirmation_status: str
    representative: bool
    source_citation: str | None = None
    effective_from: date | None = None


class IcaapAppetiteEvaluationRead(ClosedModel):
    status: str
    rag: str
    current_value: Decimal | None = None
    utilisation_pct: Decimal | None = None
    headroom_to_appetite: Decimal | None = None
    headroom_to_tolerance: Decimal | None = None
    headroom_to_capacity: Decimal | None = None
    headroom_to_regulatory: Decimal | None = None
    trend: str
    regulatory_reference_absent: bool


class IcaapAppetiteMetricRead(ClosedModel):
    id: UUID
    metric_key: str
    risk_key: str | None = None
    label: str
    is_custom: bool
    measure_kind: IcaapMeasureKind
    unit: IcaapAppetiteUnit | None = None
    direction: IcaapDirection | None = None
    appetite_value: Decimal | None = None
    tolerance_value: Decimal | None = None
    capacity_value: Decimal | None = None
    regulatory_param_code: str | None = None
    board_register_code: str | None = None
    value_source: IcaapAppetiteValueSource | None = None
    source_block_type: str | None = None
    source_fact_key: str | None = None
    manual_value: Decimal | None = None
    manual_evidence_attachment_id: UUID | None = None
    prior_value: Decimal | None = None
    prior_value_label: str | None = None
    qualitative_statement: str
    board_approval_reference: str | None = None
    board_approved_on: date | None = None
    row_rev: int
    evaluation: IcaapAppetiteEvaluationRead | None = None
    regulatory_reference: IcaapRegulatoryReferenceRead | None = None
    reference_missing: bool = False
    board_register_value: Decimal | None = None
    board_register_stricter: bool = False
    violations: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class IcaapAppetiteSummaryRead(ClosedModel):
    metric_count: int
    breach_count: int
    amber_count: int
    qualitative_count: int


class IcaapAppetiteRead(ClosedModel):
    cycle_id: UUID
    catalogue: list[IcaapAppetiteMetricDefRead]
    metrics: list[IcaapAppetiteMetricRead]
    summary: IcaapAppetiteSummaryRead
    parameters: list[IcaapParameterUseRead]


class IcaapAppetiteMetricCreate(ClosedModel):
    metric_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    risk_key: str | None = Field(default=None, pattern=_KEY)
    label: str = Field(min_length=1, max_length=200)
    measure_kind: IcaapMeasureKind
    qualitative_statement: str = Field(min_length=1, max_length=4000)
    unit: IcaapAppetiteUnit | None = None
    direction: IcaapDirection | None = None
    appetite_value: Decimal | None = None
    tolerance_value: Decimal | None = None
    capacity_value: Decimal | None = None
    value_source: IcaapAppetiteValueSource | None = None
    manual_value: Decimal | None = None
    manual_evidence_attachment_id: UUID | None = None
    prior_value: Decimal | None = None
    prior_value_label: str | None = Field(default=None, max_length=80)
    board_approval_reference: str | None = Field(default=None, max_length=200)
    board_approved_on: date | None = None
    reason: str = Field(min_length=1, max_length=2000)


class IcaapAppetiteMetricUpdate(ClosedModel):
    base_rev: int = Field(ge=0)
    label: str | None = Field(default=None, min_length=1, max_length=200)
    qualitative_statement: str | None = Field(default=None, min_length=1, max_length=4000)
    risk_key: str | None = Field(default=None, pattern=_KEY)
    unit: IcaapAppetiteUnit | None = None
    direction: IcaapDirection | None = None
    appetite_value: Decimal | None = None
    tolerance_value: Decimal | None = None
    capacity_value: Decimal | None = None
    value_source: IcaapAppetiteValueSource | None = None
    manual_value: Decimal | None = None
    manual_evidence_attachment_id: UUID | None = None
    prior_value: Decimal | None = None
    prior_value_label: str | None = Field(default=None, max_length=80)
    board_approval_reference: str | None = Field(default=None, max_length=200)
    board_approved_on: date | None = None
    reason: str = Field(min_length=1, max_length=2000)


# --- capital triggers ------------------------------------------------------


class IcaapTriggerPointRead(ClosedModel):
    scenario_code: str
    year: int
    period_end: date | None = None
    value: Decimal | None = None
    status: str
    floor_pct: Decimal | None = None


class IcaapTriggerResultRead(ClosedModel):
    metric_code: str
    metric_key: str | None = None
    direction: IcaapDirection | None = None
    early_warning_level: Decimal | None = None
    action_level: Decimal | None = None
    current_value: Decimal | None = None
    current_status: str
    points: list[IcaapTriggerPointRead]
    first_crossing: dict[str, dict[str, int]]
    findings: list[str]


class IcaapTriggerEvaluationRead(ClosedModel):
    cycle_id: UUID
    plan_version: int | None = None
    trigger_count: int
    triggers_breached_now: int
    first_action_year: int | None = None
    results: list[IcaapTriggerResultRead]
    findings: list[str]
    floors: list[IcaapParameterUseRead]
    unavailable: dict[str, Any] | None = None


# --- Pillar 2 --------------------------------------------------------------


class IcaapPillar2ParameterUseRead(IcaapParameterUseRead):
    pass


class IcaapPillar2ItemRead(ClosedModel):
    id: UUID
    item_key: str
    risk_key: str
    category_key: str
    component_key: str
    table5_row: str | None = None
    method: str
    method_label: str
    method_version: str | None = None
    source: IcaapP2Source
    input_mode: IcaapP2InputMode
    method_status: IcaapP2Status
    status_detail: str | None = None
    basis: IcaapBasisKind | None = None
    basis_value: Decimal | None = None
    baseline_amount: Decimal | None = None
    stressed_amount: Decimal | None = None
    baseline_derivation: str | None = None
    stressed_derivation: str | None = None
    currency: str
    inputs_digest: str | None = None
    scenario_definition: dict[str, Any] | None = None
    rationale: str | None = None
    zero_amount_justification: str | None = None
    evidence_attachment_ids: list[UUID] = Field(default_factory=list)
    evidence_reference: str | None = None
    current_revision_no: int
    approved_revision_no: int | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    approval_note: str | None = None
    approval_current: bool
    stale: bool
    stale_reasons: list[str] = Field(default_factory=list)
    computation: dict[str, Any] | None = None
    parameters: list[IcaapParameterUseRead] = Field(default_factory=list)
    representative_parameters: list[str] = Field(default_factory=list)
    pending_parameters: list[str] = Field(default_factory=list)
    editable: bool
    approvable: bool
    updated_at: datetime | None = None


class IcaapPillar2ComponentSlotRead(ClosedModel):
    component_key: str
    category_key: str
    risk_key: str
    table5_row: str | None = None
    p29_class: str
    allowed_methods: list[str]
    deferred_methods: list[str]
    item_id: UUID | None = None


class IcaapTable5RowTotalRead(ClosedModel):
    row: str
    label: str
    baseline: Decimal | None = None
    stressed: Decimal | None = None
    item_keys: list[str]
    sources: list[str]
    partial: bool


class IcaapTable5TotalsRead(ClosedModel):
    rows: list[IcaapTable5RowTotalRead]
    total_baseline: Decimal | None = None
    total_stressed: Decimal | None = None
    partial_rows: list[str]


class IcaapConsistencyRead(ClosedModel):
    comparison_key: str
    basis: str
    comparator: str
    row: str
    icaap: Decimal | None = None
    other: Decimal | None = None
    relative_diff_pct: Decimal | None = None
    status: IcaapConsistencyStatus
    explanation: str | None = None
    explanation_current: bool = True


class IcaapPillar2FindingRead(ClosedModel):
    code: str
    ref: str
    params: dict[str, str]


class IcaapPillar2RegisterRead(ClosedModel):
    cycle_id: UUID
    currency: str
    items: list[IcaapPillar2ItemRead]
    components: list[IcaapPillar2ComponentSlotRead]
    table5_totals: IcaapTable5TotalsRead
    consistency: list[IcaapConsistencyRead]
    diversification_allowed: bool | None = None
    findings: list[IcaapPillar2FindingRead]
    parameters: list[IcaapParameterUseRead]


class IcaapPillar2ItemCreate(ClosedModel):
    component_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    method: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    source: IcaapP2Source = "icaap_method"
    input_mode: IcaapP2InputMode = "bound_blocks"
    rationale: str | None = Field(default=None, max_length=4000)
    evidence_attachment_ids: list[UUID] = Field(default_factory=list)
    evidence_reference: str | None = Field(default=None, max_length=300)
    scenario_definition: dict[str, Any] | None = None
    reason: str = Field(min_length=1, max_length=2000)


class IcaapPillar2ItemUpdate(ClosedModel):
    base_revision_no: int = Field(ge=0)
    method: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,39}$")
    source: IcaapP2Source | None = None
    input_mode: IcaapP2InputMode | None = None
    basis: IcaapBasisKind | None = None
    basis_value: Decimal | None = None
    baseline_amount: Decimal | None = None
    stressed_amount: Decimal | None = None
    stressed_same_as_baseline: bool = False
    rationale: str | None = Field(default=None, max_length=4000)
    zero_amount_justification: str | None = Field(default=None, max_length=4000)
    evidence_attachment_ids: list[UUID] | None = None
    evidence_reference: str | None = Field(default=None, max_length=300)
    scenario_definition: dict[str, Any] | None = None
    reason: str = Field(min_length=1, max_length=2000)


class IcaapPillar2ManualInputs(ClosedModel):
    """Consolidated cycles cannot read solo engine output (D-018), so the
    figures arrive by hand with an evidence attachment naming their source."""

    total_rwa: Decimal | None = None
    credit_rwa: Decimal | None = None
    market_rwa: Decimal | None = None
    operational_rwa: Decimal | None = None
    tier1: Decimal | None = None
    gross_income: Decimal | None = None
    stressed_total_rwa: Decimal | None = None
    stressed_credit_rwa: Decimal | None = None
    stressed_operational_rwa: Decimal | None = None
    concentration_single_name: list[Decimal] | None = None
    concentration_single_name_unstated: Decimal | None = None
    concentration_sector: list[Decimal] | None = None
    concentration_sector_unstated: Decimal | None = None
    fx_positions: dict[str, Decimal] | None = None
    irrbb_deltas: dict[str, Decimal] | None = None
    sovereign_holdings: list[dict[str, Any]] | None = None
    operational_scenarios: list[dict[str, Any]] | None = None


class IcaapPillar2Compute(ClosedModel):
    base_revision_no: int = Field(ge=0)
    manual_inputs: IcaapPillar2ManualInputs | None = None
    reason: str = Field(min_length=1, max_length=2000)


class IcaapPillar2Approve(ClosedModel):
    revision_no: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2000)


class IcaapPillar2RevisionRead(ClosedModel):
    id: UUID
    revision_no: int
    change_kind: str
    round: int
    snapshot: dict[str, Any]
    computation: dict[str, Any] | None = None
    inputs_digest: str | None = None
    snapshot_sha256: str
    note: str | None = None
    created_by: UUID
    created_at: datetime


class IcaapPillar2RevisionListRead(ClosedModel):
    item_id: UUID
    revisions: list[IcaapPillar2RevisionRead]


class IcaapTable5CellRead(ClosedModel):
    column: str
    value: Decimal | None = None


class IcaapTable5GridRowRead(ClosedModel):
    key: str
    label: str
    group: str
    partial: bool
    cells: list[IcaapTable5CellRead]


class IcaapTable5ColumnRead(ClosedModel):
    key: str
    label: str
    basis: str


class IcaapTable5Read(ClosedModel):
    cycle_id: UUID
    unit: str
    currency: str
    columns: list[IcaapTable5ColumnRead]
    rows: list[IcaapTable5GridRowRead]
    partial_rows: list[str]
    register_total_baseline: Decimal | None = None
    register_total_stressed: Decimal | None = None
    notes: list[str]
    findings: list[IcaapPillar2FindingRead]
    available: bool
    unavailable_reason: str | None = None


class IcaapCapitalPlanProposalCreate(ClosedModel):
    reason: str = Field(min_length=1, max_length=2000)
    replace_existing_draft: bool = False


class IcaapCapitalPlanAddonRead(ClosedModel):
    risk_type: str
    add_on_pct_rwa: Decimal
    amount: Decimal
    item_key: str
    item_revision_no: int
    rationale: str


class IcaapCapitalPlanProposalRead(ClosedModel):
    plan_id: UUID
    version: int
    status: str
    total_rwa: Decimal
    currency: str
    addons: list[IcaapCapitalPlanAddonRead]
    #: A negative diversification component is never carried into a plan
    #: add-on (plan add-ons are >= 0); this says so rather than dropping it
    #: silently.
    excluded_item_keys: list[str]
    notes: list[str]


# --- reconciliation --------------------------------------------------------


class IcaapRequirementLineRead(ClosedModel):
    line_key: str
    line_group: str
    position: int
    label: str
    table5_row: str | None = None
    pillar1_amount: Decimal | None = None
    pillar2_amount: Decimal | None = None
    internal_amount: Decimal | None = None
    regulatory_amount: Decimal | None = None
    supervisory_amount: Decimal | None = None
    difference: Decimal | None = None
    explanation_required: bool
    explanation: str | None = None
    explanation_current: bool = True
    explanation_by: UUID | None = None
    explanation_at: datetime | None = None


class IcaapRequirementTotalsRead(ClosedModel):
    total_internal_requirement: Decimal | None = None
    total_regulatory_requirement: Decimal | None = None
    difference: Decimal | None = None
    explanation_required: bool = False


class IcaapCapitalRequirementRead(ClosedModel):
    lines: list[IcaapRequirementLineRead]
    totals: IcaapRequirementTotalsRead
    computed_at: datetime | None = None
    computed_by: UUID | None = None
    computed_digest: str | None = None
    stale: bool
    computation: dict[str, Any] | None = None


class IcaapResourcesLineRead(ClosedModel):
    id: UUID
    line_key: str
    position: int
    label: str
    tier: IcaapCapitalTier
    origin: IcaapResourceOrigin
    regulatory_component_key: str | None = None
    regulatory_amount: Decimal | None = None
    internal_amount: Decimal
    recognised_amount: Decimal | None = None
    above_cap_amount: Decimal | None = None
    regulatory_eligible: bool
    explanation: str | None = None
    evidence_attachment_id: UUID | None = None
    source_binding_ref: dict[str, Any] | None = None
    explanation_required: bool = False
    row_rev: int


class IcaapResourcesTotalsRead(ClosedModel):
    available_internal_capital: Decimal | None = None
    recognised_regulatory_capital: Decimal | None = None
    regulatory_total_capital: Decimal | None = None
    matches_regulatory_total: bool | None = None
    internal_capital_surplus: Decimal | None = None
    internal_capital_coverage_pct: Decimal | None = None


class IcaapResourcesRead(ClosedModel):
    lines: list[IcaapResourcesLineRead]
    totals: IcaapResourcesTotalsRead
    caps: list[IcaapParameterUseRead]


class IcaapControlExplanationRead(ClosedModel):
    control_code: str
    comparison_key: str
    explanation: str
    explained_by: UUID
    explained_at: datetime
    current: bool


class IcaapReconciliationRead(ClosedModel):
    cycle_id: UUID
    currency: str
    requirement: IcaapCapitalRequirementRead
    resources: IcaapResourcesRead
    controls: list[IcaapConsistencyRead]
    control_explanations: list[IcaapControlExplanationRead]
    parameters: list[IcaapParameterUseRead]


class IcaapResourcesLineCreate(ClosedModel):
    line_key: str = Field(pattern=_KEY)
    label: str = Field(min_length=1, max_length=200)
    tier: IcaapCapitalTier
    internal_amount: Decimal
    regulatory_amount: Decimal | None = None
    regulatory_eligible: bool
    regulatory_component_key: str | None = Field(default=None, max_length=80)
    explanation: str | None = Field(default=None, max_length=4000)
    evidence_attachment_id: UUID | None = None
    position: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class IcaapResourcesLineUpdate(ClosedModel):
    base_rev: int = Field(ge=0)
    label: str | None = Field(default=None, min_length=1, max_length=200)
    tier: IcaapCapitalTier | None = None
    internal_amount: Decimal | None = None
    regulatory_amount: Decimal | None = None
    regulatory_eligible: bool | None = None
    explanation: str | None = Field(default=None, max_length=4000)
    evidence_attachment_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)


# --- allocation ------------------------------------------------------------


class IcaapAllocationDriverPut(ClosedModel):
    unit_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    risk_line_key: str = Field(pattern=_KEY)
    driver_kind: IcaapDriverKind
    driver_value: Decimal = Field(ge=0)


class IcaapAllocationUnitPut(ClosedModel):
    unit_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    unit_label: str = Field(min_length=1, max_length=200)
    unit_kind: IcaapUnitKind


class IcaapAllocationPut(ClosedModel):
    base_digest: str | None = Field(default=None, max_length=64)
    units: list[IcaapAllocationUnitPut]
    drivers: list[IcaapAllocationDriverPut]
    reason: str = Field(min_length=1, max_length=2000)


class IcaapAllocationUnitRead(ClosedModel):
    unit_key: str
    unit_label: str
    unit_kind: IcaapUnitKind
    total_allocated: Decimal | None = None


class IcaapAllocationCellRead(ClosedModel):
    unit_key: str
    risk_line_key: str
    driver_kind: IcaapDriverKind
    driver_value: Decimal
    allocated_amount: Decimal | None = None


class IcaapAllocationRead(ClosedModel):
    cycle_id: UUID
    currency: str
    units: list[IcaapAllocationUnitRead]
    lines: list[IcaapRequirementLineRead]
    cells: list[IcaapAllocationCellRead]
    digest: str | None = None
    total_allocated: Decimal | None = None
    available: bool
    unavailable_reason: str | None = None


# --- audit review ----------------------------------------------------------


class IcaapAuditFindingWrite(ClosedModel):
    ref: str = Field(min_length=1, max_length=40)
    severity: IcaapSeverityLevel
    finding: str = Field(min_length=1, max_length=4000)
    management_response: str | None = Field(default=None, max_length=4000)
    target_date: date | None = None
    status: Literal["open", "closed"] = "open"


class IcaapAuditReviewRead(ClosedModel):
    id: UUID
    status: IcaapReviewStatus
    review_kind: IcaapReviewKind
    reviewer_function: str
    scope: str
    frequency_statement: str
    performed_from: date | None = None
    performed_on: date
    period_covered: str | None = None
    reviewed_cycle_id: UUID | None = None
    overall_opinion: IcaapReviewOpinion
    findings: list[IcaapAuditFindingWrite]
    open_findings_count: int
    report_attachment_id: UUID | None = None
    independence_statement: str
    recorded_by: UUID
    finalised_at: datetime | None = None
    finalised_by: UUID | None = None
    supersedes_review_id: UUID | None = None
    superseded_at: datetime | None = None
    superseded_by_review_id: UUID | None = None
    row_rev: int
    created_at: datetime | None = None


class IcaapAuditReviewListRead(ClosedModel):
    cycle_id: UUID
    reviews: list[IcaapAuditReviewRead]
    latest_review_date: date | None = None
    latest_review_opinion: str | None = None
    open_findings_count: int


class IcaapAuditReviewCreate(ClosedModel):
    review_kind: IcaapReviewKind
    reviewer_function: str = Field(min_length=1, max_length=120)
    scope: str = Field(min_length=1, max_length=4000)
    frequency_statement: str = Field(min_length=1, max_length=4000)
    performed_on: date
    performed_from: date | None = None
    period_covered: str | None = Field(default=None, max_length=2000)
    reviewed_cycle_id: UUID | None = None
    overall_opinion: IcaapReviewOpinion
    findings: list[IcaapAuditFindingWrite] = Field(default_factory=list)
    report_attachment_id: UUID | None = None
    independence_statement: str = Field(min_length=1, max_length=4000)
    supersedes_review_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)


class IcaapAuditReviewUpdate(ClosedModel):
    base_rev: int = Field(ge=0)
    reviewer_function: str | None = Field(default=None, min_length=1, max_length=120)
    scope: str | None = Field(default=None, min_length=1, max_length=4000)
    frequency_statement: str | None = Field(default=None, min_length=1, max_length=4000)
    performed_on: date | None = None
    performed_from: date | None = None
    period_covered: str | None = Field(default=None, max_length=2000)
    overall_opinion: IcaapReviewOpinion | None = None
    findings: list[IcaapAuditFindingWrite] | None = None
    report_attachment_id: UUID | None = None
    independence_statement: str | None = Field(default=None, min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)


# --- challenge log ---------------------------------------------------------


class IcaapChallengeResponseRead(ClosedModel):
    id: UUID
    response_no: int
    outcome: IcaapChallengeOutcome
    response_text: str
    change_references: list[dict[str, Any]]
    responder_function: str
    responded_by: UUID
    created_at: datetime


class IcaapChallengeRead(ClosedModel):
    id: UUID
    challenge_no: int
    round: int
    raised_in: IcaapChallengeForum
    raised_by_name: str
    raised_on: date
    meeting_reference: str | None = None
    target_kind: IcaapChallengeTarget
    target_ref: str | None = None
    challenge_text: str
    severity: IcaapSeverityLevel
    minutes_attachment_id: UUID | None = None
    recorded_by: UUID
    created_at: datetime
    responses: list[IcaapChallengeResponseRead]
    open: bool


class IcaapChallengeListRead(ClosedModel):
    cycle_id: UUID
    challenges: list[IcaapChallengeRead]
    challenge_count: int
    open_challenge_count: int
    board_challenge_count: int


class IcaapChallengeCreate(ClosedModel):
    raised_in: IcaapChallengeForum
    raised_by_name: str = Field(min_length=1, max_length=200)
    raised_on: date
    meeting_reference: str | None = Field(default=None, max_length=200)
    target_kind: IcaapChallengeTarget
    target_ref: str | None = Field(default=None, max_length=120)
    challenge_text: str = Field(min_length=1, max_length=8000)
    severity: IcaapSeverityLevel
    minutes_attachment_id: UUID | None = None


class IcaapChallengeResponseCreate(ClosedModel):
    outcome: IcaapChallengeOutcome
    response_text: str = Field(min_length=1, max_length=8000)
    change_references: list[dict[str, Any]] = Field(default_factory=list)
    responder_function: str = Field(min_length=1, max_length=120)


# --- supervisory add-ons ---------------------------------------------------


class IcaapSupervisoryAddonRead(ClosedModel):
    id: UUID
    status: IcaapAddonStatus
    letter_reference: str
    letter_date: date
    effective_from: date
    effective_to: date | None = None
    applies_to_basis: str
    table5_row: str | None = None
    component_key: str | None = None
    basis: IcaapBasisKind
    basis_value: Decimal
    currency: str
    description: str | None = None
    letter_original_filename: str
    letter_media_type: str
    letter_byte_size: int
    letter_sha256: str
    supersedes_addon_id: UUID | None = None
    superseded_by_addon_id: UUID | None = None
    created_by: UUID
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    withdrawn_at: datetime | None = None
    withdrawal_reason: str | None = None
    #: The converted amount at the requested as-of date, when the denominators
    #: for its basis are available; ``null`` when they are not.
    amount_at_as_of: Decimal | None = None
    created_at: datetime | None = None


class IcaapSupervisoryAddonListRead(ClosedModel):
    bank_id: str
    as_of: date | None = None
    currency: str
    addons: list[IcaapSupervisoryAddonRead]
    total_amount_at_as_of: Decimal | None = None
    never_public: bool = True


__all__ = [
    "IcaapAllocationCellRead",
    "IcaapAllocationDriverPut",
    "IcaapAllocationPut",
    "IcaapAllocationRead",
    "IcaapAllocationUnitPut",
    "IcaapAllocationUnitRead",
    "IcaapAppetiteEvaluationRead",
    "IcaapAppetiteMetricCreate",
    "IcaapAppetiteMetricDefRead",
    "IcaapAppetiteMetricRead",
    "IcaapAppetiteMetricUpdate",
    "IcaapAppetiteRead",
    "IcaapAppetiteSummaryRead",
    "IcaapAuditFindingWrite",
    "IcaapAuditReviewCreate",
    "IcaapAuditReviewListRead",
    "IcaapAuditReviewRead",
    "IcaapAuditReviewUpdate",
    "IcaapCapitalPlanAddonRead",
    "IcaapCapitalPlanProposalCreate",
    "IcaapCapitalPlanProposalRead",
    "IcaapChallengeCreate",
    "IcaapChallengeListRead",
    "IcaapChallengeRead",
    "IcaapChallengeResponseCreate",
    "IcaapChallengeResponseRead",
    "IcaapConsistencyRead",
    "IcaapControlExplanationRead",
    "IcaapCustomRiskCreate",
    "IcaapExplanation",
    "IcaapMaterialityBandRead",
    "IcaapMaterialityCellRead",
    "IcaapMaterialityLevelRead",
    "IcaapMaterialityMatrixRead",
    "IcaapParameterUseListRead",
    "IcaapParameterUseRead",
    "IcaapPillar2Approve",
    "IcaapPillar2ComponentSlotRead",
    "IcaapPillar2Compute",
    "IcaapPillar2FindingRead",
    "IcaapPillar2ItemCreate",
    "IcaapPillar2ItemRead",
    "IcaapPillar2ItemUpdate",
    "IcaapPillar2ManualInputs",
    "IcaapPillar2RegisterRead",
    "IcaapPillar2RevisionListRead",
    "IcaapPillar2RevisionRead",
    "IcaapReason",
    "IcaapReconciliationRead",
    "IcaapRegulatoryReferenceRead",
    "IcaapRequirementLineRead",
    "IcaapCapitalRequirementRead",
    "IcaapRequirementTotalsRead",
    "IcaapResourcesLineCreate",
    "IcaapResourcesLineRead",
    "IcaapResourcesLineUpdate",
    "IcaapResourcesRead",
    "IcaapResourcesTotalsRead",
    "IcaapRetire",
    "IcaapRiskComponentRead",
    "IcaapRiskPut",
    "IcaapRiskRead",
    "IcaapRiskRegisterRead",
    "IcaapRiskRegisterSummaryRead",
    "IcaapSupervisoryAddonListRead",
    "IcaapSupervisoryAddonRead",
    "IcaapTable5CellRead",
    "IcaapTable5ColumnRead",
    "IcaapTable5GridRowRead",
    "IcaapTable5Read",
    "IcaapTable5RowTotalRead",
    "IcaapTable5TotalsRead",
    "IcaapTriggerEvaluationRead",
    "IcaapTriggerPointRead",
    "IcaapTriggerResultRead",
]
