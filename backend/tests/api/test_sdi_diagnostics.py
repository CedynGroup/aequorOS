from __future__ import annotations

from app.db.session import get_sessionmaker
from app.models import Bank
from tests.api.helpers import ORG_1, headers


def _create_sdi_bank() -> str:
    session = get_sessionmaker()()
    try:
        bank = Bank(
            organization_id=ORG_1,
            name="SDI Diagnostics",
            short_name="SDID",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="savings_and_loans",
            institution_type="savings_and_loans",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


def _create_universal_bank() -> str:
    session = get_sessionmaker()()
    try:
        bank = Bank(
            organization_id=ORG_1,
            name="Universal Loan Diagnostics",
            short_name="ULD",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type="universal_bank",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


def test_sdi_liquidity_position_returns_typed_unavailable_controls(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/liquidity-position", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["ratios"]) == 8
    assert all(row["status"] == "not_computable" for row in body["ratios"])
    assert len(body["reserves"]) == 2
    assert all(row["status"] == "not_computable" for row in body["reserves"])
    assert body["maturity_ladder"]


def test_sdi_large_exposures_returns_empty_book_without_false_breach(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/large-exposures", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exposures"] == []
    assert body["findings"] == []


def test_sdi_capital_assurance_returns_explicit_filing_blockers(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/capital-assurance", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filing_status"] == "blocked"
    assert body["current"]["assessment_status"] == "not_computable"
    assert body["filing_blockers"]


def test_sdi_loan_classification_returns_raw_dpd_buckets(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/loan-classification", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dpd_covered_count"] == 0
    assert len(body["delinquency_buckets"]) == 7
    assert {metric["code"] for metric in body["portfolio_at_risk"]} == {
        "par_30",
        "par_60",
        "par_90",
        "par_180",
        "par_360",
    }


def test_universal_bank_loan_classification_uses_neutral_endpoint(db_client) -> None:  # noqa: ANN001
    bank_id = _create_universal_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/loan-classification", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_class"] == "bank"
    assert "olem" in {bucket["grade"] for bucket in body["buckets"]}
    assert len(body["delinquency_buckets"]) == 7