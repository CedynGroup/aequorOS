"""POST /operator/v1/inspector/sessions/{id}/act-token — act-as-examiner mint.

Pins the mint contract: an ACTIVE, caller-owned inspector session yields a
short-lived READ-ONLY examiner impersonation token, clamped to min(session,
+15m), scoped to the session's tenant + the examiner role, audited without ever
logging the token. Ownership, session state, and the fail-closed secret rule are
all enforced here.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_operator_settings, get_settings
from app.db.base import utc_now
from app.models import OperatorAuditLog, OperatorInspectorSession
from tests.operator.conftest import operator_headers, provision_payload
from tests.operator.test_operator_auth import PASSWORD, bearer, login, make_operator

SESSIONS = "/operator/v1/inspector/sessions"


def _provision(client: TestClient) -> str:
    return client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    ).json()["organization_id"]


def _start_session(
    client: TestClient,
    org_id: str,
    *,
    ttl_minutes: int = 30,
    headers: dict[str, str] | None = None,
) -> str:
    response = client.post(
        SESSIONS,
        json={
            "organization_id": org_id,
            "reason": "act-as-examiner",
            "mode": "consent",
            "ttl_minutes": ttl_minutes,
        },
        headers=headers or operator_headers(),
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _act_token_url(session_id: str) -> str:
    return f"{SESSIONS}/{session_id}/act-token"


def test_requires_authentication(operator_client: TestClient) -> None:
    assert operator_client.post(_act_token_url(str(uuid.uuid4()))).status_code == 401


def test_mint_success_returns_scoped_token_and_audits(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id = _provision(operator_client)
    session_id = _start_session(operator_client, organization_id, ttl_minutes=60)

    before = utc_now()
    response = operator_client.post(
        _act_token_url(session_id), headers=operator_headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"act_token", "expires_at", "dashboard_url"}
    assert body["dashboard_url"] == get_operator_settings().bank_app_base_url

    # The token decodes with the dedicated secret and carries the right scope.
    secret = get_settings().auth.impersonation_jwt_secret
    assert secret
    claims = security.decode_impersonation_token(body["act_token"], secret=secret)
    assert claims is not None
    assert claims["typ"] == "impersonation"
    assert claims["org"] == organization_id
    assert claims["session_id"] == session_id
    assert claims["act_operator"] == "dev@aequoros.com"
    assert claims["roles"] == ["examiner"]

    # A 60-minute session is clamped to the 15-minute act-token ceiling.
    expires_at = dt.datetime.fromisoformat(body["expires_at"])
    assert expires_at <= before + dt.timedelta(minutes=15, seconds=5)
    assert expires_at > before + dt.timedelta(minutes=14)

    # Audited as inspector.act_token.mint — and the raw token never appears.
    audit = list(
        operator_db.scalars(
            select(OperatorAuditLog).where(
                OperatorAuditLog.action == "inspector.act_token.mint"
            )
        )
    )
    assert len(audit) == 1
    assert audit[0].target_org == organization_id
    assert audit[0].detail["session_id"] == session_id
    assert audit[0].detail["role"] == "examiner"
    assert body["act_token"] not in str(audit[0].detail)


def test_mint_unknown_session_is_404(operator_client: TestClient) -> None:
    response = operator_client.post(
        _act_token_url(str(uuid.uuid4())), headers=operator_headers()
    )
    assert response.status_code == 404, response.text


def test_mint_ended_session_is_409(operator_client: TestClient) -> None:
    organization_id = _provision(operator_client)
    session_id = _start_session(operator_client, organization_id)
    ended = operator_client.post(f"{SESSIONS}/{session_id}/end", headers=operator_headers())
    assert ended.status_code == 200, ended.text

    response = operator_client.post(_act_token_url(session_id), headers=operator_headers())
    assert response.status_code == 409, response.text


def test_mint_expired_session_is_409(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id = _provision(operator_client)
    session_id = _start_session(operator_client, organization_id)
    # Force the session past its expiry (end_session updates the same row, so
    # this table is not update-blocked).
    row = operator_db.get(OperatorInspectorSession, uuid.UUID(session_id))
    assert row is not None
    row.expires_at = utc_now() - dt.timedelta(minutes=1)
    operator_db.commit()

    response = operator_client.post(_act_token_url(session_id), headers=operator_headers())
    assert response.status_code == 409, response.text


def test_mint_by_non_owner_is_403(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id = _provision(operator_client)
    # The dev token (dev@aequoros.com) opens the session …
    session_id = _start_session(operator_client, organization_id)
    # … a DIFFERENT operator may not mint an act-token in it.
    make_operator(operator_db)  # ama@aequoros.com, developer
    ama = bearer(login(operator_client, "ama@aequoros.com", PASSWORD))

    response = operator_client.post(_act_token_url(session_id), headers=ama)
    assert response.status_code == 403, response.text


def test_mint_503_when_secret_unset(
    operator_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    organization_id = _provision(operator_client)
    session_id = _start_session(operator_client, organization_id)

    monkeypatch.setenv("IMPERSONATION_JWT_SECRET", "")
    get_settings.cache_clear()
    assert get_settings().auth.impersonation_jwt_secret is None

    response = operator_client.post(_act_token_url(session_id), headers=operator_headers())
    assert response.status_code == 503, response.text
