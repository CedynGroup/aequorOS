"""SDI diagnostics API contracts (docs/sdi.md §11, §4.2).

Read-only surfaces over the SDI services: per-module data-quality readiness and
the simplified-capital regulatory checks (paid-up floor, statutory reserve fund).
Every value carries its provenance + confirmation status so a pending default is
never presented as authoritative.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModuleReadinessRead(ClosedModel):
    #: Module slug (e.g. 'liquidity_table1', 'capital', 'provisioning').
    module: str
    #: 'ready' | 'partial' | 'blocked'.
    status: str
    #: The specific missing/degraded-data reasons behind a non-ready status.
    reasons: list[str]


class SdiReadinessRead(ClosedModel):
    as_of: str
    modules: list[ModuleReadinessRead]


class CapitalCheckRead(ClosedModel):
    check: str
    #: True/False when computable; null when the required data is absent.
    compliant: bool | None
    actual_ghs: Decimal | None
    required_ghs: Decimal | None
    detail: str
    source_citation: str
    #: 'confirmed' | 'pending' — whether the threshold is a confirmed regulatory
    #: fact or a documented default awaiting BoG confirmation.
    confirmation_status: str


class SdiCapitalChecksRead(ClosedModel):
    as_of: str
    checks: list[CapitalCheckRead]


class LoanGradeBucketRead(ClosedModel):
    grade: str
    count: int
    exposure_ghs: Decimal
    provision_required_ghs: Decimal
    non_performing: bool


class DelinquencyBucketRead(ClosedModel):
    #: Analytical raw-DPD bucket, not a regulatory classification grade.
    code: str
    label: str
    count: int
    exposure_ghs: Decimal


class PortfolioAtRiskRead(ClosedModel):
    code: str
    label: str
    exposure_ghs: Decimal
    #: Share of gross loan exposure, as a fraction.
    ratio: Decimal


class ProvisionsHeldRead(ClosedModel):
    """Provisions the bank HOLDS (stated on ingested loans), split by the
    applied classification. Present only when at least one loan states a
    provision — an unstated book carries ``null``, never a fabricated zero."""

    specific_ghs: Decimal
    general_ghs: Decimal
    total_ghs: Decimal
    interest_in_suspense_ghs: Decimal
    #: Loans that stated a provision amount (coverage disclosure).
    stated_loan_count: int


class SdiLoanClassificationRead(ClosedModel):
    as_of: str
    #: 'bank' (5-grade) or 'sdi' (NBFI 4-grade) — which grid was applied.
    institution_class: str
    loan_count: int
    total_exposure_ghs: Decimal
    npl_exposure_ghs: Decimal
    #: NPL / total exposure as a fraction.
    npl_ratio: Decimal
    total_provision_required_ghs: Decimal
    #: Loans classified via the IFRS-9 stage proxy (no stated days-past-due).
    stage_proxy_count: int
    #: Loans with a stated raw DPD, the only loans included in exact DPD buckets.
    dpd_covered_count: int
    dpd_covered_exposure_ghs: Decimal
    #: Loans with neither DPD nor stage — booked ``unclassified``, never performing.
    unclassified_count: int
    buckets: list[LoanGradeBucketRead]
    delinquency_buckets: list[DelinquencyBucketRead]
    portfolio_at_risk: list[PortfolioAtRiskRead]
    #: Param codes whose value is still pending BoG/internal confirmation.
    pending_parameters: list[str]
    #: Provisions HELD against the book; ``null`` when no loan states one.
    provisions_held: ProvisionsHeldRead | None = None
    #: Specific provisions held ÷ NPL exposure (%); ``null`` when provisions are
    #: unstated or there is no NPL exposure to cover.
    provision_coverage_pct: Decimal | None = None


class RiskWeightBandRead(ClosedModel):
    bucket: str
    weight_pct: Decimal
    exposure_ghs: Decimal
    rwa_ghs: Decimal
    #: 'confirmed' or 'pending' (unconfirmed control-plane risk weight).
    confirmation_status: str


class SdiRwaRiskClassRead(ClosedModel):
    """One risk class of the declared RWA composition — in scope or not.

    Out-of-scope classes are returned deliberately: a capital ratio computed on
    credit risk alone has to say so on the surface that presents it.
    """

    risk_class: str
    in_scope: bool
    #: The declared measurement, null when the class is out of scope.
    measurement: str | None
    rwa_ghs: Decimal
    #: What this class contributes and why — reader-facing copy.
    note: str


class SdiCapitalSummaryRead(ClosedModel):
    """The s.29 capital-adequacy summary: CAR = Net Own Funds ÷ RWA vs the floor."""

    as_of: str
    net_own_funds_ghs: Decimal
    total_rwa_ghs: Decimal
    #: CAR as a percentage; null when RWA is zero (no risk assets ingested).
    car_pct: Decimal | None
    #: The s.29 floor (percent) from the control plane.
    car_min_pct: Decimal
    #: 'green' (>= floor) | 'red' (< floor) | 'na' (not computable).
    status: str
    #: Confirmation status of the resolved CAR floor.
    car_min_confirmation: str
    computable: bool
    bands: list[RiskWeightBandRead]
    #: Risk-weight param codes whose value is still pending confirmation.
    pending_parameters: list[str]
    #: 'control_plane' when a governed composition declared which risk classes
    #: the ratio covers, 'code_default' while none exists (ratio provisional).
    composition_source: str
    #: Every known risk class, in scope or not, with what it contributed.
    risk_classes: list[SdiRwaRiskClassRead]
    #: One sentence stating what this ratio charges for and what it omits.
    rwa_scope_note: str


class SdiCapitalHistoryPointRead(ClosedModel):
    as_of: str
    net_own_funds_ghs: Decimal
    total_rwa_ghs: Decimal
    car_pct: Decimal | None
    capital_headroom_ghs: Decimal | None
    npl_exposure_ghs: Decimal
    npl_ratio: Decimal
    required_provision_ghs: Decimal
    actual_provision_ghs: Decimal | None
    provision_coverage_pct: Decimal | None
    #: 'provisional' when any regulatory input remains unconfirmed.
    assessment_status: str


class SdiCapitalAssuranceRead(ClosedModel):
    as_of: str
    current: SdiCapitalHistoryPointRead
    history: list[SdiCapitalHistoryPointRead]
    mapped_gl_capital_ghs: Decimal | None
    capital_to_gl_difference_ghs: Decimal | None
    #: 'mapped' only when every capital component identifies its GL account code.
    gl_reconciliation_status: str
    reserve_change_ghs: Decimal | None
    #: Filing remains blocked until all listed evidence conditions are resolved.
    filing_status: str
    filing_blockers: list[str]


class SdiLiquidityRatioRead(ClosedModel):
    code: str
    label: str
    value_pct: Decimal | None
    threshold_pct: Decimal
    status: str
    threshold_source: str


class SdiLiquidityReserveRead(ClosedModel):
    code: str
    label: str
    value_pct: Decimal | None
    threshold_pct: Decimal
    status: str
    source_citation: str
    confirmation_status: str


class SdiMaturityBucketRead(ClosedModel):
    code: str
    label: str
    net_mismatch_ghs: Decimal
    cumulative_mismatch_ghs: Decimal | None


class SdiFundingProviderRead(ClosedModel):
    name: str
    deposit_ghs: Decimal
    pct_total_deposits: Decimal | None
    related: bool


class SdiFundingConcentrationRead(ClosedModel):
    total_deposits_ghs: Decimal
    top_five_deposits_ghs: Decimal
    top_five_pct: Decimal | None
    unattributed_deposits_ghs: Decimal
    providers: list[SdiFundingProviderRead]


class SdiCounterbalancingCapacityRead(ClosedModel):
    gross_unencumbered_ghs: Decimal
    monetized_value_ghs: Decimal
    bog_eligible_ghs: Decimal
    uncalibrated_asset_count: int


class LiquidityMonitoringRead(ClosedModel):
    """Institution-neutral canonical liquidity-monitoring analytics."""

    as_of: str
    institution_class: str
    maturity_ladder: list[SdiMaturityBucketRead]
    funding_concentration: SdiFundingConcentrationRead
    counterbalancing_capacity: SdiCounterbalancingCapacityRead
    readiness: list[ModuleReadinessRead]


class SdiLiquidityPositionRead(ClosedModel):
    as_of: str
    ratios: list[SdiLiquidityRatioRead]
    reserves: list[SdiLiquidityReserveRead]
    maturity_ladder: list[SdiMaturityBucketRead]
    funding_concentration: SdiFundingConcentrationRead
    counterbalancing_capacity: SdiCounterbalancingCapacityRead
    readiness: list[ModuleReadinessRead]


class SdiExposureRead(ClosedModel):
    counterparty_name: str
    connection: str
    exposure_ghs: Decimal
    pct_net_own_funds: Decimal | None
    single_obligor_limit_pct: Decimal
    large_exposure_limit_pct: Decimal
    status: str
    exempt: bool


class SdiLargeExposuresRead(ClosedModel):
    as_of: str
    net_own_funds_ghs: Decimal
    exposures: list[SdiExposureRead]
    findings: list[str]
