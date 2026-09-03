"""Attestation policy administration rides the Org Owner binding.

``PUT /attestation/signing-policies`` and ``PUT /attestation/signature-placements``
decide how a filed return is produced and who must sign it, so they sit in the
same authority that create/revoke scoped grants: the persisted, explicit
``org_owner`` binding (``GrantAdminTenant``). A scalar ``admin``/``account_admin``
role claim is deliberately ignored — that is the post-PR #150 RBAC finding this
suite pins, and the reason the gate is the binding rather than the ladder.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.models import AuditEvent, User
from app.services import authorization
from tests.api.helpers import ORG_1, USER_1, headers

POLICY_URL = "/api/v1/attestation/signing-policies"
PLACEMENT_URL = "/api/v1/attestation/signature-placements"

POLICY_PAYLOAD = {
    "return_code": "LCR-NSFR",
    "required_signatures": [],
    "require_signature": False,
    "effective_from": "2000-01-01",
    "reason": "aligned with the BoG attestation runbook",
}

PLACEMENT_PAYLOAD = {
    "return_code": "LCR-NSFR",
    "placements": [
        {
            "signing_role": "preparer",
            "field_type": "signature",
            "page_index": 1,
            "x1": 60,
            "y1": 260,
            "x2": 300,
            "y2": 345,
        },
        {
            "signing_role": "approver",
            "field_type": "signature",
            "page_index": 1,
            "x1": 310,
            "y1": 260,
            "x2": 550,
            "y2": 345,
        },
    ],
    "reason": "BoG LCR/NSFR signature block",
}

_OWNER_DENIAL = "This action requires Organization Owner authority."


def _session() -> Session:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    return session


def _owner_headers() -> dict[str, str]:
    """The role claim is irrelevant to the gate: the binding decides. Mirrors
    ``test_grant_administration._owner_headers``."""
    return headers(roles=("account_admin",), authorization_version=2)


def _bind(db: Session, *, role_bundle: RoleBundle = RoleBundle.ORG_OWNER) -> None:
    owner = db.get(User, USER_1)
    assert owner is not None, "the hermetic demo tenant must seed the actor"
    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=role_bundle,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION,
            None,
            ModuleScope.ACCOUNT,
            SensitivityScope.ALL,
        ),
        grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test-suite"),
        reason="binding-based authority for attestation policy tests",
    )


@pytest.fixture
def owner_client(db_client: TestClient) -> TestClient:
    """A hermetic API client whose actor holds an explicit Org Owner binding."""
    with _session() as db:
        _bind(db)
    return db_client


def test_org_owner_can_configure_placements_and_signing_policies(
    owner_client: TestClient,
) -> None:
    placed = owner_client.put(PLACEMENT_URL, headers=_owner_headers(), json=PLACEMENT_PAYLOAD)
    assert placed.status_code == 200, placed.text
    assert placed.json()["return_code"] == "LCR-NSFR"

    policy = owner_client.put(POLICY_URL, headers=_owner_headers(), json=POLICY_PAYLOAD)
    assert policy.status_code == 200, policy.text
    assert policy.json()["return_code"] == "LCR-NSFR"
    assert policy.json()["require_signature"] is False

    with _session() as db:
        events = set(
            db.scalars(select(AuditEvent.event_type).where(AuditEvent.actor_user_id == USER_1))
        )
        assert "attestation.signature_placement_template_updated" in events
        assert "attestation.signing_policy_updated" in events


@pytest.mark.parametrize("role", ("account_admin", "analyst", "approver", "viewer", "examiner"))
def test_no_scalar_role_configures_either_resource_without_the_binding(
    db_client: TestClient,
    role: str,
) -> None:
    for url, payload in ((POLICY_URL, POLICY_PAYLOAD), (PLACEMENT_URL, PLACEMENT_PAYLOAD)):
        response = db_client.put(url, headers=headers(roles=(role,)), json=payload)
        assert response.status_code == 403, (url, response.text)
        assert _OWNER_DENIAL in response.json()["error"]["message"], url


def test_an_account_admin_binding_does_not_open_attestation_configuration(
    db_client: TestClient,
) -> None:
    """The binding vocabulary has no account-administration tier for this control."""
    with _session() as db:
        _bind(db, role_bundle=RoleBundle.ACCOUNT_ADMIN)

    for url, payload in ((POLICY_URL, POLICY_PAYLOAD), (PLACEMENT_URL, PLACEMENT_PAYLOAD)):
        response = db_client.put(url, headers=_owner_headers(), json=payload)
        assert response.status_code == 403, (url, response.text)
        assert _OWNER_DENIAL in response.json()["error"]["message"], url


def test_reason_validation_survives_the_gate(owner_client: TestClient) -> None:
    without_reason = {**POLICY_PAYLOAD, "reason": ""}
    response = owner_client.put(POLICY_URL, headers=_owner_headers(), json=without_reason)
    assert response.status_code == 422, response.text
