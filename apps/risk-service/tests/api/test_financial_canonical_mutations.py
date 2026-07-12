from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import AuditEvent, FinancialInstitution, FinancialManualEditHistory
from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_1, ORG_2, headers


def test_resource_specific_manual_entry_and_correction_refresh_validation_and_history(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    create = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions",
        headers=headers(),
        json={"name": "Aequor Bank", "reason": "Missing source record"},
    )

    assert create.status_code == 200, create.text
    created = create.json()
    assert created["record"]["metadata"]["provenance"] == "manual"
    assert created["validation"]["case_id"] == str(case.id)

    correction = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions/{created['record']['id']}",
        headers=headers(),
        json={
            "name": "Aequor Commercial Bank",
            "reference_code": "ACB",
            "reason": "Reviewer verified legal name",
        },
    )

    assert correction.status_code == 200, correction.text
    corrected = correction.json()
    assert corrected["record"]["name"] == "Aequor Commercial Bank"
    assert corrected["record"]["metadata"]["provenance"] == "corrected"
    assert corrected["validation"]["issue_count"] == len(corrected["validation"]["issues"])

    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        edits = list(
            session.scalars(
                select(FinancialManualEditHistory).where(
                    FinancialManualEditHistory.record_id == UUID(corrected["record"]["id"])
                )
            )
        )
        correction_edits = [edit for edit in edits if edit.reason == "Reviewer verified legal name"]
        assert {edit.field_name for edit in correction_edits} == {
            "name",
            "reference_code",
            "metadata",
        }
        assert all(edit.edited_by is not None for edit in edits)
        assert session.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_id == UUID(corrected["record"]["id"]),
                AuditEvent.event_type == "financial_record.corrected",
            )
        )


def test_all_core_entity_manual_entry_contracts_are_available(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()

    institution = create_record(
        db_client,
        case.id,
        "institutions",
        {"name": "Core Bank", "reason": "manual"},
    )
    account = create_record(
        db_client,
        case.id,
        "accounts",
        {
            "institution_id": institution["id"],
            "account_name": "Operating",
            "account_type": "deposit",
            "currency": "usd",
            "reason": "manual",
        },
    )
    period = create_record(
        db_client,
        case.id,
        "reporting-periods",
        {
            "period_type": "as_of",
            "as_of_date": "2026-06-30",
            "label": "June 2026",
            "reason": "manual",
        },
    )
    balance = create_record(
        db_client,
        case.id,
        "balances",
        {
            "account_id": account["id"],
            "reporting_period_id": period["id"],
            "balance_type": "cash",
            "amount": "125.50",
            "currency": "usd",
            "reason": "manual",
        },
    )
    obligation = create_record(
        db_client,
        case.id,
        "obligations",
        {
            "institution_id": institution["id"],
            "account_id": account["id"],
            "reporting_period_id": period["id"],
            "obligation_type": "facility",
            "facility_type": "revolver",
            "principal_amount": "1000",
            "outstanding_amount": "100",
            "currency": "usd",
            "reason": "manual",
        },
    )

    assert account["currency"] == "USD"
    assert balance["currency"] == "USD"
    assert obligation["facility_type"] == "revolver"

    corrections = (
        ("accounts", account["id"], {"account_name": "Operating corrected"}),
        ("reporting-periods", period["id"], {"label": "June 2026 corrected"}),
        ("balances", balance["id"], {"amount": "150.75"}),
        ("obligations", obligation["id"], {"outstanding_amount": "125"}),
    )
    for resource, record_id, fields in corrections:
        response = db_client.patch(
            f"/api/v1/cases/{case.id}/financial-workspace/{resource}/{record_id}",
            headers=headers(),
            json={**fields, "reason": "Reviewer correction"},
        )
        assert response.status_code == 200, response.text
        field_name = next(iter(fields))
        actual = response.json()["record"][field_name]
        expected = next(iter(fields.values()))
        if field_name in {"amount", "outstanding_amount"}:
            assert Decimal(str(actual)) == Decimal(str(expected))
        else:
            assert actual == expected


def test_explicit_provenance_fields_record_the_persisted_history_value(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    institution = create_record(
        db_client,
        case.id,
        "institutions",
        {"name": "History Bank", "metadata": {"note": "source"}, "reason": "manual"},
    )
    obligation = create_record(
        db_client,
        case.id,
        "obligations",
        {
            "obligation_type": "loan",
            "details": {"note": "source"},
            "reason": "manual",
        },
    )

    institution_response = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions/{institution['id']}",
        headers=headers(),
        json={"metadata": {"note": "reviewed"}, "reason": "metadata correction"},
    )
    obligation_response = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/obligations/{obligation['id']}",
        headers=headers(),
        json={"details": {"note": "reviewed"}, "reason": "details correction"},
    )

    assert institution_response.status_code == 200, institution_response.text
    assert obligation_response.status_code == 200, obligation_response.text
    with get_sessionmaker()() as session:
        for record_id, field, reason, persisted in (
            (
                institution["id"],
                "metadata",
                "metadata correction",
                institution_response.json()["record"]["metadata"],
            ),
            (
                obligation["id"],
                "details",
                "details correction",
                obligation_response.json()["record"]["details"],
            ),
        ):
            history = session.scalar(
                select(FinancialManualEditHistory).where(
                    FinancialManualEditHistory.record_id == UUID(str(record_id)),
                    FinancialManualEditHistory.field_name == field,
                    FinancialManualEditHistory.reason == reason,
                )
            )
            assert history is not None
            assert history.new_value == persisted
            assert history.new_value["provenance"] == "corrected"


def test_obligation_identity_uses_type_and_updates_after_correction(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    common = {
        "principal_amount": "1000",
        "outstanding_amount": "500",
        "currency": "USD",
        "reason": "manual",
    }
    loan = create_record(
        db_client,
        case.id,
        "obligations",
        {**common, "obligation_type": "loan"},
    )
    facility = create_record(
        db_client,
        case.id,
        "obligations",
        {**common, "obligation_type": "facility"},
    )

    correction = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/obligations/{loan['id']}",
        headers=headers(),
        json={"obligation_type": "lease", "reason": "correct type"},
    )
    replacement = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/obligations",
        headers=headers(),
        json={**common, "obligation_type": "loan"},
    )

    assert facility["obligation_type"] == "facility"
    assert correction.status_code == 200, correction.text
    assert replacement.status_code == 200, replacement.text


def test_account_identity_uses_account_number_and_updates_after_correction(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    common = {
        "account_name": "Operating",
        "currency": "USD",
        "reason": "manual",
    }
    first = create_record(
        db_client,
        case.id,
        "accounts",
        {**common, "account_number": "111"},
    )
    second = create_record(
        db_client,
        case.id,
        "accounts",
        {**common, "account_number": "222"},
    )

    correction = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/accounts/{first['id']}",
        headers=headers(),
        json={"account_number": "333", "reason": "correct account number"},
    )
    replacement = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/accounts",
        headers=headers(),
        json={**common, "account_number": "111"},
    )

    assert first["id"] != second["id"]
    assert correction.status_code == 200, correction.text
    assert replacement.status_code == 200, replacement.text


def test_mutation_contracts_reject_database_overflow_and_nonnullable_covenant_nulls(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    institution_overflow = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions",
        headers=headers(),
        json={"name": "Bank", "institution_type": "x" * 121, "reason": "manual"},
    )
    balance_overflow = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/balances",
        headers=headers(),
        json={"balance_type": "cash", "amount": "12345678901234567.1234", "reason": "manual"},
    )
    interest_overflow = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/obligations",
        headers=headers(),
        json={"obligation_type": "loan", "interest_rate": "12345.123456", "reason": "manual"},
    )
    covenant = create_record(
        db_client,
        case.id,
        "covenants",
        {
            "name": "Leverage",
            "metric": "leverage",
            "operator": "lte",
            "threshold": "3.0",
            "reason": "manual",
        },
    )

    assert institution_overflow.status_code == 422
    assert balance_overflow.status_code == 422
    assert interest_overflow.status_code == 422
    for field in ("compliance_status", "source_record", "reporting_context", "metadata"):
        response = db_client.patch(
            f"/api/v1/cases/{case.id}/financial-workspace/covenants/{covenant['id']}",
            headers=headers(),
            json={field: None, "reason": "invalid null"},
        )
        assert response.status_code == 422, response.text


def test_covenant_contract_computes_and_validates_compliance(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    obligation = create_record(
        db_client,
        case.id,
        "obligations",
        {
            "obligation_type": "facility",
            "principal_amount": "1000",
            "outstanding_amount": "100",
            "currency": "USD",
            "reason": "manual",
        },
    )
    covenant_response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/covenants",
        headers=headers(),
        json={
            "obligation_id": obligation["id"],
            "name": "Minimum liquidity",
            "metric": "Liquidity Ratio",
            "operator": "gte",
            "threshold": "1.25",
            "actual_value": "1.50",
            "reason": "Entered from signed facility",
        },
    )

    assert covenant_response.status_code == 200, covenant_response.text
    covenant = covenant_response.json()["record"]
    assert covenant["metric"] == "liquidity ratio"
    assert covenant["compliance_status"] == "compliant"
    assert covenant["obligation_id"] == obligation["id"]

    correction = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/covenants/{covenant['id']}",
        headers=headers(),
        json={
            "compliance_status": "non_compliant",
            "reason": "Test declared status inconsistency",
        },
    )
    assert correction.status_code == 200, correction.text
    issues = correction.json()["validation"]["issues"]
    assert any(issue["code"] == "covenant_compliance_status_mismatch" for issue in issues)

    workspace = db_client.get(f"/api/v1/cases/{case.id}/financial-workspace", headers=headers())
    assert workspace.status_code == 200
    assert workspace.json()["covenants"][0]["id"] == covenant["id"]


def test_mutations_reject_unsupported_input_authorization_and_cross_tenant_access(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    unsupported_type = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/derivatives",
        headers=headers(),
        json={},
    )
    unsupported_field = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions",
        headers=headers(),
        json={"name": "Bank", "secret": "no", "reason": "manual"},
    )
    missing_actor = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions",
        headers={"X-Org-Id": str(ORG_1)},
        json={"name": "Bank", "reason": "manual"},
    )
    cross_tenant = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions",
        headers=headers(ORG_2),
        json={"name": "Bank", "reason": "manual"},
    )
    created = create_record(
        db_client,
        case.id,
        "institutions",
        {"name": "Tenant-owned bank", "reason": "manual"},
    )
    cross_tenant_update = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions/{created['id']}",
        headers=headers(ORG_2),
        json={"name": "Wrong tenant", "reason": "manual"},
    )

    assert unsupported_type.status_code == 422
    assert unsupported_type.json()["error"]["details"]["code"] == (
        "unsupported_financial_entity_type"
    )
    assert unsupported_field.status_code == 422
    assert unsupported_field.json()["error"]["code"] == "validation_error"
    assert missing_actor.status_code == 422
    assert cross_tenant.status_code == 404
    assert cross_tenant_update.status_code == 404


def test_mutations_strip_reasons_and_reject_whitespace_only_values(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    blank = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions",
        headers=headers(),
        json={"name": "Blank Reason Bank", "reason": "   \t"},
    )
    created = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions",
        headers=headers(),
        json={"name": "Trimmed Reason Bank", "reason": "  reviewer verified  "},
    )

    assert blank.status_code == 422
    assert created.status_code == 200, created.text
    record_id = UUID(created.json()["record"]["id"])
    with get_sessionmaker()() as session:
        reasons = set(
            session.scalars(
                select(FinancialManualEditHistory.reason).where(
                    FinancialManualEditHistory.record_id == record_id
                )
            )
        )
    assert reasons == {"reviewer verified"}


def test_mutation_rolls_back_when_validation_refresh_fails(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = CaseFactory(db_client).create()

    def fail_validation(*args: object, **kwargs: object) -> None:
        raise RuntimeError("validation refresh failed")

    monkeypatch.setattr(
        "app.services.financial_canonical_edits.validate_financial_data",
        fail_validation,
    )
    response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/institutions",
        headers=headers(),
        json={"name": "Rolled Back Bank", "reason": "manual"},
    )

    assert response.status_code == 500
    with get_sessionmaker()() as session:
        assert session.scalar(
            select(FinancialInstitution).where(FinancialInstitution.name == "Rolled Back Bank")
        ) is None
        assert session.scalar(
            select(FinancialManualEditHistory).where(
                FinancialManualEditHistory.case_id == case.id,
                FinancialManualEditHistory.reason == "manual",
            )
        ) is None


def create_record(
    client: TestClient, case_id: UUID, resource: str, payload: dict[str, object]
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/cases/{case_id}/financial-workspace/{resource}",
        headers=headers(),
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()["record"]
