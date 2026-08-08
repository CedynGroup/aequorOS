"""IFRS 9 ECL engine + CRM supervisory haircuts (Phase 2 items 8/9).

Engine goldens are hand-computed; the capital-run integration proves that
modeled stage-1/2 ECL replaces the ingested general-provisions component
(still Tier-2-capped), that stage-3 reports as specific allowances, that
scenario runs condition PD/LGD through the ``ecl_*`` shock keys without ever
reaching the stress engine, and that CRM collateral nets credit exposures
after the supervisory haircut — while a book without staging or collateral
keeps the pre-existing ingested-provisions arithmetic untouched.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.capital.ecl import (
    EclAssumption,
    EclComputationError,
    EclExposure,
    EclScenario,
    compute_ecl,
)
from app.domain.capital.engine import (
    CapitalFact,
    CapitalParams,
    compute_capital_ratios,
    compute_rwa,
)
from app.models import BankFinancialFact, BankReportingPeriod, ParamStressShock, RegulatoryRun
from app.schemas.credit_params import EclAssumptionEntry, EclAssumptionUpdate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import credit_params, regulatory_capital
from app.services.fact_derivation import derive_facts
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)
from tests.services.test_le_and_lmt import _CanonicalSeeder

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)

ASSUMPTIONS = (
    EclAssumption("ALL", 1, Decimal("1.5"), Decimal("45")),
    EclAssumption("ALL", 2, Decimal("15"), Decimal("45")),
    EclAssumption("ALL", 3, Decimal("0"), Decimal("60")),
)


def test_ecl_engine_base_scenario_goldens() -> None:
    result = compute_ecl(
        (
            EclExposure("commercial_loans", 1, Decimal("100000000")),
            EclExposure("commercial_loans", 2, Decimal("20000000")),
            EclExposure("past_due_unsecured", 3, Decimal("10000000")),
        ),
        ASSUMPTIONS,
    )
    # 100M x 1.5% x 45% / 20M x 15% x 45% / 10M x 100% (stage 3) x 60%.
    assert result.stage_totals[1] == Decimal("675000.0000")
    assert result.stage_totals[2] == Decimal("1350000.0000")
    assert result.stage_totals[3] == Decimal("6000000.0000")
    assert result.general_ecl == Decimal("2025000.0000")
    assert result.specific_ecl == Decimal("6000000.0000")
    assert result.total_ecl == Decimal("8025000.0000")
    assert result.uncovered == ()


def test_ecl_probability_weighted_scenarios_and_guards() -> None:
    scenarios = (
        EclScenario("base", Decimal("60")),
        EclScenario(
            "downside", Decimal("40"), pd_multiplier=Decimal("2"),
            lgd_multiplier=Decimal("1.1"),
        ),
    )
    result = compute_ecl(
        (EclExposure("commercial_loans", 1, Decimal("100000000")),), ASSUMPTIONS, scenarios
    )
    # 0.6 x 1.5% x 45% + 0.4 x 3.0% x 49.5% = 0.999% of EAD.
    assert result.total_ecl == Decimal("999000.0000")

    with pytest.raises(EclComputationError):
        compute_ecl((), ASSUMPTIONS, (EclScenario("base", Decimal("90")),))

    partial = compute_ecl(
        (EclExposure("mystery_book", 2, Decimal("5000000")),),
        (EclAssumption("OTHER", 2, Decimal("10"), Decimal("50")),),
    )
    assert partial.total_ecl == Decimal("0.0000")
    assert partial.uncovered == (("mystery_book", 2),)


def _minimal_capital_params(crm_haircuts: dict[str, Decimal]) -> CapitalParams:
    return CapitalParams(
        risk_weights={"RW100": Decimal("100"), "RW0": Decimal("0")},
        bia_alpha_pct=Decimal("15"),
        fx_charge_pct=Decimal("10"),
        rwa_multiplier_pct=Decimal("1250"),
        tier2_gp_cap_pct_credit_rwa=Decimal("1.25"),
        cet1_min_pct=Decimal("6.5"),
        tier1_min_pct=Decimal("8"),
        car_min_pct=Decimal("13"),
        leverage_min_pct=Decimal("6"),
        car_early_warning_pct=Decimal("14"),
        car_critical_pct=Decimal("10"),
        crm_haircuts=crm_haircuts,
    )


def test_crm_collateral_nets_credit_exposure_after_supervisory_haircut() -> None:
    facts = (
        CapitalFact("loan_exposure", "commercial_loans", Decimal("50000000"), "RW100"),
        # 20M corporate-debt collateral at an 8% haircut -> 18.4M recognized.
        CapitalFact("crm_collateral", "commercial_loans:CORPORATE_DEBT", Decimal("20000000")),
        # Unknown class: zero recognition, never an invented haircut.
        CapitalFact("crm_collateral", "commercial_loans:ART_COLLECTION", Decimal("99000000")),
        CapitalFact("operational_income", "gross_income", Decimal("10000000"), income_year=2025),
    )
    rwa = compute_rwa(facts, _minimal_capital_params({"CORPORATE_DEBT": Decimal("8")}))
    line = next(item for item in rwa.line_items if item.line_code == "commercial_loans")
    assert line.exposure_amount == Decimal("31600000.0000")
    assert line.weighted_amount == Decimal("31600000.0000")
    assert "After CRM" in line.description

    bare = compute_rwa(facts, _minimal_capital_params({}))
    bare_line = next(item for item in bare.line_items if item.line_code == "commercial_loans")
    assert bare_line.exposure_amount == Decimal("50000000.0000")


def test_general_provisions_override_replaces_ingested_component() -> None:
    facts = (
        CapitalFact("loan_exposure", "commercial_loans", Decimal("100000000"), "RW100"),
        CapitalFact("operational_income", "gross_income", Decimal("10000000"), income_year=2025),
        CapitalFact(
            "capital_component", "paid_up_capital", Decimal("20000000"), capital_tier="CET1"
        ),
        CapitalFact(
            "capital_component",
            "general_provisions",
            Decimal("900000"),
            capital_tier="T2",
        ),
        CapitalFact("balance_sheet", "total_assets", Decimal("120000000"), side="asset"),
    )
    params = _minimal_capital_params({})
    rwa = compute_rwa(facts, params)
    ingested = compute_capital_ratios(facts, rwa, params)
    assert ingested.general_provisions_amount == Decimal("900000.0000")
    modeled = compute_capital_ratios(
        facts, rwa, params, general_provisions_override=Decimal("600000")
    )
    assert modeled.general_provisions_amount == Decimal("600000.0000")
    assert modeled.tier2_capital == Decimal("600000.0000")


def test_fact_derivation_emits_staged_ead_and_crm_buckets(db_session: Session) -> None:
    seed_sample_bank(db_session)
    seeder = _CanonicalSeeder(db_session)
    product = seeder.product("LN.COMM", "CORPORATE_UNRATED")
    seeder.position(
        "ECL/L1", "LOAN", Decimal("60000000"), product=product, ifrs9_stage=1,
        extra_attributes={
            "crm_collateral_ghs": "20000000",
            "crm_collateral_class": "corporate_debt",
        },
    )
    seeder.position("ECL/L2", "LOAN", Decimal("15000000"), product=product, ifrs9_stage=2)
    seeder.position(
        "ECL/L3", "LOAN", Decimal("5000000"), product=product, ifrs9_stage=3,
        extra_attributes={"crm_guarantee_ghs": "1000000", "crm_guarantor_class": "BANK_DEBT"},
    )

    result = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    facts = db_session.scalars(
        select(BankFinancialFact).where(
            BankFinancialFact.organization_id == DEMO_ORG_ID,
            BankFinancialFact.bank_id == SAMPLE_BANK_ID,
            BankFinancialFact.fact_group.in_(("ecl_exposure", "crm_collateral")),
        )
    ).all()
    by_key = {(fact.fact_group, fact.category): Decimal(str(fact.amount)) for fact in facts}
    assert by_key[("ecl_exposure", "corporate_unrated:stage1")] == Decimal("60000000")
    assert by_key[("ecl_exposure", "corporate_unrated:stage2")] == Decimal("15000000")
    # Stage-3 loans reclassify to the past-due family before bucketing.
    stage3_key = next(
        key for key in by_key if key[0] == "ecl_exposure" and key[1].endswith(":stage3")
    )
    assert by_key[stage3_key] == Decimal("5000000")
    assert by_key[("crm_collateral", "corporate_unrated:CORPORATE_DEBT")] == Decimal("20000000")
    crm_guarantee_key = next(
        key for key in by_key if key[0] == "crm_collateral" and key[1].endswith(":BANK_DEBT")
    )
    assert by_key[crm_guarantee_key] == Decimal("1000000")
    assert result is not None


def _seed_ecl_facts(db: Session) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period is not None
    rows = (
        ("ecl_exposure", "corporate_unrated:stage1", Decimal("100000000")),
        ("ecl_exposure", "commercial_loans:stage2", Decimal("20000000")),
        ("ecl_exposure", "past_due_unsecured:stage3", Decimal("10000000")),
    )
    for fact_group, category, amount in rows:
        db.add(
            BankFinancialFact(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                reporting_period_id=period.id,
                fact_group=fact_group,
                category=category,
                amount=amount,
                currency="GHS",
            )
        )
    db.flush()
    return period


def _run_capital(db: Session, period_id, scenario: str):
    return regulatory_capital.create_capital_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period_id, scenario_code=scenario
        ),
    )


def test_capital_run_uses_modeled_ecl_with_scenario_conditioning(db_session: Session) -> None:
    seed_sample_bank(db_session)
    period = _seed_ecl_facts(db_session)

    # Staged facts alone do not activate the engine: assumptions are Board
    # configuration, and without them the ingested-provisions path holds.
    before = _run_capital(db_session, period.id, "baseline")
    assert before.status == "succeeded", before
    stored_before = db_session.scalar(
        select(RegulatoryRun).where(RegulatoryRun.id == before.id)
    )
    assert stored_before is not None
    assert "ecl_total_ghs" not in stored_before.metrics

    credit_params.update_ecl_register(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        EclAssumptionUpdate(
            assumptions=[
                EclAssumptionEntry(
                    segment="ALL", stage=1, pd_pct=Decimal("1.5"), lgd_pct=Decimal("45")
                ),
                EclAssumptionEntry(
                    segment="ALL", stage=2, pd_pct=Decimal("15"), lgd_pct=Decimal("45")
                ),
                EclAssumptionEntry(
                    segment="ALL", stage=3, pd_pct=Decimal("0"), lgd_pct=Decimal("60")
                ),
            ],
            effective_from=date(2026, 1, 1),
            approved_by="Model committee minute 2026-02",
            reason="Adopt IFRS 9 PD/LGD set",
        ),
    )

    run = _run_capital(db_session, period.id, "baseline")
    assert run.status == "succeeded", run
    stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
    assert stored is not None
    metrics = stored.metrics
    assert Decimal(metrics["ecl_general_ghs"]) == Decimal("2025000.0000")
    assert Decimal(metrics["ecl_specific_ghs"]) == Decimal("6000000.0000")
    assert Decimal(metrics["ecl_total_ghs"]) == Decimal("8025000.0000")
    # Configured assumptions enter the snapshot: the hash must move.
    assert stored.input_hash != stored_before.input_hash
    # Deterministic: an identical rerun reproduces the hash.
    rerun = _run_capital(db_session, period.id, "baseline")
    stored_rerun = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == rerun.id))
    assert stored_rerun is not None and stored_rerun.input_hash == stored.input_hash

    # Scenario conditioning: PD doubles under the severe scenario's ecl_*
    # shock keys, which never reach the stress engine.
    db_session.add(
        ParamStressShock(
            organization_id=DEMO_ORG_ID,
            jurisdiction_code="GH",
            module="capital",
            scenario_code="severe",
            shock_key="ecl_pd_multiplier",
            shock_value=Decimal("2"),
            effective_from=date(2026, 1, 1),
            approved_by="test fixture",
            approval_timestamp=utc_now(),
        )
    )
    db_session.flush()
    severe = _run_capital(db_session, period.id, "severe")
    assert severe.status == "succeeded", severe
    stored_severe = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == severe.id))
    assert stored_severe is not None
    # Stage 1: 100M x 3% x 45% = 1.35M; stage 2: 20M x 30% x 45% = 2.7M.
    assert Decimal(stored_severe.metrics["ecl_general_ghs"]) == Decimal("4050000.0000")
    # Stage 3 PD is already 100%: conditioning must not inflate it.
    assert Decimal(stored_severe.metrics["ecl_specific_ghs"]) == Decimal("6000000.0000")


def test_unstaged_book_keeps_ingested_provisions_untouched(db_session: Session) -> None:
    seed_sample_bank(db_session)
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period is not None
    run = _run_capital(db_session, period.id, "baseline")
    assert run.status == "succeeded", run
    stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
    assert stored is not None
    assert "ecl_total_ghs" not in stored.metrics
    assert "crm_haircuts_pct" not in stored.inputs["parameters"]
    assert "ecl_assumptions" not in stored.inputs["parameters"]
