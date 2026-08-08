"""Phase 2 item 2 — per-currency liquidity gaps + USD funding stress.

The liquidity run snapshot gains the per-currency contractual ladders
(bank-facts-v3); metrics carry the FX funding-mismatch headlines and the
full per-currency gap block; the usd_funding_stress scenario couples cedi
depreciation onto FX liabilities; and the Board's para-11(d) per-currency
mismatch limit produces validation rows only when adopted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import BankReportingPeriod, ParamLiquidityThreshold, RegulatoryRun
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_liquidity
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)
from tests.services.test_le_and_lmt import _CanonicalSeeder

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)


def _period_id(db: Session):
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    return period_id


def _seed_fx_book(db: Session) -> None:
    """GHS: 10.0M loan (>1y) + 8.0M CALL deposit (on demand). USD: 2.0M loan
    at 15 days + 5.0M fixed deposit at 91 days. Cedi equivalents throughout."""
    seeder = _CanonicalSeeder(db)
    seeder.position("FXB/LOAN-GHS", "LOAN", Decimal("10000000"), maturity=date(2027, 6, 30))
    seeder.position(
        "FXB/DEP-GHS", "DEPOSIT", Decimal("8000000"), deposit_account_type="CALL"
    )
    seeder.position(
        "FXB/LOAN-USD", "LOAN", Decimal("2000000"), currency="USD",
        maturity=date(2026, 4, 15), extra_attributes={"balance_ghs": "2000000"},
    )
    seeder.position(
        "FXB/DEP-USD", "DEPOSIT", Decimal("5000000"), currency="USD",
        deposit_account_type="FIXED", maturity=date(2026, 6, 30),
        extra_attributes={"balance_ghs": "5000000"},
    )


def _run(db: Session, scenario: str):
    return regulatory_liquidity.create_liquidity_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=_period_id(db), scenario_code=scenario
        ),
    )


def test_baseline_run_carries_currency_ladders_and_fx_metrics(db_session: Session) -> None:
    seed_sample_bank(db_session)
    _seed_fx_book(db_session)

    run = _run(db_session, "baseline")
    assert run.status == "succeeded", run

    stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
    assert stored is not None
    assert stored.input_schema_version == "bank-facts-v3"
    ladders = stored.inputs["currency_ladders"]
    assert set(ladders) == {"GHS", "USD"}
    # USD: the 15-day loan sits in the first horizon, the 91-day deposit in
    # the second; the CALL deposit reports on demand (first horizon).
    assert ladders["USD"]["assets"] == ["2000000", "0", "0", "0", "0"]
    assert ladders["USD"]["liabilities"] == ["0", "5000000", "0", "0", "0"]
    assert ladders["GHS"]["liabilities"][0] == "8000000"

    metrics = stored.metrics
    assert Decimal(metrics["fx_funding_gap_ghs"]) == Decimal("-3000000")
    # FX share = 5.0M USD liabilities over 13.0M total (canonical book only).
    assert Decimal(metrics["fx_share_of_liabilities_pct"]) == Decimal("38.461538")
    assert Decimal(metrics["fx_depreciation_pct"]) == Decimal("0")
    usd = next(gap for gap in metrics["currency_gaps"] if gap["currency"] == "USD")
    assert usd["cumulative"] == ["2000000", "-3000000", "-3000000", "-3000000", "-3000000"]

    # Reproducibility: an identical rerun hashes identically (v3 snapshot).
    rerun = _run(db_session, "baseline")
    stored_rerun = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == rerun.id))
    assert stored_rerun is not None
    assert stored_rerun.input_hash == stored.input_hash


def test_usd_funding_stress_applies_depreciation_to_fx_liabilities(
    db_session: Session,
) -> None:
    seed_sample_bank(db_session)
    _seed_fx_book(db_session)

    run = _run(db_session, "usd_funding_stress")
    assert run.status == "succeeded", run
    stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
    assert stored is not None
    metrics = stored.metrics
    assert Decimal(metrics["fx_depreciation_pct"]) == Decimal("30")
    # 5.0M USD liabilities x 1.30 = 6.5M stressed; gap 2.0M - 6.5M = -4.5M.
    assert Decimal(metrics["stressed_fx_funding_gap_ghs"]) == Decimal("-4500000.0000")
    usd = next(gap for gap in metrics["currency_gaps"] if gap["currency"] == "USD")
    assert Decimal(usd["stressed_liabilities_total"]) == Decimal("6500000.0000")
    # Base currency never takes the depreciation.
    ghs = next(gap for gap in metrics["currency_gaps"] if gap["currency"] == "GHS")
    assert Decimal(ghs["stressed_liabilities_total"]) == Decimal(
        ghs["liabilities_total"]
    )


def test_board_currency_mismatch_limit_produces_validation_rows(
    db_session: Session,
) -> None:
    seed_sample_bank(db_session)
    _seed_fx_book(db_session)

    # Without a Board row: no per-currency mismatch checks are invented.
    unlimited = _run(db_session, "baseline")
    assert all(
        not v.rule_code.startswith("currency_mismatch_") for v in unlimited.validations
    )

    db_session.add(
        ParamLiquidityThreshold(
            organization_id=DEMO_ORG_ID,
            jurisdiction_code="GH",
            institution_class="bank",
            threshold_code="currency_mismatch_limit_pct",
            threshold_pct=Decimal("10"),
            effective_from=date(2026, 1, 1),
            approved_by="Board minute 2026-05",
            approval_timestamp=utc_now(),
        )
    )
    db_session.flush()

    limited = _run(db_session, "baseline")
    rows = {v.rule_code: v for v in limited.validations}
    usd = rows["currency_mismatch_usd"]
    # Worst USD cumulative gap is -3.0M on 5.0M liabilities = 60% > 10% limit.
    assert usd.passed is False
    assert usd.severity == "warning"
    assert "60" in usd.message and "10" in usd.message


def test_stressed_behavioural_ladder_redistributes_demand_deposits(
    db_session: Session,
) -> None:
    """LRMD ¶50–54: the usd_funding_stress schedule (h1=30%, h2=10%) moves
    the 8.0M GHS CALL book from all-in-horizon-1 to [2.4M, 0.8M, 0, 0,
    4.8M stable core]; the USD fixed deposit is not demand-natured and its
    contractual placement never moves."""
    seed_sample_bank(db_session)
    _seed_fx_book(db_session)

    baseline = _run(db_session, "baseline")
    stored_baseline = db_session.scalar(
        select(RegulatoryRun).where(RegulatoryRun.id == baseline.id)
    )
    assert stored_baseline is not None
    # Baseline carries no behavioural schedule → no stressed ladder block.
    assert "stressed_ladder" not in stored_baseline.metrics
    assert stored_baseline.inputs["currency_ladders"]["GHS"]["demand_liabilities"] == "8000000"

    run = _run(db_session, "usd_funding_stress")
    stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
    assert stored is not None
    ladder = {entry["currency"]: entry for entry in stored.metrics["stressed_ladder"]}

    ghs = ladder["GHS"]
    assert Decimal(ghs["demand_deposits"]) == Decimal("8000000")
    assert [Decimal(v) for v in ghs["stressed_liabilities"]] == [
        Decimal("2400000.0000"),
        Decimal("800000.0000"),
        Decimal("0"),
        Decimal("0"),
        Decimal("4800000.0000"),
    ]
    assert Decimal(ghs["stable_core"]) == Decimal("4800000.0000")

    usd = ladder["USD"]
    assert Decimal(usd["demand_deposits"]) == Decimal("0")
    assert [Decimal(v) for v in usd["stressed_liabilities"]] == [
        Decimal(v) for v in usd["contractual_liabilities"]
    ]
