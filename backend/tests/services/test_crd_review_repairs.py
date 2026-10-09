"""BoG CRD (June 2018) ¶98, ¶106–107, ¶117–124: shared review regressions."""

from dataclasses import replace
from decimal import Decimal
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.domain.capital.engine import (
    CapitalFact,
    RiskWeightUnavailable,
    _credit_line_items,  # pyright: ignore[reportPrivateUsage]
    compute_rwa,
)
from app.domain.stress.appendix_ii import _crd_class  # pyright: ignore[reportPrivateUsage]
from app.domain.stress.credit_bottom_up import compute_bottom_up_credit
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalProduct,
    RegulatoryRun,
)
from app.services.enterprise_stress import (
    _build_credit_exposures,  # pyright: ignore[reportPrivateUsage]
)
from app.services.fact_derivation import (
    _derive_specs,  # pyright: ignore[reportPrivateUsage]
    _load_canonical,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.test_enterprise_stress import (
    RUNS_URL,
    _approve_scenario,  # pyright: ignore[reportPrivateUsage]
    _create_scenario,  # pyright: ignore[reportPrivateUsage]
    _period_id,  # pyright: ignore[reportPrivateUsage]
    _seed_checker,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.test_enterprise_stress_phase4 import (
    _AS_OF,  # pyright: ignore[reportPrivateUsage]
    _seed_canonical_positions,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.test_ingestion import seed_bank
from tests.domain.test_capital_engine import bog_capital_params
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.fixtures.capital_structure import MAKER, REPORTING_DATE
from tests.services.test_crd_credit_exposures import (
    _stress_row,  # pyright: ignore[reportPrivateUsage]
)
from tests.services.test_derivation_fail_closed_defaults import (
    _canonical,  # pyright: ignore[reportPrivateUsage]
    _row,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.helpers import headers


class _OutcomeDetail(TypedDict):
    state: str
    items: list[str]


class _Outcome(TypedDict):
    blocks_filing: bool
    details: list[_OutcomeDetail]


class _Details(TypedDict):
    outcome: _Outcome


class _ErrorDetail(TypedDict):
    details: _Details


class _HTTPError(TypedDict):
    details: _ErrorDetail


class _ErrorResponse(TypedDict):
    error: _HTTPError


pytestmark = pytest.mark.requirement("BoG CRD (June 2018) ¶98, ¶106–107, ¶117–124")


@pytest.mark.parametrize("position_type", ["LOAN", "SECURITY_HOLDING", "INTERBANK_PLACEMENT"])
@pytest.mark.parametrize("counterparty", [("CENTRAL_BANK", "bog"), ("SOVEREIGN", "gog")])
@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("domicile", ["domestic", "foreign_country", "nonresident"])
def test_typed_domestic_identity_reaches_capital_and_stress(
    position_type: str, counterparty: tuple[str, str], foreign: bool, live: bool, domicile: str
) -> None:
    """BoG CRD (June 2018) ¶106–107: typed domestic claims carry 0%/20%, conflicts refuse."""
    counterparty_type, role = counterparty
    row = replace(
        _row(
            "TYPED/1",
            position_type,
            balance="1000",
            currency="USD" if foreign else "GHS",
            counterparty_type=counterparty_type,
        ),
        counterparty_country="NG" if domicile == "foreign_country" else "GH",
        counterparty_resident=domicile != "nonresident",
    )
    specs, _, _ = _derive_specs(_canonical(row), live=live)
    facts = [
        CapitalFact(spec.fact_group, spec.category, spec.amount, spec.risk_weight_code)
        for spec in specs
    ]
    if domicile != "domestic":
        with pytest.raises(RiskWeightUnavailable):
            _credit_line_items(facts, bog_capital_params())
        return
    lines = _credit_line_items(facts, bog_capital_params())
    line = next(line for line in lines if line.exposure_amount == Decimal("1000"))
    assert line.weighted_amount == Decimal("200" if foreign else "0")
    assert _crd_class(line) == role
    book = _build_credit_exposures(
        [_stress_row(row)], bog_capital_params(), domestic_country="GH", capital_facts=facts
    )
    assert book[0].crd_class == role
    assert book[0].risk_weight_pct == Decimal("20" if foreign else "0")


@pytest.mark.parametrize("position_type", ["LOAN", "SECURITY_HOLDING", "INTERBANK_PLACEMENT"])
@pytest.mark.parametrize("issuer,role", [("Bank of Ghana", "bog"), ("Government of Ghana", "gog")])
@pytest.mark.parametrize("live", [False, True])
def test_registry_issuer_name_preserves_the_role_in_capital_and_loss_outputs(
    position_type: str, issuer: str, role: str, live: bool
) -> None:
    """BoG CRD (June 2018) ¶107: the same 20% weight retains distinct GoG/BoG reporting roles."""
    row = _row(
        "ISSUER/1",
        position_type,
        balance="1000",
        currency="USD",
        attributes={"issuer": issuer, "pd_pct": "2", "lgd_pct": "45"},
    )
    canonical = _canonical(row)
    canonical = replace(
        canonical, central_bank_names=replace(canonical.central_bank_names, full=("bank of ghana",))
    )
    specs, _, _ = _derive_specs(canonical, live=live)
    facts = [
        CapitalFact(spec.fact_group, spec.category, spec.amount, spec.risk_weight_code)
        for spec in specs
    ]
    line = next(
        line
        for line in _credit_line_items(facts, bog_capital_params())
        if line.exposure_amount == Decimal("1000")
    )
    assert line.weighted_amount == Decimal("200")
    assert _crd_class(line) == role
    book = _build_credit_exposures(
        [_stress_row(row)],
        bog_capital_params(),
        sovereign_names=canonical.sovereign_issuer_names,
        central_bank_names=canonical.central_bank_names.full,
        domestic_country="GH",
        capital_facts=facts,
    )
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0")
    )
    assert book[0].crd_class == role
    assert result.base_expected_loss == Decimal("9")
    assert {impact.exposure_class for impact in result.by_class} == {role}


@pytest.mark.usefixtures("fx_run_authority", "irrbb_run_authority")
@pytest.mark.parametrize("failure", ["foreign_pse", "stale_bucket"])
def test_enterprise_expected_credit_refusals_return_409_without_creating_runs(
    db_client: TestClient, db_session: Session, failure: str
) -> None:
    """BoG CRD (June 2018) ¶98, ¶120–124: unassessed or stale credit cannot mint stress runs."""
    bank_id = seed_bank(db_client)
    _seed_canonical_positions(bank_id)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker(db_client)
    scenario_id = _create_scenario(db_client, code=f"refusal_{failure}")
    _approve_scenario(db_client, scenario_id, checker)
    session = db_session
    product = session.scalar(
        select(CanonicalProduct).where(
            CanonicalProduct.bank_id == bank_id, CanonicalProduct.product_code == "LN.CORP"
        )
    )
    assert product is not None
    if failure == "stale_bucket":
        product.regulatory_category = "SME_UNRATED"
    else:
        cp = session.scalar(
            select(CanonicalCounterparty).where(
                CanonicalCounterparty.bank_id == bank_id,
                CanonicalCounterparty.source_reference == "CP/BIGCORP",
            )
        )
        assert cp is not None
        cp.counterparty_type = "GOVERNMENT_ENTITY"
        cp.country_code = "NG"
        cp.resident = False
        cp.attributes = {"borrower_class": "public_institution"}
        bank = session.get(Bank, bank_id)
        period = session.scalar(
            select(BankReportingPeriod).where(
                BankReportingPeriod.bank_id == bank_id, BankReportingPeriod.period_end == _AS_OF
            )
        )
        assert bank is not None and period is not None
        session.flush()
        specs, _, _ = _derive_specs(_load_canonical(session, MAKER, bank, _AS_OF), live=False)
        session.execute(
            delete(BankFinancialFact).where(
                BankFinancialFact.bank_id == bank_id,
                BankFinancialFact.reporting_period_id == period.id,
                BankFinancialFact.fact_group == "credit_exposure",
            )
        )
        session.add_all(
            [
                BankFinancialFact(
                    organization_id=bank.organization_id,
                    bank_id=bank_id,
                    reporting_period_id=period.id,
                    fact_group=spec.fact_group,
                    category=spec.category,
                    amount=spec.amount,
                    currency=bank.currency,
                    risk_weight_code=spec.risk_weight_code,
                    attributes={},
                )
                for spec in specs
                if spec.fact_group == "credit_exposure"
            ]
        )
    session.commit()
    response = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Exercise the declared credit refusal.",
        },
    )
    assert response.status_code == 409, response.text
    detail = TypeAdapter(_ErrorResponse).validate_json(response.text)["error"]["details"]
    outcome = detail["details"]["outcome"]
    assert outcome["blocks_filing"] is True
    assert outcome["details"][0]["items"] == [
        "exposure:loans:pse_public_institution:unclassified"
        if failure == "foreign_pse"
        else "fact:credit_exposure:sme_retail:RW100"
    ]
    assert outcome["details"][0]["state"] == "missing_required_input"
    session = db_session
    assert (
        session.scalar(
            select(func.count())
            .select_from(RegulatoryRun)
            .where(RegulatoryRun.bank_id == bank_id, RegulatoryRun.module == "enterprise_stress")
        )
        == 0
    )


def test_shared_synthetic_fixture_supplies_the_crd_basis_without_changing_accounting(
    db_session: Session,
) -> None:
    """BoG CRD (June 2018) ¶98, ¶106, ¶139: net credit facts preserve gross accounting."""
    materialize_canonical_test_book(db_session)
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period is not None
    rows = list(
        db_session.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.bank_id == SAMPLE_BANK_ID,
                BankFinancialFact.reporting_period_id == period.id,
            )
        )
    )
    gross = next(
        row for row in rows if row.fact_group == "balance_sheet" and row.category == "loans_gross"
    )
    assert gross.amount == Decimal("1400000000")
    credit = {row.category: row for row in rows if row.fact_group == "credit_exposure"}
    assert credit["sme_retail:RW100"].amount == Decimal("280000000")
    assert credit["sme_retail:RW100"].risk_weight_code == "RW100"
    assert credit["past_due_90:RW150"].amount == Decimal("50000000")
    facts = [
        CapitalFact(
            row.fact_group,
            row.category,
            row.amount,
            row.risk_weight_code,
            ccf_pct=row.ccf_pct,
            income_year=row.income_year,
            capital_tier=row.capital_tier,
            is_deduction=row.is_deduction,
        )
        for row in rows
    ]
    assert compute_rwa(facts, bog_capital_params()).credit_rwa == Decimal("1472500000")


@pytest.mark.parametrize("role", ["bog", "gog"])
@pytest.mark.parametrize("named", [False, True])
def test_domestic_public_loan_uses_one_net_and_collateral_basis(role: str, named: bool) -> None:
    """BoG CRD (June 2018) ¶98, ¶107: netting and CRM preserve the domestic issuer role."""
    attributes = {
        "specific_provision_ghs": "100",
        "crm_collateral_ghs": "200",
        "crm_collateral_class": "corporate_debt",
    }
    if named:
        attributes["issuer"] = "Bank of Ghana" if role == "bog" else "Government of Ghana"
    row = replace(
        _row(
            "PUBLIC/CRM",
            "LOAN",
            balance="1000",
            currency="USD",
            counterparty_type=None if named else "CENTRAL_BANK" if role == "bog" else "SOVEREIGN",
            attributes=attributes,
        ),
        counterparty_country="GH",
        counterparty_resident=True,
    )
    canonical = _canonical(row)
    canonical = replace(
        canonical, central_bank_names=replace(canonical.central_bank_names, full=("bank of ghana",))
    )
    specs, _, _ = _derive_specs(canonical, live=False)
    facts = [
        CapitalFact(spec.fact_group, spec.category, spec.amount, spec.risk_weight_code)
        for spec in specs
    ]
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    line = next(
        line for line in _credit_line_items(facts, params) if line.rate_pct == Decimal("20")
    )
    assert line.exposure_amount == Decimal("700")
    assert line.weighted_amount == Decimal("140")
    assert _crd_class(line) == role
    book = _build_credit_exposures(
        [_stress_row(row)],
        params,
        sovereign_names=canonical.sovereign_issuer_names,
        central_bank_names=canonical.central_bank_names.full,
        domestic_country="GH",
        capital_facts=facts,
    )
    assert book[0].ead == Decimal("1000")
    assert book[0].credit_amount == Decimal("900")
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0")
    )
    assert result.base_credit_rwa == Decimal("140")
