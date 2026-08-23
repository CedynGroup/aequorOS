"""Desk dataset entitlements: the Tenant Inspector gate and its audit trail.

An entitlement is tenant state — which desk-published datasets one bank
receives — so reading or changing it is a look inside that tenant, not a fleet
operation. These four routes used to be the one gap in the staff plane's
control story (audit finding D-26): the organization came from the client, the
gate was the base ``Operator`` dependency, the read wrote no audit row at all,
and ``revoke`` resolved a grant by id alone with no org scoping.

Two facts make that worse than a missing log line. ``market_data_entitlements``
is FORCE row-level secured since migration ``202608230036``, so an ungated
write crosses an isolation boundary; and ``active_datasets`` grandfathers an
org with no visible rows to the STANDARD tier, so revoking the wrong tenant's
rows silently upgrades it rather than cutting it off.

The structural half of this guard — that no operator route naming a tenant can
skip the gate — is ``test_tenant_mutation_gate.py``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OperatorAuditLog
from app.models.entitlements import MarketDataEntitlement
from tests.operator.conftest import operator_headers, provision_payload, start_inspection

DESK = "/operator/v1/desk/entitlements"
TENANTS = "/operator/v1/tenants"
EFFECTIVE_FROM = "2026-01-01"


def _provision(client: TestClient, **overrides: object) -> str:
    body = client.post(
        TENANTS, json=provision_payload(**overrides), headers=operator_headers()
    ).json()
    assert body["succeeded"] is True, body
    return str(body["organization_id"])


def _grant_tier(client: TestClient, org_id: str, tier: str = "premium"):  # noqa: ANN202
    return client.post(
        f"{DESK}/grant-tier",
        json={"organization_id": org_id, "tier": tier, "effective_from": EFFECTIVE_FROM},
        headers=operator_headers(),
    )


def _audit_rows(db: Session, action: str) -> list[OperatorAuditLog]:
    db.expire_all()
    return list(
        db.scalars(select(OperatorAuditLog).where(OperatorAuditLog.action == action))
    )


# -- the gate --------------------------------------------------------------------
def test_every_entitlement_route_refuses_without_an_inspection_session(
    operator_client: TestClient, operator_db: Session
) -> None:
    org_id = _provision(operator_client)
    calls = [
        operator_client.get(
            DESK, params={"organization_id": org_id}, headers=operator_headers()
        ),
        _grant_tier(operator_client, org_id),
        operator_client.post(
            f"{DESK}/grant-dataset",
            json={
                "organization_id": org_id,
                "dataset_code": "DESK_CURVES_CREDIT",
                "effective_from": EFFECTIVE_FROM,
            },
            headers=operator_headers(),
        ),
        operator_client.post(
            f"{DESK}/00000000-0000-0000-0000-000000000000/revoke",
            json={"organization_id": org_id},
            headers=operator_headers(),
        ),
    ]
    for response in calls:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["details"]["code"] == "inspection_required"
    # The refusal happens BEFORE any work: no grant row and no audit row.
    operator_db.expire_all()
    assert operator_db.scalars(select(MarketDataEntitlement)).all() == []
    entitlement_actions = operator_db.scalars(
        select(OperatorAuditLog.action).where(
            OperatorAuditLog.action.like("%entitlement%")
        )
    ).all()
    assert list(entitlement_actions) == []


def test_listing_without_an_organization_is_refused(operator_client: TestClient) -> None:
    """The unfiltered form dumped every tenant's commercial terms in one
    unaudited response. Absence refuses — it does not fall back to 'all'."""
    _provision(operator_client)
    response = operator_client.get(DESK, headers=operator_headers())
    assert response.status_code == 422


# -- the audit trail --------------------------------------------------------------
def test_grant_tier_is_gated_and_audited_against_the_tenant(
    operator_client: TestClient, operator_db: Session
) -> None:
    org_id = _provision(operator_client)
    session_id = start_inspection(operator_client, org_id)
    response = _grant_tier(operator_client, org_id)
    assert response.status_code == 201, response.text
    granted = {row["dataset_code"] for row in response.json()["entitlements"]}
    assert "DESK_CURVES_CREDIT" in granted

    rows = _audit_rows(operator_db, "inspector.entitlement.grant_tier")
    assert len(rows) == 1
    assert rows[0].target_org == org_id
    assert rows[0].detail["session_id"] == session_id
    assert rows[0].detail["tier"] == "premium"


def test_read_is_gated_and_audited(
    operator_client: TestClient, operator_db: Session
) -> None:
    org_id = _provision(operator_client)
    session_id = start_inspection(operator_client, org_id)
    _grant_tier(operator_client, org_id, tier="core")
    response = operator_client.get(
        DESK, params={"organization_id": org_id}, headers=operator_headers()
    )
    assert response.status_code == 200
    assert {row["dataset_code"] for row in response.json()["entitlements"]} == {
        "DESK_RATES",
        "DESK_FX",
    }
    rows = _audit_rows(operator_db, "inspector.read.entitlements")
    assert [row.target_org for row in rows] == [org_id]
    assert rows[0].detail["session_id"] == session_id


def test_grant_dataset_and_revoke_are_gated_and_audited(
    operator_client: TestClient, operator_db: Session
) -> None:
    org_id = _provision(operator_client)
    start_inspection(operator_client, org_id)
    granted = operator_client.post(
        f"{DESK}/grant-dataset",
        json={
            "organization_id": org_id,
            "dataset_code": "DESK_CURVES_CREDIT",
            "effective_from": EFFECTIVE_FROM,
        },
        headers=operator_headers(),
    )
    assert granted.status_code == 201, granted.text
    entitlement_id = granted.json()["id"]

    revoked = operator_client.post(
        f"{DESK}/{entitlement_id}/revoke",
        json={"organization_id": org_id},
        headers=operator_headers(),
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    for action in ("inspector.entitlement.grant_dataset", "inspector.entitlement.revoke"):
        rows = _audit_rows(operator_db, action)
        assert [row.target_org for row in rows] == [org_id], action


# -- cross-tenant scoping ----------------------------------------------------------
def test_revoke_cannot_reach_another_tenants_grant(
    operator_client: TestClient, operator_db: Session
) -> None:
    """A session for tenant B must not end tenant A's grant by naming its id.

    ``revoke`` resolved the row with a bare ``db.get``, so the id alone was
    authority. Now the organization is part of the lookup key: a foreign row is
    a 404, indistinguishable from one that never existed.
    """
    owner = _provision(operator_client)
    bystander = _provision(
        operator_client,
        organization_name="Bystander Holdings",
        bank_name="Bystander Bank",
        admin_email="admin@bystander.example",
    )
    start_inspection(operator_client, owner)
    granted = operator_client.post(
        f"{DESK}/grant-dataset",
        json={
            "organization_id": owner,
            "dataset_code": "DESK_CURVES_CREDIT",
            "effective_from": EFFECTIVE_FROM,
        },
        headers=operator_headers(),
    )
    entitlement_id = granted.json()["id"]

    start_inspection(operator_client, bystander)
    attempt = operator_client.post(
        f"{DESK}/{entitlement_id}/revoke",
        json={"organization_id": bystander},
        headers=operator_headers(),
    )
    assert attempt.status_code == 404
    operator_db.expire_all()
    row = operator_db.scalar(
        select(MarketDataEntitlement).where(
            MarketDataEntitlement.id == UUID(entitlement_id)
        )
    )
    assert row is not None and row.status == "active"
    assert _audit_rows(operator_db, "inspector.entitlement.revoke") == []


def test_a_session_for_one_tenant_does_not_open_another(
    operator_client: TestClient,
) -> None:
    owner = _provision(operator_client)
    bystander = _provision(
        operator_client,
        organization_name="Bystander Holdings",
        bank_name="Bystander Bank",
        admin_email="admin@bystander.example",
    )
    start_inspection(operator_client, bystander)
    refused = _grant_tier(operator_client, owner)
    assert refused.status_code == 403
    assert refused.json()["error"]["details"]["code"] == "inspection_required"
