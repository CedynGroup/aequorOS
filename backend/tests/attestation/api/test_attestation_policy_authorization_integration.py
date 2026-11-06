"""Attestation policy administration is an organization-scoped REG configure duty.

``PUT /attestation/signing-policies`` and ``PUT /attestation/signature-placements``
decide how a filed return is produced and who must sign it for EVERY bank in the
organization, so they require one explicit organization-scope
``reg/configure/restricted`` binding (VAL-CFG-008). A bank-scoped Configurator
binding is deliberately insufficient — authority over every future filing of a
return is not a single-institution act — and scalar ``admin``/``account_admin``
role claims are ignored, the post-PR #150 RBAC finding this suite pins. Denial
writes nothing: no policy row, no template row, no audit event.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    ReturnSignaturePlacement,
    ReturnSigningPolicy,
    User,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.helpers import ORG_1, USER_1, error_envelope, headers

POLICY_URL = "/api/v1/attestation/signing-policies"
PLACEMENT_URL = "/api/v1/attestation/signature-placements"

# ``_grant`` and the ``book`` fixture commit through independent sessions, so
# this module needs the committing database: its schema resets between tests
# and one test's binding can never authorize (or deny) the next one.
pytestmark = pytest.mark.committing_db

POLICY_PAYLOAD: dict[str, Any] = {
    "return_code": "LCR-NSFR",
    "required_signatures": [],
    "require_signature": False,
    "effective_from": "2000-01-01",
    "reason": "aligned with the BoG attestation runbook",
}

PLACEMENT_PAYLOAD: dict[str, Any] = {
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

ORG_DENIAL = "This action requires an organization-scoped Regulatory Reporting binding."


def _session() -> Session:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    return session


def _grant(
    bundle: RoleBundle,
    *,
    scope: authorization.BindingScope,
) -> None:
    with _session() as db:
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=scope,
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "attestation-policy-test"),
            reason="Attestation policy configuration regression authority",
        )
        db.commit()


def _org_scope(
    module: ModuleScope = ModuleScope.REGULATORY,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
) -> authorization.BindingScope:
    return authorization.BindingScope(InstitutionScope.ORGANIZATION, None, module, sensitivity)


def _bank_scope() -> authorization.BindingScope:
    return authorization.BindingScope(
        InstitutionScope.INSTITUTION,
        SAMPLE_BANK_ID,
        ModuleScope.REGULATORY,
        SensitivityScope.RESTRICTED,
    )


def _current_version() -> int:
    with _session() as db:
        user = db.get(User, USER_1)
        assert user is not None
        return user.authorization_version


def _auth() -> dict[str, str]:
    # Scalar roles ("admin", "approver") grant nothing by themselves: only the
    # explicit scoped binding may authorize these routes.
    return headers(roles=("admin", "approver"), authorization_version=_current_version())


@pytest.fixture(autouse=True)
def book(db_client: TestClient) -> None:
    _ = db_client
    with _session() as db:
        materialize_canonical_test_book(db)
        db.commit()


def _counts() -> tuple[int, int, int]:
    with _session() as db:
        return (
            db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
            db.scalar(select(func.count()).select_from(ReturnSigningPolicy)) or 0,
            db.scalar(select(func.count()).select_from(ReturnSignaturePlacement)) or 0,
        )


def _suspend_all() -> None:
    """Suspend EVERY active binding for the fixture actor (the hermetic demo
    tenant seeds an organization-scope viewer binding that would otherwise
    keep the bank covered)."""
    with get_sessionmaker()() as db:
        bindings = list(
            db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.principal_user_id == USER_1,
                    AuthorizationBinding.status == BindingStatus.ACTIVE.value,
                )
            )
        )
        for binding in bindings:
            binding.status = BindingStatus.SUSPENDED.value
        user = db.get(User, USER_1)
        assert user is not None
        user.authorization_version += 1
        db.commit()


@pytest.fixture
def org_reg_configurator(db_client: TestClient) -> TestClient:
    _ = db_client
    _grant(RoleBundle.CONFIGURATOR, scope=_org_scope())
    return db_client


def test_organization_scoped_reg_configurator_configures_placements_and_signing_policies(
    org_reg_configurator: TestClient,
) -> None:
    placed = org_reg_configurator.put(
        PLACEMENT_URL, headers=_auth(), json=PLACEMENT_PAYLOAD
    )
    assert placed.status_code == 200, placed.text
    assert placed.json()["return_code"] == "LCR-NSFR"

    policy = org_reg_configurator.put(POLICY_URL, headers=_auth(), json=POLICY_PAYLOAD)
    assert policy.status_code == 200, policy.text
    assert policy.json()["return_code"] == "LCR-NSFR"
    assert policy.json()["require_signature"] is False

    with _session() as db:
        events = set(
            db.scalars(select(AuditEvent.event_type).where(AuditEvent.actor_user_id == USER_1))
        )
        assert "attestation.signature_placement_template_updated" in events
        assert "attestation.signing_policy_updated" in events


def test_no_scalar_role_configures_either_resource_without_the_binding(
    db_client: TestClient,
) -> None:
    before = _counts()
    for url, payload in ((POLICY_URL, POLICY_PAYLOAD), (PLACEMENT_URL, PLACEMENT_PAYLOAD)):
        response = db_client.put(url, headers=headers(roles=("account_admin",)), json=payload)
        assert response.status_code == 403, (url, response.text)
        assert error_envelope(response)["message"] == ORG_DENIAL
    assert _counts() == before


def test_a_bank_scoped_reg_binding_is_insufficient(
    db_client: TestClient,
) -> None:
    """VAL-CFG-008: configuring a policy for EVERY bank is not a single-bank act."""
    before = _counts()
    _grant(RoleBundle.CONFIGURATOR, scope=_bank_scope())

    for url, payload in ((POLICY_URL, POLICY_PAYLOAD), (PLACEMENT_URL, PLACEMENT_PAYLOAD)):
        denied = db_client.put(url, headers=_auth(), json=payload)
        assert denied.status_code == 403, (url, denied.text)
        assert error_envelope(denied)["message"] == ORG_DENIAL
    assert _counts() == before


def test_wrong_module_and_admin_bundles_never_open_attestation_configuration(
    db_client: TestClient,
) -> None:
    """A Configurator binding over the WRONG module, and Account administration
    itself, are both outside this control's authority."""
    _grant(RoleBundle.CONFIGURATOR, scope=_org_scope(module=ModuleScope.ACCOUNT))
    _grant(RoleBundle.ACCOUNT_ADMIN, scope=_org_scope(module=ModuleScope.ACCOUNT))

    for url, payload in ((POLICY_URL, POLICY_PAYLOAD), (PLACEMENT_URL, PLACEMENT_PAYLOAD)):
        denied = db_client.put(url, headers=_auth(), json=payload)
        assert denied.status_code == 403, (url, denied.text)
        assert error_envelope(denied)["message"] == ORG_DENIAL


def test_reason_validation_survives_the_gate(org_reg_configurator: TestClient) -> None:
    without_reason: dict[str, Any] = {**POLICY_PAYLOAD, "reason": ""}
    response = org_reg_configurator.put(
        POLICY_URL, headers=_auth(), json=without_reason
    )
    assert response.status_code == 422, response.text


def test_reg_configurator_without_capital_reads_still_signs_off_icaap_policies(
    org_reg_configurator: TestClient,
) -> None:
    """Configuration authority over an ICAAP return's signing policy needs no
    Capital read — the two acts stay independent. The hermetic fixture's
    seeded all-module viewer binding is suspended so the principal genuinely
    holds no Capital view while configuring."""
    _suspend_all()
    _grant(RoleBundle.CONFIGURATOR, scope=_org_scope())

    capital = org_reg_configurator.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard", headers=_auth()
    )
    assert capital.status_code == 403, capital.text

    policy = org_reg_configurator.put(
        POLICY_URL, headers=_auth(), json={**POLICY_PAYLOAD, "return_code": "ICAAP-REPORT"}
    )
    assert policy.status_code == 200, policy.text
    assert policy.json()["return_code"] == "ICAAP-REPORT"
