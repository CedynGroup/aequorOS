"""BoG CRD (June 2018) ¶98, ¶123, ¶139: share the net credit and CRM basis."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import TypedDict
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    ParamCrmHaircut,
    ParamEclAssumption,
)
from app.services.fact_derivation import (
    _derive_specs,  # pyright: ignore[reportPrivateUsage]
    _load_canonical,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.test_enterprise_stress import RUNS_URL
from tests.api.test_enterprise_stress_phase4 import (
    _AS_OF,  # pyright: ignore[reportPrivateUsage]
)
from tests.fixtures.capital_structure import MAKER
from tests.services.test_crd_stress_basis_reconciliation import (
    _prepare_credit_basis_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.helpers import headers

pytestmark = [
    pytest.mark.requirement("BoG CRD (June 2018) ¶98, ¶123, ¶139"),
    pytest.mark.usefixtures("capital_run_authority", "fx_run_authority", "irrbb_run_authority"),
]


class _BottomUp(TypedDict):
    base_credit_rwa: str
    stressed_credit_rwa: str


class _Outcome(TypedDict):
    bottom_up_credit: _BottomUp


class _RwaRow(TypedDict):
    label: str
    credit_rwa: str


class _RwaTable(TypedDict):
    rows: list[_RwaRow]


class _Appendix(TypedDict):
    table5_rwa: _RwaTable


class _Run(TypedDict):
    outcome: _Outcome
    appendix_ii: _Appendix


class _CapitalRwa(TypedDict):
    credit_rwa_ghs: str


def _configure_credit_book(db_session: Session, bank_id: str, book: str) -> None:
    snapshots = list(
        db_session.scalars(
            select(CanonicalPositionSnapshot).where(CanonicalPositionSnapshot.bank_id == bank_id)
        )
    )
    for snapshot in snapshots:
        position = db_session.get(CanonicalPosition, snapshot.position_id)
        assert position is not None
        if position.position_type not in ("LOAN", "INTERBANK_PLACEMENT"):
            continue
        keep = "IBP/PEER" if book == "bank" else "LOAN/CORP1"
        if position.source_reference != keep and not (
            book == "split_sme" and position.source_reference == "LOAN/CORP2"
        ):
            db_session.delete(snapshot)
            db_session.flush()
            db_session.delete(position)
            continue
        snapshot.balance = Decimal("1000")
        snapshot.attributes = {"balance_ghs": "1000"}
        if position.source_reference == keep:
            snapshot.attributes.update(
                {
                    "crm_collateral_ghs": "1500" if book == "split_sme" else "200",
                    "crm_collateral_class": "cash",
                }
            )
        elif book == "split_sme":
            position.currency = "USD"
        if book == "bank":
            product = db_session.scalar(
                select(CanonicalProduct).where(
                    CanonicalProduct.bank_id == bank_id,
                    CanonicalProduct.product_code == "LN.CORP",
                )
            )
            assert product is not None
            snapshot.product_id = product.id
        else:
            assert snapshot.product_id is not None
            product = db_session.get(CanonicalProduct, snapshot.product_id)
            assert product is not None
            product.regulatory_category = "SME_UNRATED"
        if book == "provided_sme":
            snapshot.attributes["specific_provision_ghs"] = "200"


def _derive_credit_basis(db_session: Session, bank_id: str, haircut: str | None) -> UUID:
    bank = db_session.get(Bank, bank_id)
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == bank_id, BankReportingPeriod.period_end == _AS_OF
        )
    )
    assert bank is not None and period is not None
    db_session.execute(delete(ParamCrmHaircut))
    db_session.add_all(
        ParamEclAssumption(
            organization_id=bank.organization_id,
            jurisdiction_code=bank.jurisdiction_code,
            segment="ALL",
            stage=stage,
            pd_pct=Decimal("2"),
            lgd_pct=Decimal("45"),
            effective_from=_AS_OF,
            approved_by="Independent fixture checker",
            approval_timestamp=datetime.now(UTC),
        )
        for stage in (1, 2, 3)
    )
    if haircut is not None:
        db_session.add(
            ParamCrmHaircut(
                organization_id=bank.organization_id,
                jurisdiction_code=bank.jurisdiction_code,
                collateral_class="CASH",
                haircut_pct=Decimal(haircut),
                effective_from=_AS_OF,
                approved_by="Independent fixture checker",
                approval_timestamp=datetime.now(UTC),
            )
        )
    db_session.flush()
    canonical = _load_canonical(db_session, MAKER, bank, _AS_OF)
    specs, _, _ = _derive_specs(canonical, live=False)
    period.credit_source_basis = canonical.credit_source_basis
    groups = ("credit_exposure", "loan_exposure", "crm_collateral", "ecl_exposure")
    db_session.execute(
        delete(BankFinancialFact).where(
            BankFinancialFact.bank_id == bank_id,
            BankFinancialFact.reporting_period_id == period.id,
            BankFinancialFact.fact_group.in_(groups),
        )
    )
    db_session.add_all(
        BankFinancialFact(
            organization_id=bank.organization_id,
            bank_id=bank_id,
            reporting_period_id=period.id,
            fact_group=spec.fact_group,
            category=spec.category,
            amount=spec.amount,
            currency=bank.currency,
            risk_weight_code=spec.risk_weight_code,
            attributes=spec.attributes,
        )
        for spec in specs
        if spec.fact_group in groups
    )
    db_session.commit()
    return period.id


@pytest.mark.parametrize(
    ("book", "haircut", "growth", "expected"),
    [
        ("split_sme", None, "0", "600"),
        ("split_sme", "0", "0", "600"),
        ("split_sme", "20", "0", "960"),
        ("bank", "0", "0", "400"),
        ("provided_sme", "0", "10", "600"),
    ],
)
def test_enterprise_endpoint_preserves_capital_crm_in_both_projection_legs(  # noqa: PLR0913
    db_client: TestClient,
    db_session: Session,
    book: str,
    haircut: str | None,
    growth: str,
    expected: str,
) -> None:
    """BoG CRD (June 2018) ¶98, ¶123, ¶139: netting, CRM and weights agree across legs."""
    bank_id, payload = _prepare_credit_basis_run(db_client, db_session, "unchanged")
    _configure_credit_book(db_session, bank_id, book)
    period_id = _derive_credit_basis(db_session, bank_id, haircut)

    capital = db_client.post(
        f"/api/v1/banks/{bank_id}/regulatory-runs",
        headers=headers(),
        json={
            "module": "capital",
            "scenario_code": "baseline",
            "reporting_period_id": str(period_id),
        },
    )
    assert capital.status_code == 201, capital.text
    capital_rwa = db_client.get(
        f"/api/v1/banks/{bank_id}/capital/rwa?reporting_period_id={period_id}", headers=headers()
    )
    assert capital_rwa.status_code == 200, capital_rwa.text
    capital_amount = TypeAdapter(_CapitalRwa).validate_json(capital_rwa.text)["credit_rwa_ghs"]
    assert Decimal(capital_amount) == Decimal(expected)

    payload["plan"] = {
        "loan_growth_pct": growth,
        "deposit_growth_pct": "0",
        "securities_shift_pp": "0",
        "fx_depreciation_pct": "0",
    }
    response = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=payload)
    assert response.status_code == 201, response.text
    run = TypeAdapter(_Run).validate_json(response.text)
    assert Decimal(run["outcome"]["bottom_up_credit"]["base_credit_rwa"]) == Decimal(expected)
    assert Decimal(run["outcome"]["bottom_up_credit"]["stressed_credit_rwa"]) == Decimal(expected)
    projected = {
        row["label"]: Decimal(row["credit_rwa"]) * Decimal("1000")
        for row in run["appendix_ii"]["table5_rwa"]["rows"]
    }
    if growth == "0":
        assert set(projected.values()) == {Decimal(expected)}
    else:
        assert projected["current"] == Decimal(expected)
        for year in (1, 2, 3):
            amount = (Decimal("800") * Decimal("1.1") ** year - Decimal("200")).quantize(
                Decimal("1")
            )
            assert projected[f"base_y{year}"] == projected[f"stress_y{year}"] == amount
