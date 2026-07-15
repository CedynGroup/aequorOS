"""Deterministic Sample Bank Ltd regulatory seed for the demo organization.

Seeds one bank, twelve monthly reporting periods (April 2025 through March
2026), tie-out validated financial facts per period, and the Bank of Ghana CRD
baseline parameter tables. The seed is idempotent: existing Sample Bank rows
are deleted by fixed UUID before re-insertion.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    Organization,
    ParamCapitalThreshold,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
    User,
)
from app.models.regulatory import RegulatoryParameterMixin

DEMO_ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
DEMO_ORG_NAME = "AequorOS Demo Organization"
DEMO_USER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEMO_USER_EMAIL = "demo.user.one@example.test"
DEMO_USER_NAME = "Demo User One"
ISOLATED_ORG_ID = UUID("22222222-2222-4222-8222-222222222222")
ISOLATED_ORG_NAME = "AequorOS Isolated Tenant"
SAMPLE_BANK_ID = UUID("77000000-0000-4000-8000-000000000001")

CURRENCY = "GHS"
JURISDICTION_CODE = "GH"
APPROVED_BY = "Bank of Ghana CRD baseline"
APPROVAL_TIMESTAMP = datetime(2025, 1, 1, tzinfo=UTC)
EFFECTIVE_FROM = date(2025, 1, 1)

PERIOD_COUNT = 12
FIRST_PERIOD_YEAR = 2025
FIRST_PERIOD_MONTH = 4

_ONE = Decimal(1)
_ZERO = Decimal(0)
MILLION = Decimal("1000000")
MONEY = Decimal("0.0001")

_SECURITIES_FACTOR_START = Decimal("0.90")
_LOANS_FACTOR_START = Decimal("0.94")
_DEPOSITS_FACTOR_START = Decimal("0.985")
_CAPITAL_FACTOR_START = Decimal("0.96")

# Canonical latest-period amounts, in GHS millions.
_FIXED_ASSETS_M: tuple[tuple[str, str], ...] = (
    ("cash_vault", "45"),
    ("bog_required_reserves", "175"),
    ("bog_excess_reserves", "70"),
)
_DEPOSITS_M: tuple[tuple[str, str], ...] = (
    ("retail_deposits_stable", "700"),
    ("retail_deposits_less_stable", "440"),
    ("wholesale_operational", "240"),
    ("wholesale_non_op_sme", "200"),
    ("wholesale_non_op_corporate", "320"),
)
_SECURED_FUNDING_M = "60"
_TERM_BORROWINGS_M = "100"
_OTHER_ASSETS_FLOOR_M = "40"
_LOAN_EXPOSURES_M: tuple[tuple[str, str, str], ...] = (
    ("corporate_unrated", "560", "RW100"),
    ("sme_retail", "280", "RW75"),
    ("retail_other", "250", "RW75"),
    ("residential_mortgage", "200", "RW35"),
    ("commercial_real_estate", "60", "RW100"),
    ("past_due_90", "50", "RW150"),
)
# (category, amount, sourced-from-cash marker); bog_bills and gog_bonds tie to
# the securities_bog_bills and securities_gog_bonds balance-sheet rows.
_SECURITIES_M: tuple[tuple[str, str, bool], ...] = (
    ("bog_bills", "260", False),
    ("gog_bonds", "360", False),
    ("cash_vault_hqla", "45", True),
    ("bog_excess_reserves_hqla", "70", True),
)
_OFF_BALANCE_M: tuple[tuple[str, str, str, str], ...] = (
    ("committed_retail", "80", "50", "RW75"),
    ("committed_corporate", "240", "50", "RW100"),
)
_LCR_INFLOWS_M: tuple[tuple[str, str, str], ...] = (
    ("retail_loan_repayments", "60", "50"),
    ("corporate_sme_repayments", "90", "50"),
    ("interbank_maturing", "45", "100"),
)
_MARKET_RISK_M: tuple[tuple[str, str], ...] = (
    ("net_long_fx", "45"),
    ("net_short_fx", "12"),
)
_OPERATIONAL_INCOME_M: tuple[tuple[str, str, int], ...] = (
    ("gross_income_2023", "340", 2023),
    ("gross_income_2024", "380", 2024),
    ("gross_income_2025", "400", 2025),
)
_CAPITAL_COMPONENTS_M: tuple[tuple[str, str, str, bool], ...] = (
    ("paid_up_capital", "150", "CET1", False),
    ("retained_earnings", "95", "CET1", False),
    ("statutory_reserves", "45", "CET1", False),
    ("other_reserves", "10", "CET1", False),
    ("intangibles", "25", "CET1", True),
    ("deferred_tax_assets", "15", "CET1", True),
    ("perpetual_instruments", "20", "AT1", False),
    ("subordinated_debt", "45", "T2", False),
    ("general_provisions", "15", "T2", False),
)

_LCR_OUTFLOW_RATES: dict[str, str] = {
    "retail_deposits_stable": "5",
    "retail_deposits_less_stable": "10",
    "wholesale_operational": "25",
    "wholesale_non_op_sme": "40",
    "wholesale_non_op_corporate": "100",
    "secured_funding_l1": "0",
    "term_borrowings_gt_1y": "0",
    "committed_retail": "10",
    "committed_corporate": "30",
}
_LCR_INFLOW_RATES: dict[str, str] = {
    "retail_loan_repayments": "50",
    "corporate_sme_repayments": "50",
    "interbank_maturing": "100",
}
_NSFR_ASF_WEIGHTS: dict[str, str] = {
    "capital_total": "100",
    "retail_deposits_stable": "95",
    "retail_deposits_less_stable": "90",
    "wholesale_operational": "50",
    "wholesale_non_op_sme": "90",
    "wholesale_non_op_corporate": "50",
    "secured_funding_l1": "0",
    "term_borrowings_gt_1y": "100",
}
_NSFR_RSF_WEIGHTS: dict[str, str] = {
    "cash_vault": "0",
    "bog_required_reserves": "0",
    "bog_excess_reserves": "0",
    "securities_bog_bills": "5",
    "securities_gog_bonds": "5",
    "corporate_unrated": "85",
    "sme_retail": "85",
    "retail_other": "85",
    "residential_mortgage": "65",
    "commercial_real_estate": "85",
    "past_due_90": "100",
    "other_assets": "100",
    "off_balance_commitments": "5",
}
_RISK_WEIGHTS: dict[str, str] = {
    "RW0": "0",
    "RW20": "20",
    "RW35": "35",
    "RW50": "50",
    "RW75": "75",
    "RW100": "100",
    "RW150": "150",
}
_CAPITAL_THRESHOLDS: dict[str, str] = {
    "car_min": "10",
    "car_early_warning": "10.5",
    "car_critical": "9",
    "cet1_min": "6.5",
    "tier1_min": "8",
    "leverage_min": "3",
    "lcr_min": "100",
    "lcr_amber_floor": "90",
    "nsfr_min": "100",
    "lcr_inflow_cap_pct": "75",
    "bia_alpha_pct": "15",
    "fx_charge_pct": "8",
    "rwa_multiplier": "1250",
    "tier2_gp_cap_pct_credit_rwa": "1.25",
}
_LIQUIDITY_IDIOSYNCRATIC: dict[str, str] = {
    "runoff:retail_deposits_stable": "15",
    "runoff:retail_deposits_less_stable": "20",
    "runoff:wholesale_operational": "40",
    "runoff:wholesale_non_op_sme": "60",
    "runoff:wholesale_non_op_corporate": "100",
    "runoff:committed_retail": "20",
    "runoff:committed_corporate": "50",
    "inflow_multiplier": "0.75",
    "hqla_securities_haircut_pct": "0",
    "asf:retail_deposits_stable": "90",
    "asf:retail_deposits_less_stable": "80",
    "asf:wholesale_operational": "40",
    "asf:wholesale_non_op_sme": "80",
    "asf:wholesale_non_op_corporate": "40",
}
_LIQUIDITY_MARKET_WIDE: dict[str, str] = {
    "runoff:retail_deposits_stable": "7.5",
    "runoff:retail_deposits_less_stable": "15",
    "runoff:wholesale_operational": "30",
    "runoff:wholesale_non_op_sme": "50",
    "runoff:wholesale_non_op_corporate": "100",
    "runoff:committed_retail": "10",
    "runoff:committed_corporate": "40",
    "inflow_multiplier": "0.90",
    "hqla_securities_haircut_pct": "8",
    "rsf:securities_weight_override": "10",
}
_LIQUIDITY_COMBINED: dict[str, str] = {
    **{
        key: value
        for key, value in _LIQUIDITY_IDIOSYNCRATIC.items()
        if key.startswith(("runoff:", "asf:"))
    },
    "inflow_multiplier": "0.67",
    "hqla_securities_haircut_pct": "8",
    "rsf:securities_weight_override": "10",
}
_STRESS_SHOCKS: dict[str, dict[str, dict[str, str]]] = {
    "liquidity": {
        "idiosyncratic": _LIQUIDITY_IDIOSYNCRATIC,
        "market_wide": _LIQUIDITY_MARKET_WIDE,
        "combined": _LIQUIDITY_COMBINED,
    },
    "capital": {
        "mild": {
            "quarterly_rwa_growth_pct": "1.5",
            "quarterly_income_m": "16",
            "quarterly_credit_loss_m": "1.4",
            "fx_rwa_multiplier": "1.0",
        },
        "moderate": {
            "quarterly_rwa_growth_pct": "2.5",
            "quarterly_income_m": "12",
            "quarterly_credit_loss_m": "6.3",
            "fx_rwa_multiplier": "1.25",
        },
        "severe": {
            "quarterly_rwa_growth_pct": "4.0",
            "quarterly_income_m": "2",
            "quarterly_credit_loss_m": "30.8",
            "fx_rwa_multiplier": "1.6",
        },
    },
    "forecast": {
        "base": {
            "loan_growth_pct": "18",
            "deposit_growth_pct": "16",
            "nim_pct": "4.8",
            "cost_to_income_pct": "48",
            "credit_loss_rate_pct": "1.0",
            "fx_depreciation_pct": "0",
            "dividend_payout_pct": "30",
        },
        "adverse": {
            "loan_growth_pct": "8",
            "deposit_growth_pct": "6",
            "nim_pct": "4.2",
            "cost_to_income_pct": "54",
            "credit_loss_rate_pct": "1.5",
            "fx_depreciation_pct": "15",
            "dividend_payout_pct": "0",
        },
        "severely_adverse": {
            "loan_growth_pct": "-2",
            "deposit_growth_pct": "-8",
            "nim_pct": "3.6",
            "cost_to_income_pct": "60",
            "credit_loss_rate_pct": "2.0",
            "fx_depreciation_pct": "40",
            "dividend_payout_pct": "0",
        },
    },
}
_PARAMETER_MODELS: tuple[type[RegulatoryParameterMixin], ...] = (
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
    ParamCapitalThreshold,
)


class SampleBankSeedError(RuntimeError):
    """Raised when the generated seed data fails a deterministic tie-out check."""


@dataclass(frozen=True)
class SeedSummary:
    bank_id: UUID
    periods: int
    fact_count: int
    param_count: int


@dataclass(frozen=True)
class _PeriodFactors:
    securities: Decimal
    loans: Decimal
    deposits: Decimal
    capital: Decimal


def seed_sample_bank(session: Session) -> SeedSummary:
    """Idempotently seed Sample Bank Ltd for the demo organization."""
    _set_tenant_context(session, DEMO_ORG_ID)
    _ensure_organization(session, DEMO_ORG_ID, DEMO_ORG_NAME)
    _ensure_demo_user(session)
    _delete_existing_seed(session)

    session.add(
        Bank(
            id=SAMPLE_BANK_ID,
            organization_id=DEMO_ORG_ID,
            name="Sample Bank Ltd",
            short_name="Sample Bank",
            currency=CURRENCY,
            jurisdiction_code=JURISDICTION_CODE,
            license_type="universal",
        )
    )
    periods = _build_reporting_periods()
    session.add_all(periods)
    session.flush()

    fact_count = 0
    for index, period in enumerate(periods):
        facts = _build_period_facts(period, index)
        _validate_period_facts(period, facts)
        session.add_all(facts)
        fact_count += len(facts)

    param_count = _seed_parameters(session)
    session.flush()

    _set_tenant_context(session, ISOLATED_ORG_ID)
    _ensure_organization(session, ISOLATED_ORG_ID, ISOLATED_ORG_NAME)
    _set_tenant_context(session, DEMO_ORG_ID)

    return SeedSummary(
        bank_id=SAMPLE_BANK_ID,
        periods=len(periods),
        fact_count=fact_count,
        param_count=param_count,
    )


def _set_tenant_context(session: Session, organization_id: UUID) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        sql_text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": str(organization_id)},
    )


def _ensure_organization(session: Session, organization_id: UUID, name: str) -> None:
    exists = session.scalar(select(Organization.id).where(Organization.id == organization_id))
    if exists is None:
        session.add(Organization(id=organization_id, name=name))
        session.flush()


def _ensure_demo_user(session: Session) -> None:
    exists = session.scalar(
        select(User.id).where(User.id == DEMO_USER_ID, User.organization_id == DEMO_ORG_ID)
    )
    if exists is None:
        session.add(
            User(
                id=DEMO_USER_ID,
                organization_id=DEMO_ORG_ID,
                email=DEMO_USER_EMAIL,
                display_name=DEMO_USER_NAME,
                is_active=True,
            )
        )
        session.flush()


def _delete_existing_seed(session: Session) -> None:
    session.execute(
        delete(BankFinancialFact).where(
            BankFinancialFact.bank_id == SAMPLE_BANK_ID,
            BankFinancialFact.organization_id == DEMO_ORG_ID,
        )
    )
    session.execute(
        delete(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
        )
    )
    session.execute(
        delete(Bank).where(Bank.id == SAMPLE_BANK_ID, Bank.organization_id == DEMO_ORG_ID)
    )
    for model in _PARAMETER_MODELS:
        session.execute(
            delete(model).where(
                model.organization_id == DEMO_ORG_ID,
                model.jurisdiction_code == JURISDICTION_CODE,
                model.approved_by == APPROVED_BY,
            )
        )


def _build_reporting_periods() -> list[BankReportingPeriod]:
    periods: list[BankReportingPeriod] = []
    for index in range(PERIOD_COUNT):
        year, month = _period_month(index)
        periods.append(
            BankReportingPeriod(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                period_start=date(year, month, 1),
                period_end=date(year, month, monthrange(year, month)[1]),
                label=f"{year:04d}-{month:02d}",
                status="open" if index == PERIOD_COUNT - 1 else "closed",
            )
        )
    return periods


def _period_month(index: int) -> tuple[int, int]:
    month_ordinal = FIRST_PERIOD_MONTH - 1 + index
    return FIRST_PERIOD_YEAR + month_ordinal // 12, month_ordinal % 12 + 1


def _factors(index: int) -> _PeriodFactors:
    return _PeriodFactors(
        securities=_factor(_SECURITIES_FACTOR_START, index),
        loans=_factor(_LOANS_FACTOR_START, index),
        deposits=_factor(_DEPOSITS_FACTOR_START, index),
        capital=_factor(_CAPITAL_FACTOR_START, index),
    )


def _factor(start: Decimal, index: int) -> Decimal:
    return start + (_ONE - start) * Decimal(index) / Decimal(PERIOD_COUNT - 1)


def _amount(millions: str, factor: Decimal = _ONE) -> Decimal:
    return (Decimal(millions) * MILLION * factor).quantize(MONEY)


def _total(amounts: Iterable[Decimal]) -> Decimal:
    return sum(amounts, _ZERO)


def _fact(
    period: BankReportingPeriod,
    fact_group: str,
    category: str,
    amount: Decimal,
    **extra: Any,
) -> BankFinancialFact:
    return BankFinancialFact(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        reporting_period_id=period.id,
        fact_group=fact_group,
        category=category,
        amount=amount,
        currency=CURRENCY,
        **extra,
    )


def _build_period_facts(period: BankReportingPeriod, index: int) -> list[BankFinancialFact]:
    factors = _factors(index)
    loan_rows = [
        (category, _amount(millions, factors.loans), code)
        for category, millions, code in _LOAN_EXPOSURES_M
    ]
    securities_rows = [
        (category, _amount(millions, factors.securities), from_cash)
        for category, millions, from_cash in _SECURITIES_M
    ]
    capital_rows = [
        (category, _amount(millions, factors.capital), tier, deduction)
        for category, millions, tier, deduction in _CAPITAL_COMPONENTS_M
    ]
    capital_total = _total(
        amount if not deduction else -amount for _, amount, _, deduction in capital_rows
    )

    facts = _balance_sheet_facts(
        period,
        factors,
        loans_gross=_total(amount for _, amount, _ in loan_rows),
        securities_amounts=(securities_rows[0][1], securities_rows[1][1]),
        capital_total=capital_total,
    )
    facts.extend(
        _fact(period, "loan_exposure", category, amount, risk_weight_code=code)
        for category, amount, code in loan_rows
    )
    facts.extend(
        _fact(
            period,
            "securities",
            category,
            amount,
            hqla_level="L1",
            risk_weight_code="RW0",
            attributes={"source": "cash"} if from_cash else {},
        )
        for category, amount, from_cash in securities_rows
    )
    facts.extend(
        _fact(
            period,
            "off_balance",
            category,
            _amount(millions, factors.loans),
            ccf_pct=Decimal(ccf),
            risk_weight_code=code,
        )
        for category, millions, ccf, code in _OFF_BALANCE_M
    )
    facts.extend(
        _fact(
            period, "lcr_inflow", category, _amount(millions, factors.loans), rate_pct=Decimal(rate)
        )
        for category, millions, rate in _LCR_INFLOWS_M
    )
    facts.extend(
        _fact(period, "market_risk", category, _amount(millions))
        for category, millions in _MARKET_RISK_M
    )
    facts.extend(
        _fact(period, "operational_income", category, _amount(millions), income_year=year)
        for category, millions, year in _OPERATIONAL_INCOME_M
    )
    facts.extend(
        _fact(
            period,
            "capital_component",
            category,
            amount,
            capital_tier=tier,
            is_deduction=deduction,
        )
        for category, amount, tier, deduction in capital_rows
    )
    return facts


def _balance_sheet_facts(
    period: BankReportingPeriod,
    factors: _PeriodFactors,
    loans_gross: Decimal,
    securities_amounts: tuple[Decimal, Decimal],
    capital_total: Decimal,
) -> list[BankFinancialFact]:
    bills, bonds = securities_amounts
    fixed_rows = [(category, _amount(millions)) for category, millions in _FIXED_ASSETS_M]
    deposit_rows = [
        (category, _amount(millions, factors.deposits)) for category, millions in _DEPOSITS_M
    ]
    secured_funding = _amount(_SECURED_FUNDING_M)
    term_borrowings = _amount(_TERM_BORROWINGS_M)
    other_assets_floor = _amount(_OTHER_ASSETS_FLOOR_M)

    assets_before_plug = _total(amount for _, amount in fixed_rows) + bills + bonds + loans_gross
    liabilities_and_equity = (
        _total(amount for _, amount in deposit_rows)
        + secured_funding
        + term_borrowings
        + capital_total
    )
    other_assets = liabilities_and_equity - assets_before_plug
    if other_assets < other_assets_floor:
        term_borrowings += other_assets_floor - other_assets
        other_assets = other_assets_floor

    facts = [
        _fact(period, "balance_sheet", category, amount, attributes=_side("asset"))
        for category, amount in fixed_rows
    ]
    facts.append(
        _fact(period, "balance_sheet", "securities_bog_bills", bills, attributes=_side("asset"))
    )
    facts.append(
        _fact(period, "balance_sheet", "securities_gog_bonds", bonds, attributes=_side("asset"))
    )
    facts.append(
        _fact(period, "balance_sheet", "loans_gross", loans_gross, attributes=_side("asset"))
    )
    facts.append(
        _fact(period, "balance_sheet", "other_assets", other_assets, attributes=_side("asset"))
    )
    facts.extend(
        _fact(period, "balance_sheet", category, amount, attributes=_side("liability"))
        for category, amount in deposit_rows
    )
    facts.append(
        _fact(
            period,
            "balance_sheet",
            "secured_funding_l1",
            secured_funding,
            attributes=_side("liability"),
        )
    )
    facts.append(
        _fact(
            period,
            "balance_sheet",
            "term_borrowings_gt_1y",
            term_borrowings,
            attributes=_side("liability"),
        )
    )
    facts.append(
        _fact(
            period,
            "balance_sheet",
            "capital_total",
            capital_total,
            attributes=_side("equity"),
        )
    )
    return facts


def _side(side: str) -> dict[str, Any]:
    return {"side": side}


def _validate_period_facts(period: BankReportingPeriod, facts: list[BankFinancialFact]) -> None:
    balance = [fact for fact in facts if fact.fact_group == "balance_sheet"]
    assets_total = _total(fact.amount for fact in balance if fact.attributes.get("side") == "asset")
    funding_total = _total(
        fact.amount for fact in balance if fact.attributes.get("side") in ("liability", "equity")
    )
    if assets_total != funding_total:
        raise SampleBankSeedError(
            f"Period {period.label}: assets {assets_total} != liabilities+equity {funding_total}."
        )

    loans_gross = next(fact.amount for fact in balance if fact.category == "loans_gross")
    exposure_total = _total(fact.amount for fact in facts if fact.fact_group == "loan_exposure")
    if exposure_total != loans_gross:
        raise SampleBankSeedError(
            f"Period {period.label}: loan exposures {exposure_total} != loans_gross {loans_gross}."
        )

    securities_balance = _total(
        fact.amount
        for fact in balance
        if fact.category in ("securities_bog_bills", "securities_gog_bonds")
    )
    securities_group = _total(
        fact.amount
        for fact in facts
        if fact.fact_group == "securities" and fact.attributes.get("source") != "cash"
    )
    if securities_group != securities_balance:
        raise SampleBankSeedError(
            f"Period {period.label}: securities facts {securities_group} != "
            f"balance-sheet securities {securities_balance}."
        )


def _parameter_scope() -> dict[str, Any]:
    return {
        "organization_id": DEMO_ORG_ID,
        "jurisdiction_code": JURISDICTION_CODE,
        "effective_from": EFFECTIVE_FROM,
        "effective_to": None,
        "approved_by": APPROVED_BY,
        "approval_timestamp": APPROVAL_TIMESTAMP,
    }


def _seed_parameters(session: Session) -> int:
    scope = _parameter_scope()
    rows: list[Base] = []
    rows.extend(
        ParamLcrRunoffRate(
            flow_direction="outflow", category=category, rate_pct=Decimal(rate), **scope
        )
        for category, rate in _LCR_OUTFLOW_RATES.items()
    )
    rows.extend(
        ParamLcrRunoffRate(
            flow_direction="inflow", category=category, rate_pct=Decimal(rate), **scope
        )
        for category, rate in _LCR_INFLOW_RATES.items()
    )
    rows.extend(
        ParamNsfrWeight(side="asf", category=category, weight_pct=Decimal(weight), **scope)
        for category, weight in _NSFR_ASF_WEIGHTS.items()
    )
    rows.extend(
        ParamNsfrWeight(side="rsf", category=category, weight_pct=Decimal(weight), **scope)
        for category, weight in _NSFR_RSF_WEIGHTS.items()
    )
    rows.extend(
        ParamRiskWeight(risk_weight_code=code, weight_pct=Decimal(weight), **scope)
        for code, weight in _RISK_WEIGHTS.items()
    )
    rows.extend(
        ParamCapitalThreshold(threshold_code=code, value_pct=Decimal(value), **scope)
        for code, value in _CAPITAL_THRESHOLDS.items()
    )
    for module, scenarios in _STRESS_SHOCKS.items():
        for scenario_code, shocks in scenarios.items():
            rows.extend(
                ParamStressShock(
                    module=module,
                    scenario_code=scenario_code,
                    shock_key=shock_key,
                    shock_value=Decimal(value),
                    **scope,
                )
                for shock_key, value in shocks.items()
            )
    session.add_all(rows)
    return len(rows)
