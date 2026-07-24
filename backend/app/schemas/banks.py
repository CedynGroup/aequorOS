from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.common import JsonObject

type BankReportingPeriodStatus = Literal["open", "closed"]
type BankFactGroup = Literal[
    "balance_sheet",
    "loan_exposure",
    "securities",
    "off_balance",
    "lcr_inflow",
    "market_risk",
    "operational_income",
    "capital_component",
    "deposit_behavior",
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JurisdictionRead(ClosedModel):
    """Country identity resolved from the jurisdictions registry — the source
    of every country-derived display value (currency, locale, regulator)."""

    code: str
    country_name: str
    currency_code: str
    currency_name: str
    locale: str
    central_bank_name: str
    regulator_short: str
    submission_portal: str | None
    timezone: str | None


class BankRead(ClosedModel):
    id: UUID
    organization_id: UUID
    name: str
    short_name: str
    currency: str
    jurisdiction_code: str
    license_type: str
    # Resolved from the registry; None only if the code has no registry row
    # (clients fall back to the raw code + bank currency).
    jurisdiction: JurisdictionRead | None = None
    created_at: datetime
    updated_at: datetime


class BankListRead(ClosedModel):
    banks: list[BankRead]


class BankReportingPeriodRead(ClosedModel):
    id: UUID
    bank_id: UUID
    period_start: date
    period_end: date
    label: str
    status: BankReportingPeriodStatus


class BankReportingPeriodListRead(ClosedModel):
    bank_id: UUID
    periods: list[BankReportingPeriodRead]


class BankFactRead(ClosedModel):
    id: UUID
    fact_group: BankFactGroup
    category: str
    amount: Decimal
    currency: str
    risk_weight_code: str | None
    hqla_level: str | None
    ccf_pct: Decimal | None
    rate_pct: Decimal | None
    income_year: int | None
    capital_tier: str | None
    is_deduction: bool
    attributes: JsonObject


class BankFactsRead(ClosedModel):
    period: BankReportingPeriodRead
    balance_sheet: list[BankFactRead]
    loan_exposures: list[BankFactRead]
    securities: list[BankFactRead]
    off_balance: list[BankFactRead]
    lcr_inflows: list[BankFactRead]
    market_risk: list[BankFactRead]
    operational_income: list[BankFactRead]
    capital_components: list[BankFactRead]
    deposit_behavior: list[BankFactRead]


class BankSeedSummaryRead(ClosedModel):
    bank_id: UUID
    periods: int
    fact_count: int
    param_count: int
