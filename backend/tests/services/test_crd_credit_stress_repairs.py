"""BoG CRD (June 2018) ¶98, ¶139: stress and capital share net measurement."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from app.domain.capital.engine import CapitalFact
from app.domain.forecasting.engine import ForecastFact
from app.domain.stress.credit_bottom_up import compute_bottom_up_credit
from app.domain.stress.projection import EnterpriseProjectionInputs, project_enterprise
from app.models import BankFinancialFact, CanonicalGlAccount, RegulatoryRun
from app.schemas.reverse_stress import ReverseStressRunCreate
from app.services import enterprise_stress, reverse_stress
from app.services.fact_derivation import _derive_specs, _PositionRow, derive_facts
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    base_paths,
    bog_forecast_params,
    sample_bank_latest_facts,
)
from tests.domain.test_capital_engine import bog_capital_params
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.fixtures.capital_structure import MAKER, REPORTING_DATE, seed_book
from tests.services.test_crd_credit_exposures import _credit_rwa, _stress_row
from tests.services.test_derivation_fail_closed_defaults import _canonical, _row
from tests.support.authority import grant_organization_analyst

pytestmark = pytest.mark.requirement("BoG CRD (June 2018) ¶98, ¶139")


def _capital_facts(rows: list[_PositionRow]) -> list[CapitalFact]:
    specs, _, _ = _derive_specs(_canonical(*rows), live=True)
    return [
        CapitalFact(spec.fact_group, spec.category, spec.amount, spec.risk_weight_code)
        for spec in specs
    ]


@pytest.mark.parametrize("foreign", [False, True])
def test_collateral_and_specific_provision_share_capital_basis_under_migration_and_fx(
    foreign: bool,
) -> None:
    """BoG CRD (June 2018) ¶98, ¶139: net provisions before allocating the CRM pool."""
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    row = _row(
        "SME/1",
        "LOAN",
        currency="USD" if foreign else "GHS",
        balance="1000",
        balance_ghs="1000",
        regulatory_category="SME_UNRATED",
        attributes={
            "specific_provision_ghs": "200",
            "crm_collateral_ghs": "500",
            "crm_collateral_class": "corporate_debt",
        },
    )
    book = enterprise_stress._build_credit_exposures(
        [_stress_row(row)], params, capital_facts=_capital_facts([row])
    )
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("2"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0.1")
    )
    assert (
        result.base_credit_rwa
        == _credit_rwa(row, params=params)
        == Decimal("360" if foreign else "300")
    )
    assert result.stressed_credit_rwa == Decimal("445.5" if foreign else "375")
    assert book[0].ead == Decimal("1000")
    assert result.base_expected_loss == Decimal("9")
    assert result.stressed_expected_loss == Decimal("19.8" if foreign else "18")


def test_partial_collateral_migration_increments_only_unsecured_rwa() -> None:
    """BoG CRD (June 2018) ¶139: a 500 uncovered SME slice migrates from 100% to 125%."""
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    row = _row(
        "SME/1",
        "LOAN",
        balance="1000",
        regulatory_category="SME_UNRATED",
        attributes={"crm_collateral_ghs": "500", "crm_collateral_class": "corporate_debt"},
    )
    book = enterprise_stress._build_credit_exposures(
        [_stress_row(row)], params, capital_facts=_capital_facts([row])
    )
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("2"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0")
    )
    assert result.base_credit_rwa == Decimal("500")
    assert result.stressed_credit_rwa == Decimal("625")
    assert result.migration_rwa == Decimal("125")


@pytest.mark.parametrize("growth", ["0", "10"])
@pytest.mark.parametrize("collateral", ["0", "500"])
def test_projection_stresses_each_grown_bucket_and_leaves_domestic_residual_lines(
    growth: str, collateral: str
) -> None:
    """BoG CRD (June 2018) ¶98, ¶139: annual FX delta belongs only to grown FX SME claims."""
    dom = _row(
        "SME/DOM",
        "LOAN",
        balance="1000",
        regulatory_category="SME_UNRATED",
        attributes={"crm_collateral_ghs": collateral, "crm_collateral_class": "corporate_debt"},
    )
    fx = _row(
        "SME/FX",
        "LOAN",
        currency="USD",
        balance="1000",
        balance_ghs="1000",
        regulatory_category="SME_UNRATED",
    )
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    cap_facts = _capital_facts([dom, fx])
    book = enterprise_stress._build_credit_exposures(
        [_stress_row(dom), _stress_row(fx)], params, capital_facts=cap_facts
    )
    facts = [
        fact
        for fact in sample_bank_latest_facts()
        if fact.fact_group
        not in ("loan_exposure", "credit_exposure", "off_balance", "crm_collateral")
    ]
    facts.extend(
        [
            ForecastFact("loan_exposure", "sme_retail", Decimal("2000"), risk_weight_code="RW100"),
            ForecastFact(
                "credit_exposure", "sme_retail:RW100", Decimal("1000"), risk_weight_code="RW100"
            ),
            ForecastFact(
                "credit_exposure",
                "sme_retail:RW100+RW20",
                Decimal("1000"),
                risk_weight_code="RW100+RW20",
            ),
            ForecastFact(
                "credit_exposure", "other_assets:RW100", Decimal("1000"), risk_weight_code="RW100"
            ),
            ForecastFact("crm_collateral", "sme_retail:CORPORATE_DEBT", Decimal(collateral)),
        ]
    )
    paths = tuple(
        replace(point, stress_value=point.base_value * Decimal("1.1"))
        if point.variable == "fx_usd_ghs" and point.year_index > 0
        else point
        for point in base_paths()
    )
    inputs = EnterpriseProjectionInputs(
        scenario_code="FX",
        scenario_paths=paths,
        facts=facts,
        params=replace(bog_forecast_params(), capital=params),
        plan=replace(BASE_ASSUMPTIONS, loan_growth_pct=Decimal(growth)),
        credit_exposures=book,
    )
    projection = project_enterprise(inputs)
    for year in range(1, 4):
        factor = (Decimal("1") + Decimal(growth) / Decimal("100")) ** year
        domestic = Decimal("1000") * factor - Decimal(collateral)
        foreign = Decimal("1200") * factor * Decimal("1.1")
        row = projection.stress[year - 1]
        assert row.rwa.credit_rwa == domestic + foreign + Decimal("1000")
        lines = {
            item.line_code: item for item in row.rwa.line_items if item.section == "credit_rwa"
        }
        assert lines["sme_retail:RW100"].weighted_amount == domestic
        assert lines["sme_retail:RW100+RW20"].weighted_amount == foreign
        assert lines["sme_retail:RW100+RW20"].exposure_amount == Decimal("1100") * factor
        assert lines["sme_retail:RW100+RW20"].rate_pct == Decimal("120")
        assert lines["other_assets:RW100"].weighted_amount == Decimal("1000")
        assert projection.base[year - 1].rwa.credit_rwa == Decimal("2200") * factor - Decimal(
            collateral
        ) + Decimal("1000")


@pytest.mark.parametrize(
    "claim",
    [
        ("LOAN", "CORPORATE", "Loan", "1"),
        ("SECURITY_HOLDING", "CORPORATE", "Security", "1"),
        ("INTERBANK_PLACEMENT", "BANK_NON_OECD", "Placement", "0.5"),
    ],
)
@pytest.mark.parametrize(
    "deduction_spec",
    [
        ("specific_provision_ghs", "impairment", "200"),
        ("interest_in_suspense_ghs", "suspended interest", "50"),
    ],
)
def test_covered_claim_gl_counterparts_are_netted_once_with_unrelated_impairment_retained(
    claim: tuple[str, str, str, str],
    deduction_spec: tuple[str, str, str],
) -> None:
    """BoG CRD (June 2018) ¶98: covered claim deduction appears once; equipment stays net."""
    position_type, counterparty, label, weight = claim
    deduction, name, amount = deduction_spec
    row = _row(
        "CLAIM/1",
        position_type,
        balance="1000",
        counterparty_type=counterparty,
        regulatory_category="CORPORATE_UNRATED",
        attributes={deduction: amount},
    )
    accounts = [
        CanonicalGlAccount(
            account_code="GL-1900",
            name=f"{label} {name}",
            account_class="ASSET",
            balance=-Decimal(amount),
        ),
        CanonicalGlAccount(
            account_code="GL-1700", name="Equipment", account_class="ASSET", balance=Decimal("1000")
        ),
        CanonicalGlAccount(
            account_code="GL-1790",
            name="Equipment impairment",
            account_class="ASSET",
            balance=Decimal("-200"),
        ),
    ]
    assert _credit_rwa(row, gl_accounts=accounts) == (Decimal("1000") - Decimal(amount)) * Decimal(
        weight
    ) + Decimal("800")


def test_fully_provided_claim_has_zero_rwa_increment_but_gross_expected_loss() -> None:
    """BoG CRD (June 2018) ¶98: fully specifically provided exposure has a known zero increment."""
    row = _row(
        "SME/1",
        "LOAN",
        balance="1000",
        regulatory_category="SME_UNRATED",
        attributes={"specific_provision_ghs": "1000"},
    )
    book = enterprise_stress._build_credit_exposures(
        [_stress_row(row)], bog_capital_params(), capital_facts=()
    )
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("2"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0.1")
    )
    assert result.base_credit_rwa == result.stressed_credit_rwa == Decimal("0")
    assert result.credit_rwa_uplift_factor == Decimal("1")
    assert result.base_expected_loss == Decimal("9")
    assert result.stressed_expected_loss == Decimal("18")
    uplift, _ = enterprise_stress._credit_overlays(
        book, list(base_paths()), 3, base_credit_rwa=Decimal("1000")
    )
    assert set(uplift.values()) == {Decimal("1")}

    facts = [
        fact
        for fact in sample_bank_latest_facts()
        if fact.fact_group not in ("loan_exposure", "credit_exposure", "off_balance")
    ]
    facts.extend(
        [
            ForecastFact("loan_exposure", "sme_retail", Decimal("1000"), risk_weight_code="RW100"),
            ForecastFact(
                "credit_exposure", "sme_retail:RW100", Decimal("0"), risk_weight_code="RW100"
            ),
            ForecastFact(
                "credit_exposure", "other_assets:RW100", Decimal("1000"), risk_weight_code="RW100"
            ),
        ]
    )
    projection = project_enterprise(
        EnterpriseProjectionInputs(
            scenario_code="provided",
            scenario_paths=base_paths(),
            facts=facts,
            params=bog_forecast_params(),
            plan=BASE_ASSUMPTIONS,
            credit_exposures=book,
        )
    )
    assert {year.rwa.credit_rwa for year in (*projection.base, *projection.stress)} == {
        Decimal("1000")
    }


def test_reverse_stress_stale_basis_is_actionable_and_creates_no_frontier(
    db_session: Session,
) -> None:
    """BoG CRD (June 2018) ¶98: reverse stress refuses stale facts before storing a frontier."""
    seed_book(db_session)
    derived = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    grant_organization_analyst(
        db_session,
        ModuleScope.FORECASTING,
        SensitivityScope.CONFIDENTIAL,
        grantor="credit-basis-test",
        reason="Exercise reverse stress stale-basis refusal.",
    )
    db_session.execute(
        delete(BankFinancialFact).where(
            BankFinancialFact.bank_id == derived.bank_id,
            BankFinancialFact.fact_group == "credit_exposure",
        )
    )
    db_session.flush()
    with pytest.raises(HTTPException) as refusal:
        reverse_stress.run_reverse_stress(
            db_session,
            MAKER,
            derived.bank_id,
            ReverseStressRunCreate(reporting_period_id=derived.reporting_period_id),
        )
    assert refusal.value.status_code == 409
    detail = cast(dict[str, str], refusal.value.detail)
    assert detail["error_code"] == "credit_exposure_basis_missing"
    assert "net CRD credit exposure basis" in detail["message"]
    assert (
        db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.module == "reverse_stress"))
        is None
    )
