"""Canonical → BankFinancialFact derivation on the compact canonical fixture."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import BankFinancialFact, BankReportingPeriod
from app.schemas.regulatory_capital import CapitalScenarioBatchCreate
from app.schemas.regulatory_liquidity import LiquidityScenarioBatchCreate
from app.services.fact_derivation import DerivationError, DerivationResult, derive_facts
from app.services.regulatory_capital import run_all_capital_scenarios
from app.services.regulatory_liquidity import run_all_liquidity_scenarios
from app.services.sample_bank_seed import SAMPLE_BANK_ID, seed_sample_bank
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import (
    EXPECTED_CAPITAL_TOTAL,
    EXPECTED_FX_NET_LONG,
    EXPECTED_FX_NET_SHORT,
    EXPECTED_LOANS_GROSS,
    EXPECTED_SECURITIES_BILLS,
    EXPECTED_SECURITIES_BONDS,
    FIXTURE_AS_OF,
    seed_canonical_fixture,
)

EXPECTED_GROUPS = {
    "balance_sheet",
    "loan_exposure",
    "securities",
    "off_balance",
    "lcr_inflow",
    "market_risk",
    "fx_position",
    "fx_return_history",
    "operational_income",
    "capital_component",
    "irr_position",
    "ftp_curve_point",
    "ftp_product",
    "ftp_branch",
    "ftp_nmd",
}
# The nine canonical buckets whose midpoint sits at or inside twelve months.
SHORT_END_BUCKETS = {"overnight", "1-7d", "8-30d", "1-3m", "3-6m", "6-12m"}
LONG_END_BUCKETS = {"1-3y", "3-5y", "5y+"}


def _ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _prepare(db_session: Session) -> DerivationResult:
    seed_sample_bank(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    return result


def _facts(db_session: Session, result: DerivationResult) -> list[BankFinancialFact]:
    return list(
        db_session.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.organization_id == ORG_1,
                BankFinancialFact.bank_id == SAMPLE_BANK_ID,
                BankFinancialFact.reporting_period_id == result.reporting_period_id,
            )
        )
    )


def _by_group(facts: list[BankFinancialFact]) -> dict[str, dict[str, BankFinancialFact]]:
    grouped: dict[str, dict[str, BankFinancialFact]] = {}
    for fact in facts:
        grouped.setdefault(fact.fact_group, {})[fact.category] = fact
    return grouped


def test_derivation_creates_every_group_with_plausible_aggregates(  # noqa: PLR0915
    db_session: Session,
) -> None:
    result = _prepare(db_session)

    assert result.period_created is True
    assert result.period_label == "2026-06"
    assert result.facts_deleted == 0
    derived_groups = {group.group for group in result.groups if group.status == "derived"}
    assert derived_groups >= EXPECTED_GROUPS
    skipped = {group.group: group for group in result.groups if group.status == "skipped"}
    assert set(skipped) == {"fx_hedge", "irr_swap"}
    assert all(group.note for group in skipped.values())

    facts = _facts(db_session, result)
    assert len(facts) == result.facts_created
    grouped = _by_group(facts)

    balance = grouped["balance_sheet"]
    assert balance["loans_gross"].amount == EXPECTED_LOANS_GROSS
    assert balance["capital_total"].amount == EXPECTED_CAPITAL_TOTAL
    assert balance["securities_bog_bills"].amount == EXPECTED_SECURITIES_BILLS
    assert balance["securities_gog_bonds"].amount == EXPECTED_SECURITIES_BONDS
    assert balance["cash_vault"].amount == Decimal("5000000")
    assert balance["bog_required_reserves"].amount == Decimal("8000000")
    assert balance["bog_excess_reserves"].amount == Decimal("4000000")
    # Deposit split: stability assumptions drive the stable share exactly.
    assert balance["retail_deposits_stable"].amount == Decimal("32500000")
    assert balance["retail_deposits_less_stable"].amount == Decimal("23070000")
    assert balance["wholesale_operational"].amount == Decimal("4500000")
    assert balance["wholesale_non_op_sme"].amount == Decimal("10500000")
    assert balance["wholesale_non_op_corporate"].amount == Decimal("10000000")

    # The identity holds exactly after the plug, and the plug was warned about.
    assets = sum(fact.amount for fact in balance.values() if fact.attributes.get("side") == "asset")
    funding = sum(
        fact.amount
        for fact in balance.values()
        if fact.attributes.get("side") in ("liability", "equity")
    )
    assert assets == funding
    assert any("plugged" in warning for warning in result.warnings)

    exposures = grouped["loan_exposure"]
    assert sum(fact.amount for fact in exposures.values()) == EXPECTED_LOANS_GROSS
    assert exposures["past_due_90"].amount == Decimal("3000000")
    assert exposures["past_due_90"].risk_weight_code == "RW150"
    assert exposures["corporate_unrated"].amount == Decimal("52850000")
    assert exposures["residential_mortgage"].risk_weight_code == "RW35"

    securities = grouped["securities"]
    assert securities["cash_vault_hqla"].attributes["source"] == "cash"
    assert securities["bog_excess_reserves_hqla"].amount == Decimal("4000000")
    assert all(fact.hqla_level == "L1" for fact in securities.values())

    off_balance = grouped["off_balance"]
    assert off_balance["committed_corporate"].amount == Decimal("2000000")
    assert off_balance["committed_corporate"].ccf_pct == Decimal("20")

    inflows = grouped["lcr_inflow"]
    assert inflows["retail_loan_repayments"].amount == Decimal("8000000")
    assert inflows["corporate_sme_repayments"].amount == Decimal("0")
    assert inflows["interbank_maturing"].amount == Decimal("5000000")

    market = grouped["market_risk"]
    assert market["net_long_fx"].amount == EXPECTED_FX_NET_LONG
    assert market["net_short_fx"].amount == EXPECTED_FX_NET_SHORT
    fx = grouped["fx_position"]
    assert fx["USD"].amount == EXPECTED_FX_NET_LONG
    assert fx["USD"].attributes["spot_ghs"] == "12.85"
    returns = grouped["fx_return_history"]["USD"].attributes["returns"]
    assert len(returns) == 149  # 150 spots -> 149 daily returns

    income = grouped["operational_income"]
    assert len(income) == 3
    assert all(fact.amount == Decimal("30000000") for fact in income.values())
    assert {fact.income_year for fact in income.values()} == {2024, 2025, 2026}

    capital = grouped["capital_component"]
    assert capital["regulatory_adj_goodwill"].is_deduction is True
    assert capital["regulatory_adj_goodwill"].amount == Decimal("5000000")
    assert capital["tier2_subordinated_debt"].capital_tier == "T2"

    # IRR buckets cover both the short end (<=12m) and the long end (>1y).
    irr_buckets = {fact.attributes["bucket"] for fact in grouped["irr_position"].values()}
    assert irr_buckets & SHORT_END_BUCKETS
    assert irr_buckets & LONG_END_BUCKETS
    for fact in grouped["irr_position"].values():
        assert fact.attributes["side"] in ("asset", "liability")
        assert fact.attributes["fixed_or_float"] in ("fixed", "float")
        assert Decimal(str(fact.attributes["midpoint_years"])) > 0

    # FTP: the curve prices every product within the engine's alignment tolerance.
    curve = grouped["ftp_curve_point"]
    assert len(curve) == 8
    products = grouped["ftp_product"]
    assert products["gov_securities"].amount == Decimal("35000000")
    branches = grouped["ftp_branch"]
    assert set(branches) == {"head_office", "osu"}
    nmd = grouped["ftp_nmd"]
    assert Decimal(str(nmd["current_accounts"].attributes["core_pct"])) == Decimal("70")

    # Every derived fact carries provenance.
    for fact in facts:
        assert fact.attributes.get("derived_from")
        source = fact.attributes.get("source")
        assert source == "data_engine" or (
            source == "cash" and fact.attributes.get("derived_by") == "data_engine"
        )


def test_rederivation_is_idempotent_and_replaces_facts(db_session: Session) -> None:
    first = _prepare(db_session)
    first_ids = {fact.id for fact in _facts(db_session, first)}

    second = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()

    assert second.period_created is False
    assert second.reporting_period_id == first.reporting_period_id
    assert second.facts_deleted == first.facts_created
    assert second.facts_created == first.facts_created
    second_facts = _facts(db_session, second)
    assert len(second_facts) == first.facts_created
    assert first_ids.isdisjoint({fact.id for fact in second_facts})

    periods = list(
        db_session.scalars(
            select(BankReportingPeriod).where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
                BankReportingPeriod.period_end == FIXTURE_AS_OF,
            )
        )
    )
    assert len(periods) == 1


def test_liquidity_and_capital_engines_succeed_on_derived_facts(db_session: Session) -> None:
    result = _prepare(db_session)
    ctx = _ctx()

    liquidity = run_all_liquidity_scenarios(
        db_session,
        ctx,
        SAMPLE_BANK_ID,
        LiquidityScenarioBatchCreate(reporting_period_id=result.reporting_period_id),
    )
    assert [run.status for run in liquidity.runs] == ["succeeded"] * 4
    baseline = liquidity.runs[0]
    assert Decimal(str(baseline.metrics["lcr_pct"])) > Decimal("100")
    assert Decimal(str(baseline.metrics["nsfr_pct"])) > Decimal("100")

    capital = run_all_capital_scenarios(
        db_session,
        ctx,
        SAMPLE_BANK_ID,
        CapitalScenarioBatchCreate(reporting_period_id=result.reporting_period_id),
    )
    assert [run.status for run in capital.runs] == ["succeeded"] * 4
    assert Decimal(str(capital.runs[0].metrics["car_pct"])) > Decimal("10")


def test_derivation_requires_canonical_data(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)

    with pytest.raises(DerivationError) as excinfo:
        derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, date(2031, 1, 31))
    assert excinfo.value.code == "no_canonical_data"
