"""Real operator routes own examiner assignment and credential minting."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import Jurisdiction, OperatorInspectorSession
from app.operator.schemas import InspectorSessionCreate
from tests.support.factories import inspections
from tests.support.factories import role_scope as actors


def test_factory_examination_keeps_staff_tenant_and_exact_bank_boundaries(
    operator_client: TestClient, operator_db: Session
) -> None:
    tenant = actors.organization(operator_db, name="Synthetic inspection tenant")
    foreign_tenant = actors.organization(operator_db, name="Other inspection tenant")
    jurisdiction = operator_db.get(Jurisdiction, "GH")
    assert jurisdiction is not None
    bank = actors.bank(
        operator_db,
        organization_id=tenant.id,
        bank_id="BK-EXFCT001",
        jurisdiction=jurisdiction,
        institution_type="universal_bank",
    )
    sibling = actors.bank(
        operator_db,
        organization_id=tenant.id,
        bank_id="BK-EXFCT002",
        jurisdiction=jurisdiction,
        institution_type="universal_bank",
    )
    foreign = actors.bank(
        operator_db,
        organization_id=foreign_tenant.id,
        bank_id="BK-EXFCT003",
        jurisdiction=jurisdiction,
        institution_type="universal_bank",
    )
    operator = inspections.staff(operator_db, email="examiner@staff.example.test", role="developer")
    human = actors.human(operator_db, organization_id=tenant.id, legacy_role="examiner")
    normal = actors.tenant_session(operator_db, user=human)
    staff_headers = inspections.staff_headers(operator=operator)
    assignment = inspections.examiner_assignment(
        operator_client,
        headers=staff_headers,
        payload=InspectorSessionCreate(
            organization_id=tenant.id,
            bank_id=bank.id,
            reason="Synthetic examination",
            support_reference="SUP-FACTORY",
            mode="consent",
            ttl_minutes=30,
        ),
    )
    assert assignment.bank_id == bank.id
    assert assignment.organization_id == tenant.id
    assert assignment.started_by == "examiner@staff.example.test"
    assert assignment.read_only is True
    row = operator_db.get(OperatorInspectorSession, UUID(assignment.session_id))
    assert row is not None
    assert row.bank_id == bank.id
    examiner = inspections.examiner_headers(
        operator_client, headers=staff_headers, session_id=assignment.session_id
    )
    # No credential family substitutes for another on the staff plane.
    for credential in (actors.bearer(normal.access_token), examiner):
        denied = operator_client.get("/operator/v1/inspector/sessions", headers=credential)
        assert denied.status_code == 401, denied.text
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        staff_denied = client.get("/api/v1/banks", headers=staff_headers)
        assert staff_denied.status_code == 401, staff_denied.text
        tenant_denied = client.get(
            f"/api/v1/banks/{bank.id}/ecl-assumptions",
            headers=actors.bearer(normal.access_token),
        )
        assert tenant_denied.status_code == 403, tenant_denied.text
        for bank_id, expected in ((bank.id, 200), (sibling.id, 403), (foreign.id, 404)):
            response = client.get(f"/api/v1/banks/{bank_id}/ecl-assumptions", headers=examiner)
            assert response.status_code == expected, response.text
        mutation = client.put(f"/api/v1/banks/{bank.id}/ecl-assumptions", headers=examiner, json={})
        assert mutation.status_code == 403, mutation.text


def test_factory_developer_does_not_gain_break_glass_or_another_operators_session(
    operator_client: TestClient, operator_db: Session
) -> None:
    tenant = actors.organization(operator_db, name="Synthetic staff custody tenant")
    jurisdiction = operator_db.get(Jurisdiction, "GH")
    assert jurisdiction is not None
    bank = actors.bank(
        operator_db,
        organization_id=tenant.id,
        bank_id="BK-EXFCT004",
        jurisdiction=jurisdiction,
        institution_type="universal_bank",
    )
    operator = inspections.staff(operator_db, email="first@staff.example.test", role="developer")
    other = inspections.staff(operator_db, email="other@staff.example.test", role="developer")
    operator_db.commit()
    headers = inspections.staff_headers(operator=operator)
    payload = InspectorSessionCreate(
        organization_id=tenant.id,
        bank_id=bank.id,
        reason="Synthetic custody check",
        support_reference="SUP-FACTORY",
        mode="consent",
    )
    assignment = inspections.examiner_assignment(operator_client, headers=headers, payload=payload)
    denied = operator_client.post(
        "/operator/v1/inspector/sessions",
        headers=headers,
        json={**payload.model_dump(mode="json"), "mode": "break_glass"},
    )
    assert denied.status_code == 403, denied.text
    denied = operator_client.post(
        f"/operator/v1/inspector/sessions/{assignment.session_id}/act-token",
        headers=inspections.staff_headers(operator=other),
    )
    assert denied.status_code == 403, denied.text
    ended = operator_client.post(
        f"/operator/v1/inspector/sessions/{assignment.session_id}/end", headers=headers
    )
    assert ended.status_code == 200, ended.text
    denied = operator_client.post(
        f"/operator/v1/inspector/sessions/{assignment.session_id}/act-token", headers=headers
    )
    assert denied.status_code == 409, denied.text
