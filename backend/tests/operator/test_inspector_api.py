"""Tenant inspector: READ-ONLY session tracking (no tenant token, no act-as-user).

Pins the session lifecycle (start/list/end), the audit rows, and the
break_glass admin gate. The whole security posture is that this endpoint records
a viewing window — it never mints tenant credentials — so the strongest check
here is simply that no such capability leaks into the response shape.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OperatorAuditLog, OperatorInspectorSession
from tests.operator.conftest import operator_headers, provision_payload
from tests.operator.test_operator_auth import PASSWORD, bearer, login, make_operator

SESSIONS = "/operator/v1/inspector/sessions"


def _provision(client: TestClient) -> str:
    return client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    ).json()["organization_id"]


def test_requires_authentication(operator_client: TestClient) -> None:
    assert operator_client.get(SESSIONS).status_code == 401
    assert operator_client.post(SESSIONS, json={}).status_code == 401


def test_start_consent_session_returns_read_only_window_and_audits(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id = _provision(operator_client)
    response = operator_client.post(
        SESSIONS,
        json={
            "organization_id": organization_id,
            "reason": "customer asked for help with an ingestion error",
            "mode": "consent",
            "ttl_minutes": 30,
        },
        headers=operator_headers(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organization_id"] == organization_id
    assert body["mode"] == "consent"
    assert body["read_only"] is True
    assert body["started_by"] == "dev@aequoros.com"
    assert body["ended_at"] is None
    assert body["session_id"]
    # No tenant credential / act-as-user material leaks into the response.
    assert "token" not in response.text.lower()

    audit = list(
        operator_db.scalars(
            select(OperatorAuditLog).where(
                OperatorAuditLog.action == "inspector.session.start"
            )
        )
    )
    assert len(audit) == 1
    assert audit[0].target_org == organization_id
    assert audit[0].detail["mode"] == "consent"


def test_start_unknown_org_is_404(operator_client: TestClient) -> None:
    response = operator_client.post(
        SESSIONS,
        json={"organization_id": "OR-00000000", "reason": "x", "mode": "consent"},
        headers=operator_headers(),
    )
    assert response.status_code == 404


def test_reason_is_required(operator_client: TestClient) -> None:
    organization_id = _provision(operator_client)
    response = operator_client.post(
        SESSIONS,
        json={"organization_id": organization_id, "reason": "", "mode": "consent"},
        headers=operator_headers(),
    )
    assert response.status_code == 422


def test_ttl_out_of_range_is_422(operator_client: TestClient) -> None:
    organization_id = _provision(operator_client)
    for ttl in (10, 61):
        response = operator_client.post(
            SESSIONS,
            json={
                "organization_id": organization_id,
                "reason": "r",
                "mode": "consent",
                "ttl_minutes": ttl,
            },
            headers=operator_headers(),
        )
        assert response.status_code == 422, ttl


def test_break_glass_requires_operator_admin(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id = _provision(operator_client)
    make_operator(operator_db)  # developer
    dev_headers = bearer(login(operator_client, "ama@aequoros.com", PASSWORD))

    payload = {
        "organization_id": organization_id,
        "reason": "incident triage",
        "mode": "break_glass",
    }
    # A developer may open a consent session but NOT a break_glass one.
    consent = operator_client.post(
        SESSIONS, json={**payload, "mode": "consent"}, headers=dev_headers
    )
    assert consent.status_code == 201, consent.text
    forbidden = operator_client.post(SESSIONS, json=payload, headers=dev_headers)
    assert forbidden.status_code == 403, forbidden.text

    # The dev token is super_admin → break_glass is allowed.
    allowed = operator_client.post(SESSIONS, json=payload, headers=operator_headers())
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["mode"] == "break_glass"


def test_list_newest_first_and_active_filter(operator_client: TestClient) -> None:
    organization_id = _provision(operator_client)
    first = operator_client.post(
        SESSIONS,
        json={"organization_id": organization_id, "reason": "one", "mode": "consent"},
        headers=operator_headers(),
    ).json()
    second = operator_client.post(
        SESSIONS,
        json={"organization_id": organization_id, "reason": "two", "mode": "consent"},
        headers=operator_headers(),
    ).json()

    listed = operator_client.get(SESSIONS, headers=operator_headers()).json()
    assert listed["total"] == 2
    ids = [s["session_id"] for s in listed["sessions"]]
    # Newest first.
    assert ids[0] == second["session_id"]
    assert ids[1] == first["session_id"]

    # End one; active filter should then show only the other.
    ended = operator_client.post(
        f"{SESSIONS}/{first['session_id']}/end", headers=operator_headers()
    )
    assert ended.status_code == 200
    assert ended.json()["ended_at"] is not None
    assert ended.json()["ended_by"] == "dev@aequoros.com"

    active = operator_client.get(f"{SESSIONS}?active=true", headers=operator_headers()).json()
    assert [s["session_id"] for s in active["sessions"]] == [second["session_id"]]
    inactive = operator_client.get(
        f"{SESSIONS}?active=false", headers=operator_headers()
    ).json()
    assert [s["session_id"] for s in inactive["sessions"]] == [first["session_id"]]


def test_list_scoped_by_organization(operator_client: TestClient) -> None:
    org_a = _provision(operator_client)
    org_b = operator_client.post(
        "/operator/v1/tenants",
        json=provision_payload(
            organization_name="Second Holdings",
            bank_name="Second Bank",
            admin_email="admin@second.example",
        ),
        headers=operator_headers(),
    ).json()["organization_id"]
    for org in (org_a, org_b):
        operator_client.post(
            SESSIONS,
            json={"organization_id": org, "reason": "r", "mode": "consent"},
            headers=operator_headers(),
        )
    scoped = operator_client.get(
        f"{SESSIONS}?organization_id={org_a}", headers=operator_headers()
    ).json()
    assert scoped["total"] == 1
    assert scoped["sessions"][0]["organization_id"] == org_a


def test_end_is_idempotent_and_audited(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id = _provision(operator_client)
    session_id = operator_client.post(
        SESSIONS,
        json={"organization_id": organization_id, "reason": "r", "mode": "consent"},
        headers=operator_headers(),
    ).json()["session_id"]

    first = operator_client.post(f"{SESSIONS}/{session_id}/end", headers=operator_headers())
    assert first.status_code == 200
    first_ended_at = first.json()["ended_at"]
    second = operator_client.post(f"{SESSIONS}/{session_id}/end", headers=operator_headers())
    assert second.status_code == 200
    # Idempotent: the original close stamp is preserved (normalize the SQLite
    # naive/aware 'Z'-suffix serialization artifact — same instant either way).
    assert second.json()["ended_at"].rstrip("Z") == first_ended_at.rstrip("Z")

    end_audits = list(
        operator_db.scalars(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "inspector.session.end")
        )
    )
    assert len(end_audits) == 2  # two calls, both audited


def test_end_unknown_session_is_404(operator_client: TestClient) -> None:
    response = operator_client.post(
        f"{SESSIONS}/{uuid.uuid4()}/end", headers=operator_headers()
    )
    assert response.status_code == 404


def test_read_only_column_is_always_true(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id = _provision(operator_client)
    operator_client.post(
        SESSIONS,
        json={"organization_id": organization_id, "reason": "r", "mode": "consent"},
        headers=operator_headers(),
    )
    rows = list(operator_db.scalars(select(OperatorInspectorSession)))
    assert rows and all(r.read_only is True for r in rows)
