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
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalFxRate,
    CanonicalGlAccount,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    IngestionBatch,
    LineageRecord,
)
from app.schemas.regulatory_capital import CapitalScenarioBatchCreate
from app.schemas.regulatory_liquidity import LiquidityScenarioBatchCreate
from app.services.fact_derivation import (
    DerivationError,
    DerivationResult,
    _Canonical,
    _central_bank_names,
    _CentralBankNames,
    _classify_gl_assets,
    _derive_provision_held,
    _GlCoverage,
    _LoanRow,
    _PositionRow,
    derive_facts,
)
from app.services.regulatory_capital import run_all_capital_scenarios
from app.services.regulatory_liquidity import run_all_liquidity_scenarios
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
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

EXPECTED_GROUPS = {
    "balance_sheet",
    "loan_exposure",
    "provision_held",
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
    materialize_canonical_test_book(db_session)
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
    # Groups the bare canonical fixture legitimately cannot feed (each is
    # skipped with an explanatory note, never silently absent):
    #   fx_hedge  — no FX hedge positions in the base fixture (overlaid only by
    #               seed_hedge_and_swap_positions; see _prepare_hedged below);
    #   irr_swap  — no interest-rate swap positions in the base fixture (same
    #               overlay);
    #   cashflow  — the trailing-90-day actual cash-flow summary reads canonical
    #               ``historical_cashflows`` reference rows (ETL-only), which
    #               the fixture never ingests;
    #   crm_collateral — no LOAN carries the crm_collateral_*/crm_guarantee_*
    #               attributes, so no credit-risk mitigation is recognised. This
    #               entry is NEW (enterprise audit 2026-08-20): the empty case
    #               used to append no group at all, so "no CRM was recognised"
    #               and "CRM was not applicable" were indistinguishable. No fact
    #               value changes — only the visibility of the absence.
    assert set(skipped) == {"fx_hedge", "irr_swap", "cashflow", "crm_collateral"}
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

    # operational_income emits one trailing-12-month fact per (metric, year) for
    # every metric ALL twelve months carry: the fixture's monthly rows hold net
    # interest (2M) + fees (0.5M) only, so gross_income (30M) and
    # net_interest_income (24M) appear for each of the three years, while
    # net_income / operating_expenses / provisions are absent, not zero-filled.
    income = grouped["operational_income"]
    assert len(income) == 6
    assert {fact.income_year for fact in income.values()} == {2024, 2025, 2026}
    for year in (2024, 2025, 2026):
        assert income[f"gross_income_{year}"].amount == Decimal("30000000")
        assert income[f"net_interest_income_{year}"].amount == Decimal("24000000")
    assert not {c for c in income if not c.startswith(("gross_income_", "net_interest_income_"))}

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
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    seed_hedge_and_swap_positions(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    return result


def test_provision_held_splits_specific_from_general_by_classification(
    db_session: Session,
) -> None:
    """The fixture book's stated provisions land as provision_held facts.

    One stage-3 loan states 900,000; six stage-1 loans state 660,000 between
    them (a stated "0" is stated — absent is the different case). No loan
    states interest in suspense, so that category must NOT appear: an absent
    attribute never becomes a zero row.
    """
    result = _prepare(db_session)
    grouped = _by_group(_facts(db_session, result))
    held = grouped["provision_held"]
    assert set(held) == {"specific", "general"}
    assert held["specific"].amount == Decimal("900000")
    assert held["general"].amount == Decimal("660000")


def _loan_row(
    ref: str,
    balance_ghs: str,
    *,
    stage: int | None = None,
    attributes: dict[str, Any] | None = None,
) -> _LoanRow:
    row = _PositionRow(
        source_reference=ref,
        source_system="EXCEL_CSV",
        position_type="LOAN",
        currency="GHS",
        balance=Decimal(balance_ghs),
        balance_ghs=Decimal(balance_ghs),
        interest_rate=None,
        rate_type=None,
        contractual_maturity=None,
        next_repricing_date=None,
        ifrs9_stage=stage,
        product_code=None,
        regulatory_category=None,
        counterparty_type=None,
        branch_id=None,
        ecl_ghs=Decimal("0"),
        notional_ghs=Decimal("0"),
        ccf=None,
        attributes=attributes or {},
    )
    return _LoanRow(row=row, category="retail_other", risk_weight_code="RW75")


def test_provision_held_absent_book_derives_nothing_and_reports_skipped() -> None:
    """A book where no loan states a provision yields NO facts + a skipped group
    naming the consequence — coverage is unavailable, never a fabricated 0%."""
    groups: list[Any] = []
    specs = _derive_provision_held(
        [_loan_row("L1", "1000", stage=1), _loan_row("L2", "2000", stage=3)], groups
    )
    assert specs == []
    assert groups[0].group == "provision_held"
    assert groups[0].status == "skipped"
    assert "never a fabricated zero" in (groups[0].note or "")


def test_provision_held_stated_classification_outranks_the_stage_proxy() -> None:
    """An ingested ``bog_classification`` decides the specific/general split even
    when the IFRS 9 stage disagrees; the stage is only the fallback."""
    groups: list[Any] = []
    specs = _derive_provision_held(
        [
            # Stage 1 but classified Sub-standard by the bank: specific.
            _loan_row(
                "L1",
                "1000",
                stage=1,
                attributes={"ecl_provision_ghs": "200", "bog_classification": "Sub-standard"},
            ),
            # Stage 3 but classified OLEM (watch, not NPL): general.
            _loan_row(
                "L2",
                "2000",
                stage=3,
                attributes={"ecl_provision_ghs": "50", "bog_classification": "OLEM"},
            ),
            # No classification stated: the stage-3 proxy makes it specific.
            _loan_row("L3", "3000", stage=3, attributes={"ecl_provision_ghs": "70"}),
            _loan_row("L4", "500", stage=1, attributes={"interest_in_suspense_ghs": "9"}),
        ],
        groups,
    )
    by_category = {spec.category: spec.amount for spec in specs}
    assert by_category == {
        "specific": Decimal("270"),
        "general": Decimal("50"),
        "interest_in_suspense": Decimal("9"),
    }
    assert groups[0].status == "derived"


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
    materialize_canonical_test_book(db_session)
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
    assert [run.status for run in liquidity.runs] == ["succeeded"] * 5  # + usd stress
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
    materialize_canonical_test_book(db_session)
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
    materialize_canonical_test_book(db_session)
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
    materialize_canonical_test_book(db_session)
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


# ---------------------------------------------------------------------------
# NEW-40: central-bank balances are identified from the jurisdiction registry
# ---------------------------------------------------------------------------
#
# The classifier used to test for the literal token ``"bog"`` in the account
# name. The SDI's chart spells its central bank out — ``GL-1020 "Balances with
# Bank of Ghana"`` — so 44.7m of settlement money fell through to
# ``other_assets`` and never reached high-quality liquid assets. A literal is
# also the wrong shape: it can never match a Nigerian or Kenyan tenant naming
# its own central bank.


def _gl(code: str, name: str, balance: str, account_class: str = "ASSET") -> CanonicalGlAccount:
    return CanonicalGlAccount(
        account_code=code, name=name, account_class=account_class, balance=Decimal(balance)
    )


def _gl_canonical(
    accounts: list[CanonicalGlAccount], names: _CentralBankNames
) -> _Canonical:
    return _Canonical(
        as_of=FIXTURE_AS_OF,
        base_currency="GHS",
        positions=[],
        gl_accounts=accounts,
        refs={},
        central_bank_names=names,
    )


def _classify(
    accounts: list[CanonicalGlAccount],
    names: _CentralBankNames,
    coverage: _GlCoverage | None = None,
) -> tuple[dict[str, Decimal], Decimal]:
    return _classify_gl_assets(
        _gl_canonical(accounts, names),
        coverage or _GlCoverage(False, False, False, False, False),
        [],
    )


def test_central_bank_balance_named_in_full_is_not_other_assets(db_session: Session) -> None:
    """The exact primary-database defect: ``GL-1020 Balances with Bank of Ghana``."""
    names = _CentralBankNames.from_registry("Bank of Ghana", "BoG")
    cash, other = _classify(
        [
            _gl("GL-1010", "Cash on hand (vault)", "20187020.89"),
            _gl("GL-1020", "Balances with Bank of Ghana", "44696174.83"),
            _gl("GL-1900", "Other assets", "51485014.07"),
        ],
        names,
    )
    assert cash["bog_excess_reserves"] == Decimal("44696174.83")
    assert other == Decimal("51485014.07")


def test_the_central_bank_test_is_the_bank_s_own_registry_row_not_a_country(
    db_session: Session,
) -> None:
    """A Nigerian tenant's chart names the Central Bank of Nigeria; a Kenyan
    tenant's names the Central Bank of Kenya. Neither contains ``bog``."""
    nigeria = _CentralBankNames.from_registry("Central Bank of Nigeria", "CBN")
    cash, other = _classify(
        [_gl("A/1", "Balances with Central Bank of Nigeria", "1000")], nigeria
    )
    assert cash["bog_excess_reserves"] == Decimal("1000")
    assert other == Decimal("0")

    # A registry row with no generic "central bank" phrase at all still resolves.
    south_africa = _CentralBankNames.from_registry("South African Reserve Bank", "SARB")
    cash, _ = _classify([_gl("A/1", "Deposits at South African Reserve Bank", "500")], south_africa)
    assert cash["bog_excess_reserves"] == Decimal("500")


def test_sovereign_paper_is_never_swept_into_the_central_bank_line(
    db_session: Session,
) -> None:
    """The country name is deliberately NOT a central-bank token: government
    paper is an issuer, not a settlement balance. Central-bank BILLS are
    securities too — the ``bill`` guard survives."""
    names = _CentralBankNames.from_registry("Bank of Ghana", "BoG")
    coverage = _GlCoverage(
        securities=True,
        loans=False,
        interbank_placements=False,
        deposits=False,
        interbank_borrowings=False,
    )
    cash, other = _classify(
        [
            _gl("1204", "Government of Ghana Bonds (2y)", "6000000"),
            _gl("1210", "Bank of Ghana bills (56d)", "3000000"),
        ],
        names,
        coverage,
    )
    assert cash["bog_excess_reserves"] == Decimal("0")
    # Both are covered by the SECURITY_HOLDING sub-ledger, so neither is a residual.
    assert other == Decimal("0")


def test_the_short_regulator_form_matches_a_word_not_a_substring(
    db_session: Session,
) -> None:
    """``BoG`` is three letters. Matching it as a substring would classify any
    account whose name happens to contain those letters."""
    names = _CentralBankNames.from_registry("Bank of Ghana", "BoG")
    assert names.matches("balances with bog") is True
    assert names.matches("bog - settlement account") is True
    assert names.matches("bogota branch receivable") is False


def test_registry_supplies_the_names_and_the_country_is_not_among_them(
    db_session: Session,
) -> None:
    """Resolution is from the bank's own jurisdiction row — no country literal."""
    materialize_canonical_test_book(db_session)
    db_session.flush()
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    names = _central_bank_names(db_session, bank)
    assert names.full == ("bank of ghana",)
    assert names.short == ("bog",)
    # The country name would match "Government of Ghana ..." and must not be used.
    assert "ghana" not in names.short
    assert names.matches("government of ghana bonds (2y)") is False
