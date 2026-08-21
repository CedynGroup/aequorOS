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
    # "cashflow" is served by the facts view (services/banks._FACT_GROUP_FIELDS
    # maps it to cash_flows) but was missing here, so any period carrying
    # cash-flow facts 500'd on serialisation (found by the real-DB tests,
    # 2026-08-16). Keep this Literal in step with _FACT_GROUP_FIELDS.
    "cashflow",
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
    sovereign_rating_issuer: str | None = None
    submission_portal: str | None
    timezone: str | None


class InstitutionTypeRead(ClosedModel):
    """Institution-type classification resolved from the institution_types
    registry — the typed discriminator every future SDI scoping keys off
    (docs/sdi.md §1). ``institution_class`` is the coarse regime axis
    ('bank'|'sdi') the threshold register and later capital register key on."""

    type_code: str
    display_name: str
    institution_class: str
    return_family: str
    capital_regime: str
    large_exposure_limit_pct: Decimal
    single_obligor_limit_pct: Decimal
    liquidity_binding: bool
    default_modules: list[str]


class BankRead(ClosedModel):
    # THE institution identifier (BK-XXXXXXXX) and tenant identifier
    # (OR-XXXXXXXX) — platform-generated primary keys, no UUID aliases.
    id: str
    organization_id: str
    name: str
    short_name: str
    currency: str
    jurisdiction_code: str
    license_type: str
    # THE typed institution discriminator (docs/sdi.md §1) — the raw licence
    # code carried on the bank, always present (NOT NULL). Rides the payload
    # exactly as ``jurisdiction_code`` does.
    institution_type: str
    # Resolved from the registry; None only if the code has no registry row
    # (clients fall back to the raw code + bank currency).
    jurisdiction: JurisdictionRead | None = None
    # The resolved institution-type registry row — carries the derived
    # institution_class, return family, exposure limits and default module set
    # later SDI phases branch on. None only if the code has no registry row.
    institution_type_detail: InstitutionTypeRead | None = None
    created_at: datetime
    updated_at: datetime


class BankListRead(ClosedModel):
    banks: list[BankRead]


class BankReportingPeriodRead(ClosedModel):
    id: UUID
    bank_id: str
    period_start: date
    period_end: date
    label: str
    status: BankReportingPeriodStatus


class BankReportingPeriodListRead(ClosedModel):
    bank_id: str
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
    cash_flows: list[BankFactRead]
    capital_components: list[BankFactRead]
    deposit_behavior: list[BankFactRead]
