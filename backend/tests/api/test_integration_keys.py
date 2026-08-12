"""Integration keys: generate-once revocable credentials for bank middleware.

The raw key appears exactly once at issuance (only its hash is stored), works
as a plain bearer credential on the push surface as an analyst-role service
account, and dies instantly on revocation. Issue/list/revoke are admin-only.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import AuditEvent, User
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.api.helpers import ORG_2, headers


def _seed(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text


def _issue(db_client: TestClient, label: str = "Core banking nightly push") -> dict[str, Any]:
    response = db_client.post(
        "/api/v1/integration-keys", headers=headers(), json={"label": label}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_issue_returns_key_once_and_list_only_masks(db_client: TestClient) -> None:
    _seed(db_client)
    issued = _issue(db_client)
    assert issued["key"].startswith("aeq_live_")
    assert issued["record"]["key_prefix"].endswith("…")
    assert issued["key"] not in str(issued["record"])

    listed = db_client.get("/api/v1/integration-keys", headers=headers()).json()
    assert len(listed["keys"]) == 1
    assert issued["key"] not in str(listed)
    assert listed["keys"][0]["revoked_at"] is None


def test_key_authenticates_push_as_analyst_service_account(
    db_client: TestClient,
) -> None:
    _seed(db_client)
    issued = _issue(db_client)
    bearer = {"Authorization": f"Bearer {issued['key']}"}

    opened = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches",
        headers=bearer,
        json={
            "as_of_date": "2026-06-30",
            "idempotency_key": "ik-push-1",
            "reason": "middleware nightly close",
        },
    )
    assert opened.status_code == 201, opened.text

    # The acting identity is the service account, not a human.
    with get_sessionmaker()() as session:
        session.info["organization_id"] = "OR-DEM00001"
        service_user = session.scalar(select(User).where(User.auth_provider == "service"))
        assert service_user is not None
        assert service_user.role == "analyst"

    # Analyst role only: admin surfaces are refused with the key.
    refused = db_client.get("/api/v1/integration-keys", headers=bearer)
    assert refused.status_code == 403


def test_revoked_key_is_dead_immediately(db_client: TestClient) -> None:
    _seed(db_client)
    issued = _issue(db_client)
    bearer = {"Authorization": f"Bearer {issued['key']}"}
    key_id = issued["record"]["id"]

    revoked = db_client.post(
        f"/api/v1/integration-keys/{key_id}/revoke",
        headers=headers(),
        json={"reason": "rotation drill"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    dead = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches",
        headers=bearer,
        json={
            "as_of_date": "2026-06-30",
            "idempotency_key": "ik-push-2",
            "reason": "should be refused",
        },
    )
    assert dead.status_code == 401

    again = db_client.post(
        f"/api/v1/integration-keys/{key_id}/revoke",
        headers=headers(),
        json={"reason": "twice"},
    )
    assert again.status_code == 409


def test_unknown_key_is_rejected(db_client: TestClient) -> None:
    _seed(db_client)
    response = db_client.get(
        "/api/v1/banks",
        headers={"Authorization": "Bearer aeq_live_notARealKey0000000000000000000000000"},
    )
    assert response.status_code == 401


def test_issue_and_revoke_are_admin_only_and_tenant_scoped(
    db_client: TestClient,
) -> None:
    _seed(db_client)
    forbidden = db_client.post(
        "/api/v1/integration-keys",
        headers=headers(roles=("analyst",)),
        json={"label": "nope"},
    )
    assert forbidden.status_code == 403

    issued = _issue(db_client)
    foreign = db_client.post(
        f"/api/v1/integration-keys/{issued['record']['id']}/revoke",
        headers=headers(org_id=ORG_2),
        json={"reason": "not yours"},
    )
    assert foreign.status_code == 404


def test_lifecycle_is_audited(db_client: TestClient) -> None:
    _seed(db_client)
    issued = _issue(db_client)
    db_client.post(
        f"/api/v1/integration-keys/{issued['record']['id']}/revoke",
        headers=headers(),
        json={"reason": "audit check"},
    )
    with get_sessionmaker()() as session:
        session.info["organization_id"] = "OR-DEM00001"
        events = session.scalars(
            select(AuditEvent.event_type).where(
                AuditEvent.entity_id == issued["record"]["id"],
            )
        ).all()
    assert "integration_key.issued" in events
    assert "integration_key.revoked" in events
