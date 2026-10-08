"""BoG CRD 2018 ¶32: refused registers cannot become computed capital downstream."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from app.core.errors import ModuleDataUnavailable
from app.domain.authority.outcomes import OutcomeState
from app.domain.capital.engine import (
    CAPITAL_REGISTER_REFUSED_CATEGORY,
    MODELLED_ECL_CHARGE_CATEGORY,
    CapitalFact,
    CapitalRegisterRefused,
    compute_capital_ratios,
    compute_rwa,
    tier1_capital,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalReferenceRow,
    RegulatoryRun,
)
from app.models.stress import MacroScenario, MacroScenarioPath
from app.schemas.enterprise_stress import EnterpriseStressRunCreate
from app.schemas.forecasting import ForecastRunCreate, OptimizerRunCreate, WhatIfRunCreate
from app.schemas.regulatory_fx import FxScenarioBatchCreate
from app.schemas.regulatory_irr import IrrScenarioBatchCreate
from app.schemas.regulatory_irr_sf import IrrbbSfRunCreate
from app.schemas.regulatory_liquidity import (
    RegulatoryRunCreate,
    RegulatoryScenarioCode,
)
from app.schemas.reverse_stress import ReverseStressRunCreate
from app.schemas.scenario_workbench import AnalysisRunCreate, ScenarioRefIn, WorkbenchModule
from app.services import (
    analysis_workbench,
    credit_concentration,
    enterprise_stress,
    regulatory_capital,
    regulatory_credit,
    regulatory_forecasting,
    regulatory_fx,
    regulatory_irr,
    regulatory_irr_sf,
    reverse_stress,
)
from app.services.fact_derivation import derive_current_facts, derive_facts
from app.services.regulatory_reporting.bog_forms.sources import ResolveContext, get_resolver
from tests.domain.test_capital_engine import bog_capital_params
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.sf_book import seed_fx
from tests.services.test_capital_structure_tiers import (
    MAKER,
    REPORTING_DATE,
    _push_register,
    _seed_book,
)
from tests.support.authority import grant_organization_analyst


@pytest.fixture
def refused_book(db_session: Session) -> tuple[Bank, BankReportingPeriod]:
    _seed_book(db_session)
    _push_register(
        db_session,
        ("paid_up_capital", "100000000", "CET1"),
        ("intangible_assets", "-20000000", "garbage"),
    )
    derived = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    derive_current_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    bank = db_session.get(Bank, derived.bank_id)
    period = db_session.get(BankReportingPeriod, derived.reporting_period_id)
    assert bank is not None and period is not None
    marker = db_session.scalar(
        select(BankFinancialFact).where(
            BankFinancialFact.reporting_period_id == period.id,
            BankFinancialFact.fact_group == "capital_component",
            BankFinancialFact.category == CAPITAL_REGISTER_REFUSED_CATEGORY,
        )
    )
    assert marker is not None
    assert marker.capital_tier is None
    assert marker.amount == 0
    refused_rows = cast(list[str], marker.attributes["refused_rows"])
    assert refused_rows == ["intangible_assets ('garbage')"]
    for module in (ModuleScope.CAPITAL, ModuleScope.FORECASTING, ModuleScope.IRRBB, ModuleScope.FX):
        grant_organization_analyst(
            db_session,
            module,
            SensitivityScope.CONFIDENTIAL,
            grantor="capital-refusal-test",
            reason="Exercise capital-register refusal consumers.",
        )
    return bank, period


def _assert_named_refusal(code: str | None, message: str | None) -> None:
    assert code == CAPITAL_REGISTER_REFUSED_CATEGORY
    assert message is not None
    assert "capital_structure" in message
    assert "re-ingest" in message and "re-derive" in message


@pytest.mark.parametrize("module", ["irr", "fx"])
def test_denominator_dependent_official_runs_preserve_refusal(
    db_session: Session, refused_book: tuple[Bank, BankReportingPeriod], module: str
) -> None:
    """BoG CRD 2018 ¶32: IRR and FX runs record the named capital refusal."""
    bank, period = refused_book
    if module == "irr":
        batch = regulatory_irr.run_all_irr_scenarios(
            db_session,
            MAKER,
            bank.id,
            IrrScenarioBatchCreate(reporting_period_id=period.id),
        )
    else:
        batch = regulatory_fx.run_all_fx_scenarios(
            db_session,
            MAKER,
            bank.id,
            FxScenarioBatchCreate(reporting_period_id=period.id),
        )
    assert batch.runs
    for run in batch.runs:
        assert run.status == "failed" and run.error is not None
        _assert_named_refusal(run.error.code, run.error.message)
        assert run.metrics == {}


@pytest.mark.parametrize("module", ["irr", "fx"])
@pytest.mark.parametrize("official", [False, True])
def test_denominator_dependent_dashboards_report_unavailable_capital(
    db_session: Session,
    refused_book: tuple[Bank, BankReportingPeriod],
    module: str,
    official: bool,
) -> None:
    """BoG CRD 2018 ¶32: current and official dashboards provide corrective guidance."""
    bank, period = refused_book
    dashboard = (
        regulatory_irr.get_irr_dashboard if module == "irr" else regulatory_fx.get_fx_dashboard
    )
    with pytest.raises(ModuleDataUnavailable) as refused:
        dashboard(db_session, MAKER, bank.id, period.id if official else None)
    _assert_named_refusal(refused.value.error_code, refused.value.reason)


@pytest.mark.parametrize("module", ["irr", "fx", "capital"])
def test_denominator_dependent_trends_report_refused_periods(
    db_session: Session,
    refused_book: tuple[Bank, BankReportingPeriod],
    module: str,
) -> None:
    """BoG CRD 2018 ¶32: a refused trend period retains the named corrective action."""
    bank, period = refused_book
    trend = {
        "irr": regulatory_irr._build_trend,
        "fx": regulatory_fx._build_trend,
        "capital": regulatory_capital._build_trend,
    }[module]
    with pytest.raises(ModuleDataUnavailable) as refused:
        trend(db_session, MAKER, bank, [period])
    _assert_named_refusal(refused.value.error_code, refused.value.reason)


@pytest.mark.parametrize("module", ["irr", "fx"])
def test_denominator_dependent_live_metrics_preserve_refusal(
    db_session: Session,
    refused_book: tuple[Bank, BankReportingPeriod],
    module: str,
) -> None:
    """BoG CRD 2018 ¶32: live computations expose their established named run error."""
    bank, period = refused_book
    compute = regulatory_irr.compute_live if module == "irr" else regulatory_fx.compute_live
    with pytest.raises((regulatory_irr.IrrRunError, regulatory_fx.FxRunError)) as refused:
        compute(db_session, MAKER, bank, period)
    _assert_named_refusal(refused.value.code, refused.value.message)


@pytest.mark.parametrize("module", ["irr", "fx"])
def test_denominator_dependent_workbenches_preserve_refusal(
    db_session: Session,
    refused_book: tuple[Bank, BankReportingPeriod],
    module: WorkbenchModule,
) -> None:
    """BoG CRD 2018 ¶32: workbench failures preserve capital correction instructions."""
    bank, period = refused_book
    analysis = analysis_workbench.run_analysis(
        db_session,
        MAKER,
        bank.id,
        module,
        AnalysisRunCreate(
            reporting_period_id=period.id, scenarios=[ScenarioRefIn(kind="system", code="baseline")]
        ),
    )
    assert len(analysis.results) == 1
    result = analysis.results[0]
    assert result.status == "failed" and result.metrics == {}
    _assert_named_refusal(result.error_code, result.error_message)


def test_sf_book_refusal_is_recorded_as_a_failed_attempt(
    db_session: Session,
    refused_book: tuple[Bank, BankReportingPeriod],
) -> None:
    """BoG CRD 2018 ¶32: SF book assembly records a named failed attempt before compute."""
    bank, period = refused_book
    seed_fx(db_session, base="USD", quote="GHS", rate="12.85")
    run = regulatory_irr_sf.run_standardised_framework(
        db_session,
        MAKER,
        bank.id,
        IrrbbSfRunCreate(reporting_period_id=period.id),
    )
    assert run.status == "failed" and run.error is not None, run
    _assert_named_refusal(run.error.code, run.error.message)
    assert run.metrics == {}
    stored = db_session.get(RegulatoryRun, run.id)
    assert stored is not None and stored.completed_at is not None


@pytest.mark.parametrize("surface", ["concentration", "live"])
def test_credit_surfaces_report_unavailable_capital(
    db_session: Session,
    refused_book: tuple[Bank, BankReportingPeriod],
    surface: str,
) -> None:
    """BoG CRD 2018 ¶32: concentration and live credit retain the refused denominator."""
    bank, period = refused_book
    with pytest.raises(ModuleDataUnavailable) as refused:
        if surface == "concentration":
            regulatory_credit.get_credit_concentration(db_session, MAKER, bank.id)
        else:
            regulatory_credit.compute_live(db_session, MAKER, bank, period)
    _assert_named_refusal(refused.value.error_code, refused.value.reason)


def test_reverse_stress_translates_refusal_to_a_named_conflict(
    db_session: Session,
    refused_book: tuple[Bank, BankReportingPeriod],
) -> None:
    """BoG CRD 2018 ¶32: reverse stress refuses before publishing a frontier."""
    bank, period = refused_book
    with pytest.raises(HTTPException) as refused:
        reverse_stress.run_reverse_stress(
            db_session,
            MAKER,
            bank.id,
            ReverseStressRunCreate(reporting_period_id=period.id),
        )
    assert refused.value.status_code == 409
    detail = cast(dict[str, str], refused.value.detail)
    _assert_named_refusal(detail["error_code"], detail["message"])
    assert (
        db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.module == "reverse_stress"))
        is None
    )


@pytest.mark.parametrize("scenario", ["baseline", "mild", "moderate", "severe"])
def test_official_capital_runs_refuse_invalid_registers(
    db_session: Session,
    refused_book: tuple[Bank, BankReportingPeriod],
    scenario: RegulatoryScenarioCode,
) -> None:
    """BoG CRD 2018 ¶32: official baseline and stress runs retain the register refusal."""
    bank, period = refused_book
    run = regulatory_capital.create_capital_run(
        db_session,
        MAKER,
        bank.id,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period.id, scenario_code=scenario
        ),
    )
    assert run.status == "failed"
    assert run.error is not None
    _assert_named_refusal(run.error.code, run.error.message)
    assert run.metrics == {}


@pytest.mark.parametrize("module", ["capital", "forecast"])
def test_live_capital_and_forecast_refuse_invalid_registers(
    db_session: Session, refused_book: tuple[Bank, BankReportingPeriod], module: str
) -> None:
    """BoG CRD 2018 ¶32: current facts carry the refusal into both live computations."""
    bank, period = refused_book
    compute = (
        regulatory_capital.compute_live
        if module == "capital"
        else regulatory_forecasting.compute_live
    )
    with pytest.raises(CapitalRegisterRefused) as refused:
        compute(db_session, MAKER, bank, period)
    _assert_named_refusal(refused.value.code, str(refused.value))
    assert refused.value.state == OutcomeState.DATA_QUALITY_BLOCK
    assert refused.value.blocks_filing


def test_capital_workbench_refuses_all_scenarios_without_metrics(
    db_session: Session, refused_book: tuple[Bank, BankReportingPeriod]
) -> None:
    """BoG CRD 2018 ¶32: the workbench reports a named failure for baseline and stress."""
    bank, period = refused_book
    analysis = analysis_workbench.run_analysis(
        db_session,
        MAKER,
        bank.id,
        "capital",
        AnalysisRunCreate(
            reporting_period_id=period.id,
            scenarios=[
                ScenarioRefIn(kind="system", code="baseline"),
                ScenarioRefIn(kind="system", code="severe"),
            ],
        ),
    )
    assert len(analysis.results) == 2
    for result in analysis.results:
        assert result.status == "failed"
        _assert_named_refusal(result.error_code, result.error_message)
        assert result.metrics == {}


@pytest.mark.parametrize("module", ["forecast", "optimizer", "whatif"])
def test_forecast_variants_preserve_the_named_register_refusal(
    db_session: Session, refused_book: tuple[Bank, BankReportingPeriod], module: str
) -> None:
    """BoG CRD 2018 ¶32: projection, optimizer and what-if runs refuse the same marker."""
    bank, period = refused_book
    if module == "forecast":
        run = regulatory_forecasting.create_forecast_run(
            db_session,
            MAKER,
            bank.id,
            ForecastRunCreate(reporting_period_id=period.id, scenario_code="base"),
        )
    elif module == "optimizer":
        run = regulatory_forecasting.run_strategic_optimizer(
            db_session, MAKER, bank.id, OptimizerRunCreate(reporting_period_id=period.id)
        )
    else:
        run = regulatory_forecasting.run_whatif_analysis(
            db_session,
            MAKER,
            bank.id,
            WhatIfRunCreate(reporting_period_id=period.id, shock_code="default_spike"),
        )
    assert run.status == "failed"
    assert run.error is not None
    _assert_named_refusal(run.error.code, run.error.message)


def test_enterprise_stress_refuses_the_register_before_projecting(
    db_session: Session, refused_book: tuple[Bank, BankReportingPeriod]
) -> None:
    """BoG CRD 2018 ¶32: an approved enterprise scenario cannot bypass refused capital."""
    bank, period = refused_book
    scenario = MacroScenario(
        organization_id=MAKER.organization_id,
        bank_id=bank.id,
        code="capital_refusal",
        name="Capital refusal regression",
        scenario_type="adverse",
        severity="severe",
        status="approved",
        horizon_years=3,
    )
    db_session.add(scenario)
    db_session.flush()
    levels = {
        "gdp_growth": ("0.05", "0"),
        "interest_rate": ("0.20", "0.25"),
        "inflation": ("0.15", "0.21"),
        "unemployment": ("0.06", "0.09"),
        "fx_usd_ghs": ("12.5", "15"),
        "gse_index": ("5000", "3500"),
        "gog_yield": ("0.22", "0.26"),
    }
    db_session.add_all(
        MacroScenarioPath(
            organization_id=MAKER.organization_id,
            scenario_id=scenario.id,
            variable=variable,
            year_index=year,
            base_value=Decimal(base),
            stress_value=Decimal(stress),
        )
        for variable, (base, stress) in levels.items()
        for year in (1, 2, 3)
    )
    db_session.flush()
    with pytest.raises(enterprise_stress.EnterpriseStressError) as refused:
        enterprise_stress.run_enterprise_stress_test(
            db_session,
            MAKER,
            bank.id,
            EnterpriseStressRunCreate(
                reporting_period_id=period.id,
                scenario_id=scenario.id,
                reason="Test invalid-register refusal.",
            ),
        )
    detail = cast(dict[str, str], refused.value.detail)
    _assert_named_refusal(detail["error_code"], detail["message"])
    assert refused.value.status_code == 409


@pytest.mark.parametrize("resolver", ["facts.sum", "bsd5.capital_facts", "run.metric"])
@pytest.mark.parametrize("column", ["domestic", "foreign", "total"])
def test_bog_capital_cells_refuse_before_component_or_currency_selection(
    db_session: Session, refused_book: tuple[Bank, BankReportingPeriod], resolver: str, column: str
) -> None:
    """BoG CRD 2018 ¶32: BSD2 and BSD5 capital cells cannot map a refused register to zero."""
    bank, period = refused_book
    rc = ResolveContext(db_session, MAKER, bank, period, column)
    with pytest.raises(CapitalRegisterRefused) as refused:
        get_resolver(resolver)(
            rc,
            {
                "group": "capital_component",
                "categories": ["paid_up_capital"],
                "module": "capital",
                "metric": "car_pct",
            },
        )
    _assert_named_refusal(refused.value.code, str(refused.value))


def test_synthetic_ecl_charge_does_not_clear_the_refusal_marker(
    db_session: Session, refused_book: tuple[Bank, BankReportingPeriod]
) -> None:
    """BoG CRD 2018 ¶32: a modelled CET1 charge cannot replace a refused capital register."""
    bank, period = refused_book
    facts = tuple(
        regulatory_capital._to_engine_fact(row)
        for row in regulatory_capital._load_facts(db_session, MAKER, bank, period)
    )
    params = bog_capital_params()
    rwa = compute_rwa(
        tuple(fact for fact in facts if fact.category != CAPITAL_REGISTER_REFUSED_CATEGORY), params
    )
    assert rwa.total_rwa > 0
    synthetic = CapitalFact(
        "capital_component",
        MODELLED_ECL_CHARGE_CATEGORY,
        Decimal("20000000"),
        capital_tier="CET1",
        is_deduction=True,
    )
    with pytest.raises(CapitalRegisterRefused):
        compute_capital_ratios((*facts, synthetic), rwa, params)
    with pytest.raises(CapitalRegisterRefused):
        tier1_capital(facts)


def test_books_without_a_register_keep_their_existing_zero_capital_behavior(
    db_session: Session,
) -> None:
    """BoG CRD 2018 ¶32: refusal markers distinguish invalid registers from unsupplied ones."""
    _seed_book(db_session)
    db_session.execute(
        delete(CanonicalReferenceRow).where(
            CanonicalReferenceRow.dataset_kind == "capital_structure"
        )
    )
    derived = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    bank = db_session.get(Bank, derived.bank_id)
    period = db_session.get(BankReportingPeriod, derived.reporting_period_id)
    assert bank is not None and period is not None
    facts = tuple(
        regulatory_capital._to_engine_fact(row)
        for row in regulatory_capital._load_facts(db_session, MAKER, bank, period)
    )
    assert not any(fact.fact_group == "capital_component" for fact in facts)
    params = bog_capital_params()
    ratios = compute_capital_ratios(facts, compute_rwa(facts, params), params)
    assert ratios.car_pct == 0
    assert tier1_capital(facts) == 0
    rc = ResolveContext(db_session, MAKER, bank, period, "total")
    assert get_resolver("facts.sum")(rc, {"group": "capital_component"}) == 0
    assert get_resolver("bsd5.capital_facts")(rc, {"categories": ["paid_up_capital"]}) == 0


def test_correcting_the_register_clears_official_and_live_refusals(
    db_session: Session, refused_book: tuple[Bank, BankReportingPeriod]
) -> None:
    """BoG CRD 2018 ¶32: a corrected complete register restores computable capital."""
    bank, period = refused_book
    _push_register(
        db_session,
        ("paid_up_capital", "100000000", "CET1"),
        ("intangible_assets", "-20000000", "CET1_DEDUCTION"),
    )
    derive_facts(db_session, MAKER, bank.id, REPORTING_DATE)
    derive_current_facts(db_session, MAKER, bank.id, REPORTING_DATE)
    facts = tuple(
        regulatory_capital._to_engine_fact(row)
        for row in regulatory_capital._load_facts(db_session, MAKER, bank, period)
    )
    assert not any(fact.category == CAPITAL_REGISTER_REFUSED_CATEGORY for fact in facts)
    assert tier1_capital(facts) == Decimal("80000000")
    assert regulatory_irr.tier1_for_period(db_session, MAKER, bank, period) == Decimal("80000000")
    assert credit_concentration.capital_base(db_session, MAKER, bank, REPORTING_DATE) == Decimal(
        "80000000"
    )
    fx_batch = regulatory_fx.run_all_fx_scenarios(
        db_session,
        MAKER,
        bank.id,
        FxScenarioBatchCreate(reporting_period_id=period.id),
    )
    for fx_run in fx_batch.runs:
        assert fx_run.status == "succeeded", fx_run.error
        assert Decimal(cast(str, fx_run.metrics["tier1_ghs"])) == Decimal("80000000")
    live = regulatory_capital.compute_live(db_session, MAKER, bank, period)
    assert Decimal(cast(str, live.metrics["car_pct"])) > 0
    rc = ResolveContext(db_session, MAKER, bank, period, "total")
    assert get_resolver("facts.sum")(
        rc, {"group": "capital_component", "categories": ["paid_up_capital"], "currency": "all"}
    ) == Decimal("100000000")
    assert get_resolver("bsd5.capital_facts")(rc, {"categories": ["absent_component"]}) == 0
