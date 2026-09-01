"""Credit / Loan Book module wire contracts (credit PR-2).

The dashboard payload mirrors the loan-classification read (grades, raw-DPD
delinquency, portfolio-at-risk, provisions held) and adds the module frame:
the stored-vs-inline flag, the latest sealed baseline run, validation rows,
the NPL prudential limit resolved from the control plane, and the live block.

Absence discipline (house rule): an unavailable figure is ``None`` on the wire
— provisions held on an unstated book, coverage with no NPL exposure, the NPL
limit when the control-plane row is unseeded — never a fabricated zero.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.live import LiveModuleView
from app.schemas.regulatory_liquidity import RegulatoryValidationSeverity
from app.schemas.sdi import (
    DelinquencyBucketRead,
    LoanGradeBucketRead,
    PortfolioAtRiskRead,
    ProvisionsHeldRead,
)


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type CreditStatus = Literal["green", "amber", "red", "na"]


class CreditScenarioBatchCreate(ClosedModel):
    reporting_period_id: UUID


class CreditValidationRead(ClosedModel):
    rule_code: str
    passed: bool
    severity: RegulatoryValidationSeverity
    message: str


class CreditMetricsRead(ClosedModel):
    """The headline portfolio-quality figures."""

    gross_loans_ghs: Decimal
    loan_count: int
    npl_exposure_ghs: Decimal
    #: NPL ÷ gross loans, as a PERCENTAGE (the Notice 2025/23 basis).
    npl_ratio_pct: Decimal
    #: The prudential NPL ceiling resolved from the control plane (Notice
    #: BG/GOV/SEC/2025/23: 10%). ``None`` when the parameter is unseeded —
    #: the ratio is then reported without a compliance colour, never against
    #: an invented limit.
    npl_limit_pct: Decimal | None = None
    #: The level at which dividend/lending restrictions apply immediately
    #: (Notice 2025/23: 15%).
    npl_restriction_level_pct: Decimal | None = None
    npl_status: CreditStatus = "na"
    total_provision_required_ghs: Decimal
    provisions_held: ProvisionsHeldRead | None = None
    provision_coverage_pct: Decimal | None = None
    unclassified_exposure_ghs: Decimal
    unclassified_count: int
    stage_proxy_count: int
    dpd_covered_count: int


class CreditDashboardRead(ClosedModel):
    bank: BankRead
    period: BankReportingPeriodRead
    #: True when the figures come from the sealed baseline run for the named
    #: period; False when computed inline from the current canonical book.
    stored: bool
    latest_run_id: UUID | None = None
    #: 'bank' (5-grade incl. OLEM) or 'sdi' (NBFI 4-grade).
    institution_class: str
    metrics: CreditMetricsRead
    grades: list[LoanGradeBucketRead]
    delinquency_buckets: list[DelinquencyBucketRead]
    portfolio_at_risk: list[PortfolioAtRiskRead]
    validations: list[CreditValidationRead]
    pending_parameters: list[str]
    live: LiveModuleView | None = None


class CreditLoanRead(ClosedModel):
    """One loan on the blotter, classified under the tenant's grid."""

    source_reference: str
    counterparty_name: str | None = None
    product_code: str | None = None
    branch_id: str | None = None
    sector: str | None = None
    currency: str
    exposure_ghs: Decimal
    days_past_due: int | None = None
    ifrs9_stage: int | None = None
    grade: str
    non_performing: bool
    classification_basis: str
    provision_required_ghs: Decimal
    #: Provision the bank states it holds on this loan; None = not stated.
    provision_held_ghs: Decimal | None = None
    #: The ingested ``restructured`` flag.
    restructured: bool = False
    interest_rate: Decimal | None = None
    contractual_maturity: str | None = None
    origination_date: str | None = None


class CreditLoansPageRead(ClosedModel):
    as_of: str
    total: int
    #: Loans matching the active filters (pagination denominator).
    filtered: int
    limit: int
    offset: int
    rows: list[CreditLoanRead]


class CreditFacetCountRead(ClosedModel):
    value: str
    count: int


class CreditLoanFacetsRead(ClosedModel):
    as_of: str
    grades: list[CreditFacetCountRead]
    products: list[CreditFacetCountRead]
    branches: list[CreditFacetCountRead]
    sectors: list[CreditFacetCountRead]


# --- concentration monitor (credit PR-3) -----------------------------------


class ConcentrationBucketRead(ClosedModel):
    key: str
    exposure_ghs: Decimal
    loan_count: int
    share_of_book_pct: Decimal
    #: ``null`` when no capital base resolves — not computable, never 0.
    share_of_capital_pct: Decimal | None = None
    limit_value: Decimal | None = None
    #: share_of_book_pct | share_of_capital_pct when a limit applies.
    limit_kind: str | None = None
    #: within_limit | above_limit | not_set | not_computable
    limit_status: str
    utilization_pct: Decimal | None = None


class ConcentrationDimensionRead(ClosedModel):
    dimension: str
    #: Herfindahl–Hirschman index on the 0–10,000 basis, over STATED exposure.
    hhi: Decimal
    bucket_count: int
    #: Share of the total book that states this dimension at all.
    coverage_pct: Decimal
    stated_exposure_ghs: Decimal
    buckets: list[ConcentrationBucketRead]
    hhi_limit: Decimal | None = None
    hhi_status: str


class CreditConcentrationRead(ClosedModel):
    as_of: str
    total_book_ghs: Decimal
    #: The regime-scoped denominator (SDI: Act 930 s.29 Net Own Funds; bank:
    #: Tier 1 from current capital components); ``null`` when unresolvable.
    capital_base_ghs: Decimal | None = None
    #: 'net_own_funds' | 'tier1' — what the capital basis IS, so the UI labels
    #: the column truthfully per regime.
    capital_basis: str
    dimensions: list[ConcentrationDimensionRead]
    breaches: list[ConcentrationBucketRead]
    #: Board limit rows active at ``as_of`` (empty = none configured yet).
    limit_count: int


class ConcentrationLimitRead(ClosedModel):
    dimension: str
    limit_kind: str
    bucket_key: str | None = None
    value: Decimal
    effective_from: str
    approved_by: str


class ConcentrationLimitEntry(ClosedModel):
    dimension: str
    limit_kind: str
    bucket_key: str | None = None
    value: Decimal


class ConcentrationLimitRegisterRead(ClosedModel):
    as_of: str
    limits: list[ConcentrationLimitRead]


class ConcentrationLimitUpdate(ClosedModel):
    effective_from: date
    approved_by: str
    reason: str
    limits: list[ConcentrationLimitEntry]


class CreditThresholdRead(ClosedModel):
    threshold_code: str
    value_pct: Decimal
    effective_from: str
    approved_by: str


class CreditThresholdRegisterRead(ClosedModel):
    as_of: str
    thresholds: list[CreditThresholdRead]


class CreditThresholdUpdate(ClosedModel):
    effective_from: date
    approved_by: str
    reason: str
    thresholds: dict[str, Decimal]


# --- loan events / activity (credit PR-4) ----------------------------------


class LoanEventRead(ClosedModel):
    source_reference: str
    event_type: str
    event_subtype: str | None = None
    event_date: str
    position_source_reference: str
    amount: Decimal
    currency: str
    #: Reporting-unit amount; ``null`` = unconverted foreign currency.
    amount_ghs: Decimal | None = None


class MonthlyFlowRead(ClosedModel):
    month: str
    write_offs_ghs: Decimal
    recoveries_ghs: Decimal


class CreditActivityRead(ClosedModel):
    as_of: str
    window_start: str
    restructures: list[LoanEventRead]
    write_offs: list[LoanEventRead]
    recoveries: list[LoanEventRead]
    disbursement_count: int
    repayment_count: int
    monthly_flows: list[MonthlyFlowRead]


# --- monthly migration (credit PR-5) ---------------------------------------


class MigrationCellRead(ClosedModel):
    from_state: str
    to_state: str
    exposure_ghs: Decimal
    loan_count: int


class RollRateCellRead(ClosedModel):
    from_bucket: str
    to_bucket: str
    exposure_ghs: Decimal
    loan_count: int
    #: Exposure-weighted share of the from-bucket's matched opening exposure.
    rate_pct: Decimal


class CreditMigrationRead(ClosedModel):
    as_of: str
    #: False = insufficient history (one month-end only); a soft state with a
    #: reason, never the module-unavailable envelope.
    available: bool
    reason: str | None = None
    opening_as_of: str | None = None
    opening_total_ghs: Decimal | None = None
    closing_total_ghs: Decimal | None = None
    matrix: list[MigrationCellRead] = []
    entries: list[MigrationCellRead] = []
    exits: list[MigrationCellRead] = []
    roll_rates: list[RollRateCellRead] = []
    matched_loan_count: int = 0
    entry_loan_count: int = 0
    exit_loan_count: int = 0
