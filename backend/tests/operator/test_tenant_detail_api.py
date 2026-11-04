"""Single-tenant detail reads: GET /tenants/{org_id}[/users|/entitlements|/storage]."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OperatorAuditLog, User
from tests.operator.conftest import operator_headers, provision_payload, start_inspection

BASE = "/operator/v1/tenants"


def _provision(client: TestClient) -> tuple[str, str]:
    body = client.post(f"{BASE}", json=provision_payload(), headers=operator_headers()).json()
    assert body["succeeded"] is True, body
    return body["organization_id"], body["bank_id"]


# -- GET /tenants/{org_id} -------------------------------------------------------
def test_get_tenant_returns_the_list_row_shape_and_audits(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    response = operator_client.get(f"{BASE}/{organization_id}", headers=operator_headers())
    assert response.status_code == 200
    row = response.json()
    assert row["organization_id"] == organization_id
    assert row["bank_id"] == bank_id
    assert row["jurisdiction_code"] == "GH"
    assert row["currency"] == "GHS"
    # Same field surface as a TenantRead list row.
    list_row = operator_client.get(BASE, headers=operator_headers()).json()["tenants"][0]
    assert set(row) == set(list_row)

    audit = list(
        operator_db.scalars(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "tenants.get")
        )
    )
    assert len(audit) == 1
    assert audit[0].target_org == organization_id


def test_get_tenant_unknown_org_is_404(operator_client: TestClient) -> None:
    response = operator_client.get(f"{BASE}/OR-00000000", headers=operator_headers())
    assert response.status_code == 404


def test_get_tenant_requires_auth(operator_client: TestClient) -> None:
    assert operator_client.get(f"{BASE}/OR-00000000").status_code == 401


# -- GET /tenants/{org_id}/users -------------------------------------------------
def test_tenant_users_lists_the_seeded_admin(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, _bank_id = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.get(f"{BASE}/{organization_id}/users", headers=operator_headers())
    assert response.status_code == 200
    users = response.json()["users"]
    # The provisioning saga seeds the first admin.
    assert any(u["email"] == "admin@testbank.example" for u in users)
    admin = next(u for u in users if u["email"] == "admin@testbank.example")
    assert admin["role"] == "account_admin"
    assert admin["is_active"] is True
    assert set(admin) == {
        "email",
        "full_name",
        "role",
        "auth_provider",
        "is_active",
        "last_login_at",
        "created_at",
    }
    # full_name maps from the tenant User.display_name column.
    seeded = operator_db.scalar(
        select(User).where(
            User.organization_id == organization_id,
            User.email == "admin@testbank.example",
        )
    )
    assert admin["full_name"] == (seeded.display_name if seeded else None)


def test_tenant_users_unknown_org_is_403_without_a_session(
    operator_client: TestClient,
) -> None:
    # A gated endpoint refuses before revealing whether the org exists: no
    # session can exist for an unknown org, so the response is 403, not 404.
    assert (
        operator_client.get(f"{BASE}/OR-00000000/users", headers=operator_headers()).status_code
        == 403
    )


def test_tenant_users_is_scoped_to_the_org(
    operator_client: TestClient, operator_db: Session
) -> None:
    org_a, _ = _provision(operator_client)
    org_b = operator_client.post(
        BASE,
        json=provision_payload(
            organization_name="Second Holdings",
            bank_name="Second Bank",
            admin_email="admin@second.example",
        ),
        headers=operator_headers(),
    ).json()["organization_id"]
    start_inspection(operator_client, org_a)
    start_inspection(operator_client, org_b)

    emails_a = {
        u["email"]
        for u in operator_client.get(f"{BASE}/{org_a}/users", headers=operator_headers()).json()[
            "users"
        ]
    }
    emails_b = {
        u["email"]
        for u in operator_client.get(f"{BASE}/{org_b}/users", headers=operator_headers()).json()[
            "users"
        ]
    }
    assert "admin@testbank.example" in emails_a
    assert "admin@second.example" in emails_b
    assert emails_a.isdisjoint(emails_b)


# -- GET /tenants/{org_id}/entitlements ------------------------------------------
def test_tenant_entitlements_returns_rows_and_catalog(operator_client: TestClient) -> None:
    organization_id, _bank_id = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    # Grant a tier so there are rows to see.
    granted = operator_client.post(
        "/operator/v1/desk/entitlements/grant-tier",
        json={
            "organization_id": organization_id,
            "tier": "premium",
            "effective_from": date(2026, 1, 1).isoformat(),
        },
        headers=operator_headers(),
    )
    assert granted.status_code == 201, granted.text

    response = operator_client.get(
        f"{BASE}/{organization_id}/entitlements", headers=operator_headers()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["entitlements"], "expected the granted datasets"
    assert all(e["organization_id"] == organization_id for e in body["entitlements"])
    assert "datasets" in body["catalog"]
    assert "tiers" in body["catalog"]


def test_tenant_entitlements_unknown_org_is_403_without_a_session(
    operator_client: TestClient,
) -> None:
    assert (
        operator_client.get(
            f"{BASE}/OR-00000000/entitlements", headers=operator_headers()
        ).status_code
        == 403
    )


# -- GET /tenants/{org_id}/storage -----------------------------------------------
def test_tenant_storage_is_best_effort_for_minio(operator_client: TestClient) -> None:
    organization_id, _bank_id = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.get(f"{BASE}/{organization_id}/storage", headers=operator_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "minio"
    assert body["bucket"]  # first provisioned bucket name
    # MinIO quirks: metrics/KES unavailable, explained in the note rather than failing.
    assert body["object_count"] is None
    assert body["bytes"] is None
    assert body["kms_key_state"] is None
    assert body["note"] and "MinIO" in body["note"]


def test_tenant_storage_unknown_org_is_403_without_a_session(
    operator_client: TestClient,
) -> None:
    assert (
        operator_client.get(f"{BASE}/OR-00000000/storage", headers=operator_headers()).status_code
        == 403
    )
