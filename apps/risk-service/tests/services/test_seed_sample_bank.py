from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    ParamCapitalThreshold,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
)
from app.services.params import get_active_params
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    ISOLATED_ORG_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)

EXPECTED_PERIODS = 12
EXPECTED_FACTS = 528
EXPECTED_PARAMS = 126
OTHER_ASSETS_FLOOR = Decimal("40000000")


def _count(db_session: Session, model: type) -> int:
    return db_session.scalar(select(func.count()).select_from(model)) or 0


def _period_facts(db_session: Session, period_id: UUID) -> list[BankFinancialFact]:
    return list(
        db_session.scalars(
            select(BankFinancialFact).where(BankFinancialFact.reporting_period_id == period_id)
        )
    )


def _sum(amounts: Iterable[Decimal]) -> Decimal:
    return sum(amounts, Decimal(0))


def test_seed_creates_bank_periods_facts_and_params(db_session: Session) -> None:
    summary = seed_sample_bank(db_session)
    db_session.commit()

    assert summary.bank_id == SAMPLE_BANK_ID
    assert summary.periods == EXPECTED_PERIODS
    assert summary.fact_count == EXPECTED_FACTS
    assert summary.param_count == EXPECTED_PARAMS

    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    assert bank.organization_id == DEMO_ORG_ID
    assert bank.name == "Sample Bank Ltd"
    assert bank.currency == "GHS"
    assert bank.jurisdiction_code == "GH"
    assert bank.license_type == "universal"

    periods = list(
        db_session.scalars(
            select(BankReportingPeriod)
            .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
            .order_by(BankReportingPeriod.period_end)
        )
    )
    assert [period.label for period in periods] == [
        "2025-04",
        "2025-05",
        "2025-06",
        "2025-07",
        "2025-08",
        "2025-09",
        "2025-10",
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
        "2026-03",
    ]
    assert periods[0].period_start == date(2025, 4, 1)
    assert periods[0].period_end == date(2025, 4, 30)
    assert periods[-1].period_end == date(2026, 3, 31)
    assert [period.status for period in periods] == ["closed"] * 11 + ["open"]

    assert _count(db_session, BankFinancialFact) == EXPECTED_FACTS


def test_every_period_ties_out(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.commit()

    periods = list(
        db_session.scalars(
            select(BankReportingPeriod).where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
        )
    )
    assert len(periods) == EXPECTED_PERIODS
    for period in periods:
        facts = _period_facts(db_session, period.id)
        balance = [fact for fact in facts if fact.fact_group == "balance_sheet"]

        assets = _sum(fact.amount for fact in balance if fact.attributes.get("side") == "asset")
        funding = _sum(
            fact.amount
            for fact in balance
            if fact.attributes.get("side") in ("liability", "equity")
        )
        assert assets == funding, period.label

        loans_gross = next(fact.amount for fact in balance if fact.category == "loans_gross")
        exposures = _sum(fact.amount for fact in facts if fact.fact_group == "loan_exposure")
        assert exposures == loans_gross, period.label

        securities_balance = _sum(
            fact.amount
            for fact in balance
            if fact.category in ("securities_bog_bills", "securities_gog_bonds")
        )
        securities_group = _sum(
            fact.amount
            for fact in facts
            if fact.fact_group == "securities" and fact.attributes.get("source") != "cash"
        )
        assert securities_group == securities_balance, period.label

        other_assets = next(fact.amount for fact in balance if fact.category == "other_assets")
        assert other_assets >= OTHER_ASSETS_FLOOR, period.label


def test_latest_period_matches_canonical_table(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.commit()

    latest = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == date(2026, 3, 31),
        )
    )
    assert latest is not None
    assert latest.status == "open"

    facts = _period_facts(db_session, latest.id)
    by_key = {(fact.fact_group, fact.category): fact for fact in facts}

    assert by_key[("balance_sheet", "loans_gross")].amount == Decimal("1400000000")
    assert by_key[("balance_sheet", "other_assets")].amount == Decimal("90000000")
    assert by_key[("balance_sheet", "capital_total")].amount == Decimal("340000000")
    assert by_key[("balance_sheet", "capital_total")].attributes == {"side": "equity"}
    assert by_key[("balance_sheet", "retail_deposits_stable")].amount == Decimal("700000000")
    assert by_key[("balance_sheet", "term_borrowings_gt_1y")].amount == Decimal("100000000")

    hqla_securities = _sum(
        fact.amount
        for fact in facts
        if fact.fact_group == "securities" and fact.hqla_level is not None
    )
    assert hqla_securities == Decimal("735000000")

    components = [fact for fact in facts if fact.fact_group == "capital_component"]
    net_capital = _sum(
        fact.amount if not fact.is_deduction else -fact.amount for fact in components
    )
    assert net_capital == Decimal("340000000")
    assert by_key[("capital_component", "intangibles")].is_deduction is True
    assert by_key[("capital_component", "perpetual_instruments")].capital_tier == "AT1"

    assert by_key[("loan_exposure", "residential_mortgage")].risk_weight_code == "RW35"
    assert by_key[("loan_exposure", "past_due_90")].risk_weight_code == "RW150"
    assert by_key[("off_balance", "committed_corporate")].amount == Decimal("240000000")
    assert by_key[("off_balance", "committed_corporate")].ccf_pct == Decimal("50")
    assert by_key[("lcr_inflow", "interbank_maturing")].rate_pct == Decimal("100")
    assert by_key[("market_risk", "net_short_fx")].amount == Decimal("12000000")
    assert by_key[("operational_income", "gross_income_2025")].income_year == 2025
    assert by_key[("securities", "cash_vault_hqla")].attributes == {"source": "cash"}


def test_parameter_seed_counts_and_values(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.commit()

    assert _count(db_session, ParamLcrRunoffRate) == 12
    assert _count(db_session, ParamNsfrWeight) == 21
    assert _count(db_session, ParamRiskWeight) == 7
    assert _count(db_session, ParamCapitalThreshold) == 14
    assert _count(db_session, ParamStressShock) == 72

    rwa_multiplier = db_session.scalar(
        select(ParamCapitalThreshold).where(
            ParamCapitalThreshold.threshold_code == "rwa_multiplier"
        )
    )
    assert rwa_multiplier is not None
    assert rwa_multiplier.value_pct == Decimal("1250")
    assert rwa_multiplier.approved_by == "Bank of Ghana CRD baseline"

    combined_inflow = db_session.scalar(
        select(ParamStressShock).where(
            ParamStressShock.module == "liquidity",
            ParamStressShock.scenario_code == "combined",
            ParamStressShock.shock_key == "inflow_multiplier",
        )
    )
    assert combined_inflow is not None
    assert combined_inflow.shock_value == Decimal("0.67")

    combined_runoff = db_session.scalar(
        select(ParamStressShock).where(
            ParamStressShock.module == "liquidity",
            ParamStressShock.scenario_code == "combined",
            ParamStressShock.shock_key == "runoff:retail_deposits_stable",
        )
    )
    assert combined_runoff is not None
    assert combined_runoff.shock_value == Decimal("15")


def test_seed_is_idempotent(db_session: Session) -> None:
    first = seed_sample_bank(db_session)
    db_session.commit()
    second = seed_sample_bank(db_session)
    db_session.commit()

    assert second == first
    assert _count(db_session, Bank) == 1
    assert _count(db_session, BankReportingPeriod) == EXPECTED_PERIODS
    assert _count(db_session, BankFinancialFact) == EXPECTED_FACTS
    param_total = (
        _count(db_session, ParamLcrRunoffRate)
        + _count(db_session, ParamNsfrWeight)
        + _count(db_session, ParamRiskWeight)
        + _count(db_session, ParamCapitalThreshold)
        + _count(db_session, ParamStressShock)
    )
    assert param_total == EXPECTED_PARAMS


def test_get_active_params_excludes_superseded_rows(db_session: Session) -> None:
    seed_sample_bank(db_session)
    superseded = ParamRiskWeight(
        organization_id=DEMO_ORG_ID,
        jurisdiction_code="GH",
        risk_weight_code="RW0",
        weight_pct=Decimal("10"),
        effective_from=date(2024, 1, 1),
        effective_to=date(2025, 1, 1),
        approved_by="Bank of Ghana CRD 2024",
        approval_timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )
    db_session.add(superseded)
    db_session.commit()

    active = get_active_params(db_session, DEMO_ORG_ID, "GH", ParamRiskWeight, date(2026, 3, 31))
    assert len(active) == 7
    assert superseded.id not in {row.id for row in active}
    assert all(row.approved_by == "Bank of Ghana CRD baseline" for row in active)

    boundary = get_active_params(db_session, DEMO_ORG_ID, "GH", ParamRiskWeight, date(2025, 1, 1))
    assert {row.id for row in boundary} == {row.id for row in active}

    historical = get_active_params(
        db_session, DEMO_ORG_ID, "GH", ParamRiskWeight, date(2024, 6, 30)
    )
    assert [row.id for row in historical] == [superseded.id]

    other_tenant = get_active_params(
        db_session, ISOLATED_ORG_ID, "GH", ParamRiskWeight, date(2026, 3, 31)
    )
    assert other_tenant == []
