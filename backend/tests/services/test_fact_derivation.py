"""Canonical → BankFinancialFact derivation on the compact canonical fixture."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalFxRate,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    IngestionBatch,
    LineageRecord,
)
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
    EXPECTED_POST_HEDGE_USD_NET,
    EXPECTED_SECURITIES_BILLS,
    EXPECTED_SECURITIES_BONDS,
    FIXTURE_AS_OF,
    HEDGE_USD_SOLD,
    SWAP_NOTIONAL_GHS,
    seed_canonical_fixture,
    seed_directional_swap_positions,
    seed_hedge_and_swap_positions,
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


# The exact attribute payloads the FX and IRR services read from seed-shaped
# hedge and swap facts, plus the provenance keys every derived fact carries.
SEED_FX_HEDGE_KEYS = {
    "hedge_id",
    "instrument",
    "pair",
    "notional_ccy",
    "rate",
    "maturity_days",
    "mtm_ghs",
    "prospective_r2",
    "dollar_offset_ratio",
}
SEED_IRR_SWAP_KEYS = {
    "notional",
    "pay_rate_pct",
    "receive_index",
    "tenor_years",
    "direction",
    "receive_bucket",
    "receive_midpoint_years",
    "pay_bucket",
    "pay_midpoint_years",
}
PROVENANCE_KEYS = {"source", "derived_from"}
FIXTURE_TIER1 = Decimal("35000000")  # 40M CET1 share capital - 5M goodwill
AGGREGATE_LIMIT_PCT = Decimal("20")
SINGLE_LIMIT_PCT = Decimal("10")


def _prepare_hedged(db_session: Session) -> DerivationResult:
    seed_sample_bank(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    seed_hedge_and_swap_positions(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    return result


def test_fx_hedge_facts_are_seed_shaped(db_session: Session) -> None:
    result = _prepare_hedged(db_session)

    statuses = {group.group: group.status for group in result.groups}
    assert statuses["fx_hedge"] == "derived"
    grouped = _by_group(_facts(db_session, result))
    hedges = grouped["fx_hedge"]
    assert set(hedges) == {"FXH-T-001", "FXH-T-002"}

    forward = hedges["FXH-T-001"]
    assert set(forward.attributes) == SEED_FX_HEDGE_KEYS | PROVENANCE_KEYS
    assert forward.amount == Decimal("250000")  # amount carries the MtM, like the seed
    assert forward.attributes["instrument"] == "forward"
    assert forward.attributes["pair"] == "USD/GHS"
    assert forward.attributes["notional_ccy"] == "600000.0000"
    assert forward.attributes["rate"] == "13.0"
    assert forward.attributes["maturity_days"] == "90"
    assert forward.attributes["prospective_r2"] == "0.94"
    assert forward.attributes["dollar_offset_ratio"] == "1.02"

    option = hedges["FXH-T-002"]
    assert option.amount == Decimal("-20000")
    assert option.attributes["instrument"] == "option"
    assert option.attributes["prospective_r2"] == "0.72"  # fails the IFRS 9 screen


def test_irr_swap_facts_are_seed_shaped(db_session: Session) -> None:
    result = _prepare_hedged(db_session)

    statuses = {group.group: group.status for group in result.groups}
    assert statuses["irr_swap"] == "derived"
    grouped = _by_group(_facts(db_session, result))
    swaps = grouped["irr_swap"]
    assert set(swaps) == {"IRS-T-001"}

    swap = swaps["IRS-T-001"]
    assert set(swap.attributes) == SEED_IRR_SWAP_KEYS | PROVENANCE_KEYS
    assert swap.amount == SWAP_NOTIONAL_GHS
    assert swap.attributes["notional"] == "20000000.0000"
    assert swap.attributes["pay_rate_pct"] == "25.3"
    assert swap.attributes["receive_index"] == "91d_tbill"
    assert swap.attributes["direction"] == "pay_fixed"
    assert swap.attributes["tenor_years"] == "3"
    # Leg placement: floating receive at the 91-day reset, fixed pay at the
    # remaining maturity — midpoints are canonical bucket midpoints, so the
    # parameter-table discount curve keys match.
    assert swap.attributes["receive_bucket"] == "1-3m"
    assert swap.attributes["receive_midpoint_years"] == "0.17"
    assert swap.attributes["pay_bucket"] == "1-3y"
    assert swap.attributes["pay_midpoint_years"] == "1.9"


def test_receive_fixed_swap_derives_and_unknown_direction_warns(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    seed_directional_swap_positions(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()

    group = next(item for item in result.groups if item.group == "irr_swap")
    assert group.status == "derived"
    assert group.rows == 1
    # The receive-fixed swap flows through without a warning; only the
    # unknown-direction swap is skipped.
    assert not any("IRS-T-002" in warning for warning in group.warnings)
    assert any("IRS-T-003" in warning and "'basis_swap'" in warning for warning in group.warnings)

    grouped = _by_group(_facts(db_session, result))
    swaps = grouped["irr_swap"]
    assert set(swaps) == {"IRS-T-002"}

    swap = swaps["IRS-T-002"]
    assert set(swap.attributes) == SEED_IRR_SWAP_KEYS | PROVENANCE_KEYS
    assert swap.attributes["direction"] == "receive_fixed"
    assert swap.attributes["pay_rate_pct"] == "25.3"  # the swap's fixed rate
    # Legs invert versus a pay-fixed swap: the fixed leg is RECEIVED at the
    # remaining maturity (1095 days -> 1-3y) and the floating 91d T-bill leg
    # is PAID at its index-reset bucket (91 days -> 1-3m).
    assert swap.attributes["receive_bucket"] == "1-3y"
    assert swap.attributes["receive_midpoint_years"] == "1.9"
    assert swap.attributes["pay_bucket"] == "1-3m"
    assert swap.attributes["pay_midpoint_years"] == "0.17"


def test_hedges_bring_breaching_nop_under_the_limits(db_session: Session) -> None:
    # Raw book: +10.28M GHS USD long vs 35M Tier 1 = 29.4% — breaches both the
    # 20% aggregate and 10% single-currency limits. The hedge book sells 700k
    # USD (8.995M GHS at 12.85), landing the net at +1.285M = 3.7% (compliant).
    raw_pct = EXPECTED_FX_NET_LONG / FIXTURE_TIER1 * 100
    assert raw_pct > AGGREGATE_LIMIT_PCT
    assert raw_pct > SINGLE_LIMIT_PCT

    result = _prepare_hedged(db_session)
    grouped = _by_group(_facts(db_session, result))

    usd = grouped["fx_position"]["USD"]
    assert usd.amount == EXPECTED_POST_HEDGE_USD_NET
    assert usd.attributes["net_derivatives_ccy"] == f"-{HEDGE_USD_SOLD}.0000"
    assert usd.attributes["net_ccy"] == "100000.0000"
    assert usd.attributes["side"] == "long"

    market = grouped["market_risk"]
    assert market["net_long_fx"].amount == EXPECTED_POST_HEDGE_USD_NET
    assert market["net_short_fx"].amount == Decimal("0")

    post_pct = EXPECTED_POST_HEDGE_USD_NET / FIXTURE_TIER1 * 100
    assert post_pct < SINGLE_LIMIT_PCT
    assert post_pct < AGGREGATE_LIMIT_PCT


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


# ---------------------------------------------------------------------------
# Canonical market data overrides (vendor-blind consumption, §15)
# ---------------------------------------------------------------------------

# Canonical curve rates deliberately shifted +1% off the reference fixture's
# curve (12m: 0.195 vs 0.185) so the winning source is observable.
_MARKET_CURVE_RATES: dict[int, str] = {
    1: "0.15",
    3: "0.165",
    6: "0.18",
    12: "0.195",
    24: "0.205",
    36: "0.215",
    60: "0.23",
    120: "0.25",
}
_MARKET_FX_DAYS = 40  # >= the 30-observation floor for replacing the history


def _market_meta(db_session: Session) -> dict[str, Any]:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system="BLOOMBERG",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=FIXTURE_AS_OF,
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="market-data-test-fixture",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    return {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": FIXTURE_AS_OF,
        "ingested_at": datetime(2026, 6, 30, 18, 0, tzinfo=UTC),
        "source_system": "BLOOMBERG",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }


def _seed_market_curve(db_session: Session) -> None:
    meta = _market_meta(db_session)
    curve = CanonicalYieldCurve(
        **meta,
        source_reference="BLOOMBERG/GHS_SOVEREIGN_BVAL",
        currency="GHS",
        curve_name="GHS_SOVEREIGN_BVAL",
        curve_type="sovereign",
    )
    db_session.add(curve)
    db_session.flush()
    for tenor_months, rate in _MARKET_CURVE_RATES.items():
        db_session.add(
            CanonicalYieldCurvePoint(
                **meta,
                source_reference=f"BLOOMBERG/GHS_SOVEREIGN_BVAL/{tenor_months}m",
                yield_curve_id=curve.id,
                tenor_months=tenor_months,
                rate=Decimal(rate),
            )
        )
    db_session.flush()


def _seed_market_fx_spots(db_session: Session) -> None:
    """40 daily USD/GHS canonical spots ending at 13.10 on the as-of date."""
    meta = _market_meta(db_session)
    for offset in range(_MARKET_FX_DAYS):
        day = FIXTURE_AS_OF - timedelta(days=_MARKET_FX_DAYS - 1 - offset)
        rate = Decimal("12.71") + Decimal(offset) / 100
        db_session.add(
            CanonicalFxRate(
                **{**meta, "as_of_date": day},
                source_reference=f"BLOOMBERG/USDGHS/{day.isoformat()}",
                base_currency="USD",
                quote_currency="GHS",
                rate_type="spot",
                tenor_months=None,
                rate=rate,
            )
        )
    db_session.flush()


def test_canonical_market_curve_overrides_reference_curve(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    _seed_market_curve(db_session)

    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()

    grouped = _by_group(_facts(db_session, result))
    curve = grouped["ftp_curve_point"]
    assert len(curve) == 8
    one_year = curve["1y"]
    # Canonical 0.195 wins over the reference row's 0.185, with attribution.
    assert Decimal(str(one_year.attributes["base_yield_pct"])) == Decimal("19.5")
    assert one_year.attributes["derived_from"].startswith(
        "canonical GHS market yield curve GHS_SOVEREIGN_BVAL (BLOOMBERG)"
    )
    ten_year = curve["10y"]
    assert Decimal(str(ten_year.attributes["base_yield_pct"])) == Decimal("25")


def test_canonical_fx_spot_and_history_override_reference(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    _seed_market_fx_spots(db_session)

    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()

    grouped = _by_group(_facts(db_session, result))
    usd = grouped["fx_position"]["USD"]
    # The canonical as-of spot (13.10) wins over the reference row's 12.85.
    assert Decimal(str(usd.attributes["spot_ghs"])) == Decimal("13.1")
    history = grouped["fx_return_history"]["USD"]
    # 40 canonical observations replace the 150-row legacy reference history.
    assert len(history.attributes["returns"]) == _MARKET_FX_DAYS - 1
    assert "canonical market data spot history" in history.attributes["derived_from"]


def test_legacy_reference_path_without_canonical_market_data(db_session: Session) -> None:
    result = _prepare(db_session)

    grouped = _by_group(_facts(db_session, result))
    one_year = grouped["ftp_curve_point"]["1y"]
    assert Decimal(str(one_year.attributes["base_yield_pct"])) == Decimal("18.5")
    assert one_year.attributes["derived_from"].startswith("ingested GHS yield curve")
    assert Decimal(str(grouped["fx_position"]["USD"].attributes["spot_ghs"])) == Decimal("12.85")
    history = grouped["fx_return_history"]["USD"]
    assert "fx_rates_historical" in history.attributes["derived_from"]
    assert len(history.attributes["returns"]) == 149
