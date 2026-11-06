"""Staff and examiner setup through the existing, separate credential paths."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_operator_settings
from app.operator.models import OperatorUser
from app.operator.schemas import (
    InspectorActTokenRead,
    InspectorSessionCreate,
    InspectorSessionRead,
)
from app.operator.services.operator_auth import mint_operator_token
from tests.support.factories.role_scope import bearer


def staff(db: Session, *, email: str, role: str) -> OperatorUser:
    """Persist one explicit staff role, without a password or tenant grants."""
    row = OperatorUser(
        email=email,
        display_name="Synthetic staff",
        role=role,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def staff_headers(*, operator: OperatorUser) -> dict[str, str]:
    secret = get_operator_settings().jwt_secret
    if not secret:
        raise ValueError("the operator test secret must be configured")
    token, _ = mint_operator_token(email=operator.email, role=operator.role, secret=secret)
    return bearer(token)


def examiner_assignment(
    client: TestClient,
    *,
    headers: dict[str, str],
    payload: InspectorSessionCreate,
) -> InspectorSessionRead:
    """Open a real bank-bound inspection, never select a bank for the caller."""
    response = client.post(
        "/operator/v1/inspector/sessions",
        headers=headers,
        json=payload.model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    return InspectorSessionRead.model_validate_json(response.text)


def examiner_headers(
    client: TestClient, *, headers: dict[str, str], session_id: str
) -> dict[str, str]:
    """Use the real mint route, including caller ownership and lifetime gates."""
    response = client.post(
        f"/operator/v1/inspector/sessions/{session_id}/act-token",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return bearer(InspectorActTokenRead.model_validate_json(response.text).act_token)
