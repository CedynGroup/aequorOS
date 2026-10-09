"""IFRS 9 ECL engine + CRM supervisory haircuts (Phase 2 items 8/9).

Engine goldens are hand-computed; the capital-run integration proves that
modeled ECL is reported beside the booked general provisions without ever
replacing them in Tier 2, that stage-3 reports as specific allowances, that
scenario runs condition PD/LGD through the ``ecl_*`` shock keys without ever
reaching the stress engine (the increase is a CET1 charge), and that CRM
collateral nets credit exposures after the supervisory haircut — while a book
without staging or collateral keeps the ingested-provisions arithmetic.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.authority.registry import AdvisoryDesignation, authorities_for_metric
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
    compute_rwa,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalReferenceRow,
    ParamStressShock,
    RegulatoryRun,
)
from app.schemas.credit_params import EclAssumptionEntry, EclAssumptionUpdate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import credit_params, regulatory_capital
from app.services.fact_derivation import derive_facts
from app.services.regulatory_reporting.provenance import declared_methodology_notes
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.services.test_le_and_lmt import _CanonicalSeeder
from tests.support.factories.reconciliation import allow_fixture_balance_gap

pytestmark = pytest.mark.usefixtures("capital_run_authority")

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)
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
            "downside",
            Decimal("40"),
            pd_multiplier=Decimal("2"),
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


def test_assumption_segment_matches_the_exposure_category_whatever_its_case() -> None:
    """IFRS 9 ¶B5.5.5: an assumption applies to the exposures grouped under its segment.

    The register stores ``CORPORATE_UNRATED``; the loan family's fact category is
    ``corporate_unrated``. The Board's 2% segment PD must win over the 0.5% ALL row.
    """
    result = compute_ecl(
        (EclExposure("corporate_unrated", 1, Decimal("100000000")),),
        (
            EclAssumption("ALL", 1, Decimal("0.5"), Decimal("45")),
            EclAssumption("CORPORATE_UNRATED", 1, Decimal("2"), Decimal("45")),
        ),
    )
    # 100M x 2% x 45%, not the fallback's 100M x 0.5% x 45% = 225,000.
    assert result.items[0].pd_pct == Decimal("2.000000")
    assert result.total_ecl == Decimal("900000.0000")
    assert result.items[0].segment == "corporate_unrated"


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


def test_fact_derivation_emits_staged_ead_and_crm_buckets(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    # This unit fixture seeds THREE loans and nothing else: no deposits, no
    # capital rows, so liabilities + equity is zero against 80m of assets — the
    # pre-audit derivation plugged 100% of the funding side in silence. The
    # fail-closed balance-sheet control (audit P0-10) refuses that book, so the
    # test records the governed exception that makes the defect explicit. It
    # changes no asserted value; it states, on the record, that this book's
    # entire funding side is manufactured.
    allow_fixture_balance_gap(
        db_session,
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        actor_user_id=DEMO_USER_ID,
        max_gap_fraction=Decimal("1"),
    )
    seeder = _CanonicalSeeder(db_session)
    product = seeder.product("LN.COMM", "CORPORATE_UNRATED")
    seeder.position(
        "ECL/L1",
        "LOAN",
        Decimal("60000000"),
        product=product,
        ifrs9_stage=1,
        extra_attributes={
            "crm_collateral_ghs": "20000000",
            "crm_collateral_class": "corporate_debt",
        },
    )
    seeder.position("ECL/L2", "LOAN", Decimal("15000000"), product=product, ifrs9_stage=2)
    seeder.position(
        "ECL/L3",
        "LOAN",
        Decimal("5000000"),
        product=product,
        ifrs9_stage=3,
        extra_attributes={"crm_guarantee_ghs": "1000000", "crm_guarantor_class": "BANK_DEBT"},
    )
    seeder.position("ECL/L4", "LOAN", Decimal("7000000"), product=product)

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
    # The unstaged loan derives no staged bucket, and is counted rather than
    # silently dropped from the modelled book.
    ecl_group = next(group for group in result.groups if group.group == "ecl_exposure")
    assert len(ecl_group.warnings) == 1
    assert ecl_group.warnings[0].startswith(
        "1 LOAN position(s) with known balances totalling 7,000,000.00"
    )
    assert "ECL/L4" in ecl_group.warnings[0]


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
            module="capital",
            reporting_period_id=period_id,
            scenario_code=scenario,  # type: ignore[index]
        ),
    )


def test_capital_run_uses_modeled_ecl_with_scenario_conditioning(db_session: Session) -> None:
    """Basis: Advisory modelled IFRS 9 ECL beside booked prudential allowances.

    Stress charges are gross.
    """
    materialize_canonical_test_book(db_session)
    period = _seed_ecl_facts(db_session)

    # Staged facts alone do not activate the engine: assumptions are Board
    # configuration, and without them the ingested-provisions path holds.
    before = _run_capital(db_session, period.id, "baseline")
    assert before.status == "succeeded", before
    stored_before = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == before.id))
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
    basis = cast(dict[str, dict[str, str]], run.metrics["basis"])
    for key in (
        "ecl_total_ghs",
        "ecl_general_ghs",
        "ecl_specific_ghs",
        "ecl_stage1_ghs",
        "ecl_stage2_ghs",
        "ecl_stage3_ghs",
    ):
        assert basis[key] == {
            "basis": "modelled_what_if",
            "advisory_designation": "advisory_only",
        }
    assert basis["total_capital_ghs"] == {
        "basis": "prudential",
        "allowance_basis": "booked_general_provisions",
    }
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
    assert Decimal(cast(str, severe.metrics["ecl_stress_charge_ghs"])) == Decimal("2025000.0000")
    severe_basis = cast(dict[str, dict[str, str]], severe.metrics["basis"])
    assert severe_basis["ecl_stress_charge_ghs"] == {
        "basis": "prudential_stress",
        "tax_treatment": (
            "conservative: no tax shield applied pending a governed tax-rate parameter"
        ),
    }


def test_modelled_ecl_registry_and_reporting_provenance_are_advisory() -> None:
    """Basis: Advisory IFRS 9 what-if estimates, never filed booked allowances."""
    metric_ids = ("ecl_total_ghs", "ecl_general_ghs", "ecl_specific_ghs")
    for metric_id in metric_ids:
        entries = authorities_for_metric(metric_id)
        assert entries
        assert all(
            entry.advisory_designation == AdvisoryDesignation.ADVISORY_ONLY for entry in entries
        )
    notes = cast(
        list[dict[str, object]],
        declared_methodology_notes(dict.fromkeys(metric_ids, "ifrs9_pd_lgd_ead")),
    )
    assert {note["metric_id"] for note in notes} == set(metric_ids)
    assert all(note["advisory_designation"] == "advisory_only" for note in notes)


def test_capital_stage3_only_lgd_stress_preserves_cet1_and_tier2(db_session: Session) -> None:
    """Basis: Prudential capital; modelled stage 3 specific allowances never charge CET1."""
    materialize_canonical_test_book(db_session)
    period = _seed_ecl_facts(db_session)
    for fact in db_session.scalars(
        select(BankFinancialFact).where(
            BankFinancialFact.reporting_period_id == period.id,
            BankFinancialFact.fact_group == "ecl_exposure",
        )
    ):
        if not fact.category.endswith(":stage3"):
            fact.amount = Decimal("0")
    db_session.flush()
    _adopt_register(db_session, ("ALL", 3, "0", "40"))
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    baseline = regulatory_capital.compute_scenario_analysis(db_session, MAKER, bank, period, {})
    stressed = regulatory_capital.compute_scenario_analysis(
        db_session, MAKER, bank, period, {"ecl_lgd_multiplier": Decimal("1.075")}
    )
    assert baseline.ecl is not None and stressed.ecl is not None
    assert baseline.ecl.specific_ecl == Decimal("4000000.0000")
    assert stressed.ecl.specific_ecl == Decimal("4300000.0000")
    assert stressed.ecl_stress_charge == Decimal("0.0000")
    assert stressed.ratios.cet1_capital == baseline.ratios.cet1_capital
    assert stressed.ratios.tier2_capital == baseline.ratios.tier2_capital


def test_unstaged_book_keeps_ingested_provisions_untouched(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
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


def _metric(run: RegulatoryRun, key: str) -> Decimal:
    metrics = cast(dict[str, object], run.metrics)
    return Decimal(str(metrics[key]))


def _adopt_register(db: Session, *entries: tuple[str, int, str, str]) -> None:
    credit_params.update_ecl_register(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        EclAssumptionUpdate(
            assumptions=[
                EclAssumptionEntry(
                    segment=segment, stage=stage, pd_pct=Decimal(pd), lgd_pct=Decimal(lgd)
                )
                for segment, stage, pd, lgd in entries
            ],
            effective_from=date(2026, 1, 1),
            approved_by="Model committee minute 2026-02",
            reason="Adopt IFRS 9 PD/LGD set",
        ),
    )


def test_board_segment_assumption_prices_its_own_loan_family(db_session: Session) -> None:
    """IFRS 9 ¶B5.5.5, ¶5.5.17(c): a Board PD/LGD saved for a loan family is the one applied.

    Audit evidence 12.2: the register stored ``CORPORATE_UNRATED`` while the
    exposure was ``corporate_unrated``, so the run reported stage-1 ECL of 0.
    """
    materialize_canonical_test_book(db_session)
    period = _seed_ecl_facts(db_session)
    _adopt_register(
        db_session,
        ("corporate_unrated", 1, "1.5", "45"),
        ("ALL", 2, "15", "45"),
        ("ALL", 3, "0", "60"),
    )

    run = _run_capital(db_session, period.id, "baseline")
    assert run.status == "succeeded", run
    stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
    assert stored is not None
    # 100M x 1.5% x 45%.
    assert _metric(stored, "ecl_stage1_ghs") == Decimal("675000.0000")


def test_ecl_register_rejects_a_segment_no_loan_can_land_in(db_session: Session) -> None:
    """IFRS 9 ¶B5.5.5: a segment must be a grouping the exposures actually carry."""
    materialize_canonical_test_book(db_session)
    for segment in ("LN.COMM", "SME_UNRATED", "corporate"):
        with pytest.raises(HTTPException) as refused:
            _adopt_register(db_session, (segment, 1, "1.5", "45"))
        assert refused.value.status_code == 422
        assert f"'{segment}' is not a loan exposure category" in str(refused.value.detail)

    _adopt_register(db_session, (" sme_retail ", 1, "1.5", "45"), ("all", 2, "15", "45"))
    register = credit_params.get_ecl_register(db_session, MAKER, SAMPLE_BANK_ID, date(2026, 1, 1))
    assert [(row.segment, row.stage) for row in register.assumptions] == [
        ("ALL", 2),
        ("SME_RETAIL", 1),
    ]


def test_capital_run_fails_when_a_staged_segment_is_unpriced(db_session: Session) -> None:
    """IFRS 9 ¶5.5.17: staged EAD with no assumption row is never priced at zero."""
    materialize_canonical_test_book(db_session)
    period = _seed_ecl_facts(db_session)
    _adopt_register(db_session, ("corporate_unrated", 1, "1.5", "45"))

    run = _run_capital(db_session, period.id, "baseline")
    assert run.status == "failed", run
    assert run.error is not None and run.error.code == "ecl_segment_uncovered"
    assert run.error.details is not None
    assert run.error.details["uncovered"] == [
        "commercial_loans:stage2",
        "past_due_unsecured:stage3",
    ]


def test_partly_staged_book_keeps_the_booked_general_provisions(db_session: Session) -> None:
    """Basis: Prudential (BoG CRD 2018) Tier 2 general provisions; input: the bank's booked
    IFRS 9 allowance, never a modelled figure that reaches only part of the loan book.
    """
    materialize_canonical_test_book(db_session)
    period = _seed_ecl_facts(db_session)
    booked = _run_capital(db_session, period.id, "baseline")
    assert booked.status == "succeeded", booked
    _adopt_register(
        db_session, ("ALL", 1, "1.5", "45"), ("ALL", 2, "15", "45"), ("ALL", 3, "0", "60")
    )

    run = _run_capital(db_session, period.id, "baseline")
    assert run.status == "succeeded", run
    stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
    stored_booked = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == booked.id))
    assert stored is not None and stored_booked is not None
    # The canonical book holds far more loan EAD than the 130M staged here.
    assert _metric(stored, "ecl_unstaged_ead_ghs") > Decimal("0")
    assert stored.metrics["total_capital_ghs"] == stored_booked.metrics["total_capital_ghs"]


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_zero_ead_does_not_require_an_assumption(stage: int) -> None:
    """IFRS 9 ¶5.5.17: a closed zero-EAD bucket is not an unpriced exposure."""
    result = compute_ecl(
        (
            EclExposure("corporate_unrated", 1, Decimal("100000000")),
            EclExposure("residential_mortgage", stage, Decimal("0")),
        ),
        (EclAssumption("CORPORATE_UNRATED", 1, Decimal("2"), Decimal("45")),),
    )
    result.require_coverage()
    assert result.uncovered == ()
    assert result.total_ecl == Decimal("900000.0000")


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("unconverted", [False, True])
def test_unstaged_diagnostic_survives_empty_buckets_and_missing_conversion(
    db_session: Session, staged: bool, unconverted: bool
) -> None:
    """IFRS 9 ¶5.5.17: every unstaged loan is counted even without a converted EAD."""
    materialize_canonical_test_book(db_session)
    allow_fixture_balance_gap(
        db_session,
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        actor_user_id=DEMO_USER_ID,
        max_gap_fraction=Decimal("1"),
    )
    seeder = _CanonicalSeeder(db_session)
    product = seeder.product("LN.COMM", "CORPORATE_UNRATED")
    for index in range(5):
        seeder.position(
            f"UNSTAGED/{index}",
            "LOAN",
            Decimal("1000000"),
            product=product,
            currency="USD" if unconverted and index == 0 else "GHS",
            extra_attributes={"balance_ghs": None} if unconverted and index == 0 else None,
        )
    if staged:
        seeder.position("STAGED/1", "LOAN", Decimal("1000000"), product=product, ifrs9_stage=1)
    result = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    group = next(group for group in result.groups if group.group == "ecl_exposure")
    assert group.status == ("derived" if staged else "skipped")
    assert group.rows == (1 if staged else 0)
    assert group.warnings[0].startswith("5 LOAN position(s)")
    assert "UNSTAGED/0" in group.warnings[0]
    assert ("4,000,000.00" if unconverted else "5,000,000.00") in group.warnings[0]
    if unconverted:
        assert group.warnings[1].startswith("1 unstaged LOAN position(s) lack")


def test_new_capital_version_preserves_historical_runs(db_session: Session) -> None:
    """Reproducibility contract: a new calculation version never rewrites a sealed run."""
    materialize_canonical_test_book(db_session)
    period = _seed_ecl_facts(db_session)
    historical = _run_capital(db_session, period.id, "baseline")
    stored = db_session.get(RegulatoryRun, historical.id)
    assert stored is not None
    stored.engine_version = "regulatory-capital-v3.0.0"
    db_session.commit()
    snapshot, metrics, input_hash = stored.inputs, stored.metrics, stored.input_hash
    current = _run_capital(db_session, period.id, "baseline")
    assert current.engine_version == "regulatory-capital-v5.0.0"
    db_session.refresh(stored)
    assert stored.engine_version == "regulatory-capital-v3.0.0"
    assert (stored.inputs, stored.metrics, stored.input_hash) == (snapshot, metrics, input_hash)
    assert current.id != historical.id


def test_capital_run_accepts_an_unpriced_zero_ead_bucket(db_session: Session) -> None:
    """IFRS 9 ¶5.5.17: a closed zero-EAD mortgage cannot block funded covered loans."""
    materialize_canonical_test_book(db_session)
    period = _seed_ecl_facts(db_session)
    db_session.add(
        BankFinancialFact(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            reporting_period_id=period.id,
            fact_group="ecl_exposure",
            category="residential_mortgage:stage2",
            amount=Decimal("0"),
            currency="GHS",
        )
    )
    for fact in db_session.scalars(
        select(BankFinancialFact).where(
            BankFinancialFact.reporting_period_id == period.id,
            BankFinancialFact.category.in_(
                ["commercial_loans:stage2", "past_due_unsecured:stage3"]
            ),
        )
    ):
        db_session.delete(fact)
    _adopt_register(db_session, ("CORPORATE_UNRATED", 1, "1.5", "45"))
    run = _run_capital(db_session, period.id, "baseline")
    assert run.status == "succeeded", run


@pytest.mark.parametrize("scenario", ["baseline", "severe"])
@pytest.mark.requirement("BoG CRD (June 2018) ¶98")
def test_unconverted_unstaged_loan_refuses_capital_even_with_ecl_register(
    db_session: Session, scenario: str
) -> None:
    """BoG CRD (June 2018) ¶98: an unconverted claim cannot disappear from RWA.

    IFRS 9 ¶5.5.17: the omitted unstaged loan still makes ECL coverage partial.
    Adopting ECL assumptions does not establish its capital exposure amount.
    """
    materialize_canonical_test_book(db_session)
    seeder = _CanonicalSeeder(db_session)
    product = seeder.product("LN.COMM", "CORPORATE_UNRATED")
    seeder.position("COVERED/GHS", "LOAN", Decimal("100000000"), product=product, ifrs9_stage=1)
    seeder.position(
        "UNCOVERED/USD",
        "LOAN",
        Decimal("1000000"),
        product=product,
        currency="USD",
        extra_attributes={"balance_ghs": None},
    )
    seeder.position("FUNDING/GHS", "DEPOSIT", Decimal("79000000"))
    seeder.position(
        "FUNDING/USD",
        "DEPOSIT",
        Decimal("1000000"),
        currency="USD",
        extra_attributes={"balance_ghs": None},
    )
    references: list[tuple[str, dict[str, str]]] = [
        (
            "capital_structure",
            {
                "capital_component": "paid_up_capital",
                "amount_ghs": "20000000",
                "tier": "CET1",
            },
        ),
        (
            "capital_structure",
            {
                "capital_component": "general_provisions",
                "amount_ghs": "1000000",
                "tier": "T2",
            },
        ),
    ]
    references.extend(
        (
            "historical_financials",
            {
                "period_end": date(2025, month, 28).isoformat(),
                "net_interest_income_ghs": "1000000",
            },
        )
        for month in range(1, 13)
    )
    for index, (kind, payload) in enumerate(references):
        db_session.add(
            CanonicalReferenceRow(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                ingestion_batch_id=seeder.common["ingestion_batch_id"],
                lineage_id=seeder.common["lineage_id"],
                as_of_date=REPORTING_DATE,
                dataset_kind=kind,
                row_index=index,
                source_reference=f"ECL-COVERAGE/{index}",
                payload=payload,
            )
        )
    db_session.flush()
    derived = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    assert derived.reconciliation is not None and derived.reconciliation.within_tolerance
    group = next(group for group in derived.groups if group.group == "ecl_exposure")
    assert group.warnings[0].startswith("1 LOAN position(s)")
    assert "UNCOVERED/USD" in group.warnings[0]
    assert group.warnings[1].startswith("1 unstaged LOAN position(s) lack")
    fact = db_session.scalar(
        select(BankFinancialFact).where(
            BankFinancialFact.reporting_period_id == derived.reporting_period_id,
            BankFinancialFact.fact_group == "ecl_exposure",
        )
    )
    assert fact is not None and fact.amount == Decimal("100000000")
    attributes = cast(dict[str, object], fact.attributes)
    assert attributes["ecl_coverage_complete"] is False
    booked = _run_capital(db_session, derived.reporting_period_id, scenario)
    assert booked.status == "failed", booked
    assert booked.error is not None and booked.error.code == "missing_parameter"
    assert booked.error.details == {"parameter": "risk_weight_code:unconverted_USD:unclassified"}
    _adopt_register(db_session, ("CORPORATE_UNRATED", 1, "2", "45"))
    modelled = _run_capital(db_session, derived.reporting_period_id, scenario)
    assert modelled.status == "failed", modelled
    assert modelled.error == booked.error
    stored = db_session.get(RegulatoryRun, modelled.id)
    stored_booked = db_session.get(RegulatoryRun, booked.id)
    assert stored is not None and stored_booked is not None
    assert stored.metrics == stored_booked.metrics == {}
    inputs = cast(dict[str, object], stored.inputs)
    facts = cast(list[dict[str, object]], inputs["facts"])
    ecl_input = next(row for row in facts if row["fact_group"] == "ecl_exposure")
    assert ecl_input["ecl_coverage_complete"] is False


def test_filed_capital_run_keeps_the_booked_general_provisions(db_session: Session) -> None:
    """Basis: Prudential (BoG CRD 2018) Tier 2 general provisions; input: the bank's booked
    IFRS 9 allowance, the figure of record — never the platform's modelled ECL.

    Audit evidence 12.3: switching modelled ECL on moved total capital from 340.0M to
    327.0M by replacing the booked general provisions.
    """
    materialize_canonical_test_book(db_session)
    period = _seed_ecl_facts(db_session)
    booked = _run_capital(db_session, period.id, "baseline")
    assert booked.status == "succeeded", booked
    _adopt_register(
        db_session, ("ALL", 1, "1.5", "45"), ("ALL", 2, "15", "45"), ("ALL", 3, "0", "60")
    )

    modelled = _run_capital(db_session, period.id, "baseline")
    assert modelled.status == "succeeded", modelled
    stored_booked = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == booked.id))
    stored = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == modelled.id))
    assert stored_booked is not None and stored is not None
    # The modelled figure is still reported, beside the ratios.
    assert _metric(stored, "ecl_general_ghs") == Decimal("2025000.0000")
    for key in ("total_capital_ghs", "car_pct", "cet1_ratio_pct"):
        assert _metric(stored, key) == _metric(stored_booked, key), key
    assert "ecl_stress_charge_ghs" not in stored.metrics


def test_pd_stress_never_raises_the_capital_ratio(db_session: Session) -> None:
    """Prudential invariant (no IFRS paragraph); Basis: Prudential (BoG CRD 2018).

    A higher modelled ECL can only cost capital: its increase over the unconditioned
    baseline is a CET1 charge, and Tier 2 keeps the booked general provisions. Audit
    evidence 12.3: doubling PD raised CAR from 15.228% to 15.322%.
    """
    materialize_canonical_test_book(db_session)
    period = _seed_ecl_facts(db_session)
    _adopt_register(
        db_session, ("ALL", 1, "1.5", "45"), ("ALL", 2, "15", "45"), ("ALL", 3, "0", "60")
    )
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None

    def analyse(shocks: dict[str, Decimal]) -> regulatory_capital.CapitalScenarioAnalysis:
        return regulatory_capital.compute_scenario_analysis(db_session, MAKER, bank, period, shocks)

    baseline = analyse({})
    stressed = analyse({"ecl_pd_multiplier": Decimal("2")})

    assert stressed.ratios.car_pct < baseline.ratios.car_pct
    assert stressed.ratios.cet1_ratio_pct < baseline.ratios.cet1_ratio_pct
    # Stage 1+2 general ECL doubles from 2,025,000 to 4,050,000; stage 3 is fixed.
    assert stressed.ecl_stress_charge == Decimal("2025000.0000")
    assert stressed.ratios.cet1_capital == baseline.ratios.cet1_capital - Decimal("2025000")
    assert stressed.ratios.tier2_capital == baseline.ratios.tier2_capital
    for multiplier in ("1.5", "3", "10"):
        harsher = analyse({"ecl_pd_multiplier": Decimal(multiplier)})
        assert harsher.ratios.car_pct <= baseline.ratios.car_pct, multiplier
