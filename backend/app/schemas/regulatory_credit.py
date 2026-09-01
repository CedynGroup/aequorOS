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
