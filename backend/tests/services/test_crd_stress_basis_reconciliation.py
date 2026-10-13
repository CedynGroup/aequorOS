"""BoG CRD (June 2018) ¶98, ¶123–124, ¶138–139: reconcile before stress growth."""

import json
from dataclasses import asdict, replace
from decimal import Decimal
from typing import TypedDict
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.domain.forecasting.engine import ForecastFact
from app.domain.stress.credit_bottom_up import compute_bottom_up_credit
from app.domain.stress.projection import (
    EnterpriseProjection,
    EnterpriseProjectionInputs,
    project_enterprise,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    RegulatoryRun,
)
from app.services import canonical_withdrawal, credit_exposure_book, enterprise_stress
from app.services.enterprise_stress import (
    _build_credit_exposures,  # pyright: ignore[reportPrivateUsage]
)
from app.services.fact_derivation import (
    _derive_specs,  # pyright: ignore[reportPrivateUsage]
    _load_canonical,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.test_enterprise_stress import (
    RUNS_URL,
    SCENARIO_URL,
    _approve_scenario,  # pyright: ignore[reportPrivateUsage]
    _period_id,  # pyright: ignore[reportPrivateUsage]
    _seed_checker,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.test_enterprise_stress_phase4 import (
    _AS_OF,  # pyright: ignore[reportPrivateUsage]
    _seed_canonical_positions,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.test_ingestion import seed_bank
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    base_paths,
    bog_forecast_params,
    sample_bank_latest_facts,
)
from tests.domain.test_capital_engine import bog_capital_params
from tests.fixtures.capital_structure import MAKER
from tests.services.test_crd_credit_exposures import (
    _stress_row,  # pyright: ignore[reportPrivateUsage]
)
from tests.services.test_crd_credit_stress_repairs import (
    _capital_facts,  # pyright: ignore[reportPrivateUsage]
)
from tests.services.test_crd_review_repairs import (
    _ErrorResponse,  # pyright: ignore[reportPrivateUsage]
)
from tests.services.test_derivation_fail_closed_defaults import (
    _row,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.helpers import USER_2, headers

pytestmark = pytest.mark.requirement("BoG CRD (June 2018) ¶98, ¶123–124, ¶138–139")


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("claim_type", ["LOAN", "INTERBANK_PLACEMENT"])
@pytest.mark.parametrize(
    "change", ["category", "increase", "decrease", "provision", "suspense", "weight"]
)
def test_changed_current_book_refuses_an_existing_official_bucket(
    claim_type: str, change: str, foreign: bool
) -> None:
    """BoG CRD (June 2018) ¶98, ¶123–124, ¶138: changes cannot masquerade as growth."""
    first = _row("A", "LOAN", balance="1000", regulatory_category="CORPORATE_UNRATED")
    second = replace(first, source_reference="B")
    bank = _row("BANK", claim_type, balance="1000", counterparty_type="BANK_NON_OECD")
    if foreign:
        first, second, bank = (replace(row, currency="USD") for row in (first, second, bank))
    rows = [first, second, bank]
    official_source = json.dumps([asdict(row) for row in rows], default=str, sort_keys=True)
    changed = first if claim_type == "LOAN" else bank
    if change == "category":
        changed = replace(
            changed,
            counterparty_type="BANK_NON_OECD",
            attributes={"external_rating_grade": "1"} if claim_type != "LOAN" else {},
        )
    elif change in ("increase", "decrease"):
        amount = Decimal("1100" if change == "increase" else "900")
        changed = replace(changed, balance=amount, balance_ghs=amount)
    elif change in ("provision", "suspense"):
        key = "specific_provision_ghs" if change == "provision" else "interest_in_suspense_ghs"
        changed = replace(changed, attributes={key: "100"})
    else:
        official_source = json.dumps(
            [{**asdict(row), "product_risk_weight_code": "RW20"} for row in rows],
            default=str,
            sort_keys=True,
        )
    current = [changed, second, bank] if claim_type == "LOAN" else [first, second, changed]
    with pytest.raises(NotComputable) as refused:
        credit_exposure_book.require_credit_source_basis(
            official_source,
            json.dumps([asdict(row) for row in current], default=str, sort_keys=True),
        )
    assert refused.value.details[0].state is OutcomeState.RECONCILIATION_FAILED
    assert "Re-derive the official facts" in str(refused.value)
    assert refused.value.details[0].items
    current_source = json.dumps([asdict(row) for row in current], default=str, sort_keys=True)
    credit_exposure_book.require_credit_source_basis(current_source, current_source)
    refreshed = _capital_facts(current)
    book = _build_credit_exposures(
        [_stress_row(row) for row in current], bog_capital_params(), capital_facts=refreshed
    )
    result = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0")
    )
    expected = {
        "category": "2000" if claim_type == "LOAN" else "2200",
        "increase": "2600" if claim_type == "LOAN" else "2550",
        "decrease": "2400" if claim_type == "LOAN" else "2450",
        "provision": "2400" if claim_type == "LOAN" else "2450",
        "suspense": "2400" if claim_type == "LOAN" else "2450",
        "weight": "2500",
    }
    assert result.base_credit_rwa == result.stressed_credit_rwa == Decimal(expected[change])


@pytest.mark.parametrize("net", ["0", "800"])
@pytest.mark.parametrize("growth", ["0", "10"])
def test_reconciled_net_book_preserves_crm_and_legitimate_projection_growth(
    net: str, growth: str
) -> None:
    """BoG CRD (June 2018) ¶98, ¶139: compare net basis before CRM and scenario growth."""
    row = _row(
        "SME",
        "LOAN",
        balance="1000",
        regulatory_category="SME_UNRATED",
        attributes={
            "specific_provision_ghs": str(Decimal("1000") - Decimal(net)),
            "crm_collateral_ghs": "200",
            "crm_collateral_class": "corporate_debt",
        },
    )
    params = replace(bog_capital_params(), crm_haircuts={"CORPORATE_DEBT": Decimal("0")})
    official = _capital_facts([row])
    book = _build_credit_exposures([_stress_row(row)], params, capital_facts=official)
    assert book[0].ead == Decimal("1000")
    assert book[0].credit_amount == Decimal(net)
    bottom_up = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0")
    )
    expected = max(Decimal(net) - Decimal("200"), Decimal("0"))
    assert bottom_up.base_credit_rwa == expected
    facts = [
        fact
        for fact in sample_bank_latest_facts()
        if fact.fact_group in ("capital_component", "operational_income")
    ]
    facts.extend(
        [
            ForecastFact("balance_sheet", "cash_vault", Decimal("1000"), side="asset"),
            ForecastFact(
                "balance_sheet", "retail_deposits_stable", Decimal("1000"), side="liability"
            ),
        ]
    )
    facts.extend(
        ForecastFact(
            fact.fact_group, fact.category, fact.amount, risk_weight_code=fact.risk_weight_code
        )
        for fact in official
        if fact.fact_group in ("credit_exposure", "loan_exposure", "crm_collateral")
    )
    projection = project_enterprise(
        EnterpriseProjectionInputs(
            scenario_code="reconciled",
            scenario_paths=base_paths(),
            facts=facts,
            params=replace(bog_forecast_params(), capital=params),
            plan=replace(
                BASE_ASSUMPTIONS, loan_growth_pct=Decimal(growth), fx_depreciation_pct=Decimal("0")
            ),
            credit_exposures=book,
        )
    )
    assert projection.current.rwa.credit_rwa == expected
    for year in (*projection.base, *projection.stress):
        factor = (Decimal("1") + Decimal(growth) / Decimal("100")) ** year.year
        assert year.rwa.credit_rwa == max(Decimal(net) * factor - Decimal("200"), Decimal("0"))


@pytest.mark.parametrize("missing", [True, False])
def test_zero_net_category_presence_is_part_of_the_official_basis(missing: bool) -> None:
    """BoG CRD (June 2018) ¶98: zero-net claims retain their capital category."""
    row = _row(
        "PROVIDED",
        "LOAN",
        balance="1000",
        regulatory_category="SME_UNRATED",
        attributes={"specific_provision_ghs": "1000"},
    )
    official = _capital_facts([row])
    if missing:
        with pytest.raises(NotComputable):
            credit_exposure_book.require_credit_source_basis(
                "[]", json.dumps([asdict(row)], default=str, sort_keys=True)
            )
    else:
        book = _build_credit_exposures(
            [_stress_row(row)], bog_capital_params(), capital_facts=official
        )
        assert book[0].credit_amount == Decimal("0")


@pytest.mark.parametrize("official", [None, "[]"])
def test_empty_source_book_requires_matching_recorded_provenance(official: str | None) -> None:
    """BoG CRD (June 2018) ¶98: empty sources are distinct from unknown historic sources."""
    if official is None:
        with pytest.raises(NotComputable) as refused:
            credit_exposure_book.require_credit_source_basis(official, "[]")
        assert refused.value.details[0].state is OutcomeState.RECONCILIATION_FAILED
    else:
        credit_exposure_book.require_credit_source_basis(official, "[]")


class _BottomUp(TypedDict):
    base_credit_rwa: str
    stressed_credit_rwa: str


class _Outcome(TypedDict):
    bottom_up_credit: _BottomUp


class _Run(TypedDict):
    outcome: _Outcome


def _refresh_credit_basis(session: Session, bank_id: str) -> None:
    bank = session.get(Bank, bank_id)
    period = session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == bank_id, BankReportingPeriod.period_end == _AS_OF
        )
    )
    assert bank is not None and period is not None
    session.flush()
    canonical = _load_canonical(session, MAKER, bank, _AS_OF)
    specs, _, _ = _derive_specs(canonical, live=False)
    period.credit_source_basis = canonical.credit_source_basis
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


def _assert_neutral_rwa(run: _Run, projection: EnterpriseProjection, expected: str) -> None:
    amount = Decimal(expected)
    assert Decimal(run["outcome"]["bottom_up_credit"]["base_credit_rwa"]) == amount
    assert Decimal(run["outcome"]["bottom_up_credit"]["stressed_credit_rwa"]) == amount
    assert {
        year.rwa.credit_rwa for year in (projection.current, *projection.base, *projection.stress)
    } == {amount}


def _observe_projection(monkeypatch: pytest.MonkeyPatch) -> list[EnterpriseProjection]:
    projections: list[EnterpriseProjection] = []

    def project(inputs: EnterpriseProjectionInputs) -> EnterpriseProjection:
        result = project_enterprise(inputs)
        projections.append(result)
        return result

    monkeypatch.setattr(enterprise_stress, "project_enterprise", project)
    return projections


def _prepare_credit_basis_run(
    db_client: TestClient, db_session: Session, change: str
) -> tuple[str, dict[str, object]]:
    bank_id = seed_bank(db_client)
    _seed_canonical_positions(bank_id)
    snapshots = list(
        db_session.scalars(
            select(CanonicalPositionSnapshot).where(CanonicalPositionSnapshot.bank_id == bank_id)
        )
    )
    for snapshot in snapshots:
        position = db_session.get(CanonicalPosition, snapshot.position_id)
        assert position is not None
        if position.source_reference == "LOAN/USD" or (
            change == "withdrawal"
            and position.position_type in ("LOAN", "INTERBANK_PLACEMENT")
            and position.source_reference != "LOAN/CORP1"
        ):
            db_session.delete(snapshot)
            db_session.flush()
            db_session.delete(position)
        elif position.position_type in ("LOAN", "INTERBANK_PLACEMENT"):
            position.position_type = "LOAN"
            snapshot.balance = Decimal("1000")
            snapshot.attributes = {"balance_ghs": "1000"}
            snapshot.ifrs9_stage = 1
    db_session.execute(
        delete(BankFinancialFact).where(
            BankFinancialFact.bank_id == bank_id, BankFinancialFact.fact_group == "off_balance"
        )
    )
    _refresh_credit_basis(db_session, bank_id)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker(db_client)
    scenario = db_client.post(
        SCENARIO_URL,
        headers=headers(),
        json={
            "code": "neutral_basis",
            "name": "Neutral reconciliation",
            "scenario_type": "adverse",
            "severity": "severe",
            "horizon_years": 3,
            "narrative": "Neutral macro paths isolate the net credit basis.",
            "source": "CRD regression",
            "paths": [
                {
                    "variable": point.variable,
                    "year_index": point.year_index,
                    "base_value": str(point.base_value),
                    "stress_value": str(point.base_value),
                }
                for point in base_paths()
                if point.year_index > 0
            ],
            "reason": "Exercise basis reconciliation.",
        },
    )
    assert scenario.status_code == 201, scenario.text
    scenario_id = TypeAdapter(dict[str, object]).validate_json(scenario.text)["id"]
    assert isinstance(scenario_id, str)
    _approve_scenario(db_client, scenario_id, checker)
    payload: dict[str, object] = {
        "scenario_id": scenario_id,
        "reporting_period_id": period_id,
        "include_irr": False,
        "include_fx": False,
        "plan": {
            "loan_growth_pct": "0",
            "deposit_growth_pct": "0",
            "securities_shift_pp": "0",
            "fx_depreciation_pct": "0",
        },
        "reason": "Compare neutral credit results.",
    }
    return bank_id, payload


def _change_credit_source(db_session: Session, bank_id: str, change: str) -> None:
    snapshot = db_session.scalar(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.bank_id == bank_id,
            CanonicalPositionSnapshot.source_reference == "LOAN/CORP1",
        )
    )
    bank_counterparty = db_session.scalar(
        select(CanonicalCounterparty).where(
            CanonicalCounterparty.bank_id == bank_id,
            CanonicalCounterparty.source_reference == "CP/PEERBANK",
        )
    )
    assert snapshot is not None and bank_counterparty is not None
    if change == "counterparty":
        snapshot.counterparty_id = bank_counterparty.id
    elif change == "amount":
        snapshot.balance = Decimal("1100")
        snapshot.attributes = {"balance_ghs": "1100"}
    elif change == "replacement":
        replacement = CanonicalPositionSnapshot(
            id=uuid4(),
            organization_id=snapshot.organization_id,
            bank_id=snapshot.bank_id,
            as_of_date=snapshot.as_of_date,
            source_system=snapshot.source_system,
            source_reference=snapshot.source_reference,
            ingestion_batch_id=snapshot.ingestion_batch_id,
            lineage_id=snapshot.lineage_id,
            validation_status=snapshot.validation_status,
            position_id=snapshot.position_id,
            counterparty_id=snapshot.counterparty_id,
            product_id=snapshot.product_id,
            balance=snapshot.balance,
            notional=snapshot.notional,
            interest_rate=snapshot.interest_rate,
            rate_type=snapshot.rate_type,
            contractual_maturity=snapshot.contractual_maturity,
            next_repricing_date=snapshot.next_repricing_date,
            ifrs9_stage=snapshot.ifrs9_stage,
            attributes=dict(snapshot.attributes),
        )
        snapshot.superseded_by = replacement.id
        db_session.flush()
        db_session.add(replacement)
    elif change == "withdrawal":
        bank = db_session.get(Bank, bank_id)
        assert bank is not None
        withdrawal = canonical_withdrawal.request_withdrawal(
            db_session,
            MAKER,
            bank,
            entity="position",
            source_system=snapshot.source_system,
            as_of_date=_AS_OF,
            position_type="LOAN",
            reason="Retire the final accepted loan.",
            requested_by="maker@bank.test",
        )
        approved = canonical_withdrawal.approve_withdrawal(
            db_session,
            TenantContext(
                organization_id=MAKER.organization_id, actor_user_id=USER_2, authorization_version=1
            ),
            bank,
            withdrawal.id,
            approved_by="checker@bank.test",
        )
        assert approved.status == "applied"
    db_session.commit()


@pytest.mark.usefixtures("fx_run_authority", "irrbb_run_authority")
@pytest.mark.parametrize(
    "change", ["counterparty", "amount", "replacement", "withdrawal", "unchanged"]
)
def test_reingested_corporate_as_bank_refuses_until_official_rederivation(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """BoG CRD (June 2018) ¶98, ¶123, ¶138: 2,500 versus 2,000 RWA requires re-derivation."""
    projections = _observe_projection(monkeypatch)
    bank_id, payload = _prepare_credit_basis_run(db_client, db_session, change)
    initial = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=payload)
    assert initial.status_code == 201, initial.text
    initial_rwa = "1000" if change == "withdrawal" else "2500"
    _assert_neutral_rwa(TypeAdapter(_Run).validate_json(initial.text), projections[-1], initial_rwa)
    initial_run = TypeAdapter(dict[str, object]).validate_json(initial.text)
    initial_id = initial_run["run_id"]
    assert isinstance(initial_id, str)
    initial_stored = db_session.get(RegulatoryRun, UUID(initial_id))
    assert initial_stored is not None
    initial_hash = initial_stored.input_hash
    count_before = db_session.scalar(
        select(func.count())
        .select_from(RegulatoryRun)
        .where(RegulatoryRun.bank_id == bank_id, RegulatoryRun.module == "enterprise_stress")
    )
    _change_credit_source(db_session, bank_id, change)
    if change == "unchanged":
        repeat = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=payload)
        assert repeat.status_code == 201, repeat.text
        _assert_neutral_rwa(TypeAdapter(_Run).validate_json(repeat.text), projections[-1], "2500")
        return
    refused = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=payload)
    assert refused.status_code == 409, refused.text
    assert len(projections) == 1
    outcome = TypeAdapter(_ErrorResponse).validate_json(refused.text)["error"]["details"][
        "details"
    ]["outcome"]
    assert outcome["blocks_filing"] is True
    assert outcome["details"][0]["state"] == "reconciliation_failed"
    assert outcome["details"][0]["items"] == ["source:credit_book"]
    assert "Re-derive the official facts" in refused.text
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(RegulatoryRun)
            .where(RegulatoryRun.bank_id == bank_id, RegulatoryRun.module == "enterprise_stress")
        )
        == count_before
    )
    if change == "withdrawal":
        bank = db_session.get(Bank, bank_id)
        assert bank is not None
        book, basis = credit_exposure_book.load_credit_book(db_session, MAKER, bank, _AS_OF)
        assert book == [] and basis == "[]"
        return
    _refresh_credit_basis(db_session, bank_id)
    repaired = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=payload)
    assert repaired.status_code == 201, repaired.text
    assert len(projections) == 2
    expected_rwa = {"counterparty": "2000", "amount": "2600", "replacement": "2500"}[change]
    _assert_neutral_rwa(
        TypeAdapter(_Run).validate_json(repaired.text), projections[-1], expected_rwa
    )
    if change == "replacement":
        repaired_id = TypeAdapter(dict[str, object]).validate_json(repaired.text)["run_id"]
        assert isinstance(repaired_id, str)
        repaired_stored = db_session.get(RegulatoryRun, UUID(repaired_id))
        assert repaired_stored is not None
        assert repaired_stored.input_hash == initial_hash
