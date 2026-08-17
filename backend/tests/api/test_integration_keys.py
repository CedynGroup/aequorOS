"""Integration keys on the ACTUAL primary: generate-once revocable credentials
for bank middleware.

Invariants: the raw key appears exactly once at issuance (only its hash is
stored, listings mask), it authenticates the push surface as an analyst-role
service account bound to the key, dies instantly on revocation (409 on a second
revoke), unknown keys 401, issue/revoke are admin-only and tenant-scoped, and
the lifecycle is audited. The real org already holds keys, so counts are
relative. Opt-in via REAL_DATA_DATABASE_URL, rolled back (tests/real_data.py).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, IntegrationKey, User
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    other_headers,
    real_headers,
    requires_real_data,
)

pytestmark = requires_real_data


def _issue(client: TestClient, label: str = "Core banking nightly push") -> dict[str, Any]:
    response = client.post(
        "/api/v1/integration-keys", headers=real_headers(), json={"label": label}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _list(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/v1/integration-keys", headers=real_headers())
    assert response.status_code == 200, response.text
    return response.json()["keys"]


def _push_payload(reason: str) -> dict[str, str]:
    return {
        "as_of_date": "2026-06-30",
        "idempotency_key": f"ik-push-{uuid4().hex}",
        "reason": reason,
    }


def test_issue_returns_key_once_and_list_only_masks(real_client: TestClient) -> None:
    before = _list(real_client)
    issued = _issue(real_client)
    assert issued["key"].startswith("aeq_live_")
    assert issued["record"]["key_prefix"].endswith("…")
    assert issued["key"].startswith(issued["record"]["key_prefix"][:-1])
    assert issued["key"] not in str(issued["record"])

    listed = _list(real_client)
    assert len(listed) == len(before) + 1
    assert issued["key"] not in str(listed)
    mine = next(item for item in listed if item["id"] == issued["record"]["id"])
    assert mine["revoked_at"] is None
    assert mine["label"] == "Core banking nightly push"
    assert {item["id"] for item in before} <= {item["id"] for item in listed}


def test_key_authenticates_push_as_analyst_service_account(
    real_client: TestClient, real_session: Session
) -> None:
    issued = _issue(real_client)
    bearer = {"Authorization": f"Bearer {issued['key']}"}

    opened = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/push-batches",
        headers=bearer,
        json=_push_payload("middleware nightly close"),
    )
    assert opened.status_code == 201, opened.text

    # The acting identity is THIS key's service account, not a human: a
    # per-key `service` user with the analyst role, in the issuing org.
    real_session.info["organization_id"] = REAL_ORG_ID
    key = real_session.get(IntegrationKey, UUID(issued["record"]["id"]))
    assert key is not None
    assert key.organization_id == REAL_ORG_ID
    service_user = real_session.get(User, key.service_user_id)
    assert service_user is not None
    assert service_user.auth_provider == "service"
    assert service_user.role == "analyst"
    assert service_user.organization_id == REAL_ORG_ID
    assert service_user.is_active

    # Analyst role only: admin surfaces are refused with the key.
    refused = real_client.get("/api/v1/integration-keys", headers=bearer)
    assert refused.status_code == 403


def test_revoked_key_is_dead_immediately(real_client: TestClient) -> None:
    issued = _issue(real_client)
    bearer = {"Authorization": f"Bearer {issued['key']}"}
    key_id = issued["record"]["id"]

    revoked = real_client.post(
        f"/api/v1/integration-keys/{key_id}/revoke",
        headers=real_headers(),
        json={"reason": "rotation drill"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    dead = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/push-batches",
        headers=bearer,
        json=_push_payload("should be refused"),
    )
    assert dead.status_code == 401

    again = real_client.post(
        f"/api/v1/integration-keys/{key_id}/revoke",
        headers=real_headers(),
        json={"reason": "twice"},
    )
    assert again.status_code == 409
    # The revoked row is retained (audit) and listed as revoked.
    mine = next(item for item in _list(real_client) if item["id"] == key_id)
    assert mine["revoked_at"] is not None


def test_unknown_key_is_rejected(real_client: TestClient) -> None:
    response = real_client.get(
        "/api/v1/banks",
        headers={"Authorization": "Bearer aeq_live_notARealKey0000000000000000000000000"},
    )
    assert response.status_code == 401


def test_issue_and_revoke_are_admin_only_and_tenant_scoped(real_client: TestClient) -> None:
    forbidden = real_client.post(
        "/api/v1/integration-keys",
        headers=real_headers(roles=("analyst",)),
        json={"label": "nope"},
    )
    assert forbidden.status_code == 403
    assert (
        real_client.get(
            "/api/v1/integration-keys", headers=real_headers(roles=("analyst",))
        ).status_code
        == 403
    )

    issued = _issue(real_client)
    other = other_headers()
    foreign = real_client.post(
        f"/api/v1/integration-keys/{issued['record']['id']}/revoke",
        headers=other,
        json={"reason": "not yours"},
    )
    assert foreign.status_code == 404
    # The other tenant's listing never shows this org's keys.
    foreign_list = real_client.get("/api/v1/integration-keys", headers=other)
    assert foreign_list.status_code == 200
    assert issued["record"]["id"] not in {item["id"] for item in foreign_list.json()["keys"]}
    # And the key still authenticates only into ITS org: the other tenant's
    # bank is invisible to it.
    bearer = {"Authorization": f"Bearer {issued['key']}"}
    listed = real_client.get("/api/v1/banks", headers=bearer)
    assert listed.status_code == 200
    assert {bank["organization_id"] for bank in listed.json()["banks"]} == {REAL_ORG_ID}


def test_lifecycle_is_audited(real_client: TestClient, real_session: Session) -> None:
    issued = _issue(real_client)
    real_client.post(
        f"/api/v1/integration-keys/{issued['record']['id']}/revoke",
        headers=real_headers(),
        json={"reason": "audit check"},
    )
    real_session.info["organization_id"] = REAL_ORG_ID
    events = real_session.scalars(
        select(AuditEvent.event_type).where(
            AuditEvent.entity_id == issued["record"]["id"],
        )
    ).all()
    assert "integration_key.issued" in events
    assert "integration_key.revoked" in events
