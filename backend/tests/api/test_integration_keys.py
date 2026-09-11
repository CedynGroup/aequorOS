"""Bank-scoped machine principals for API Push.

These requests cross independent authentication and tenant-session
connections, and the services commit their atomic lifecycle transactions.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

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
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import AuditEvent, AuthorizationBinding, Bank, IntegrationKey, RefreshToken, User
from app.services import authentication, authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from app.services.integration_keys import hash_key
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2, headers
from tests.api.test_ingestion import seed_bank
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.storage.inmemory import InMemoryStorageClient

pytestmark = pytest.mark.committing_db

OTHER_BANK_ID = "BK-KEYS0002"
OTHER_TENANT_BANK_ID = "BK-KEYS0003"


def _session(organization_id: str = ORG_1) -> Session:
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    return session


def _add_bank(bank_id: str, organization_id: str = ORG_1) -> None:
    with _session(organization_id) as db:
        db.add(
            Bank(
                id=bank_id,
                organization_id=organization_id,
                name=f"Integration test {bank_id}",
                short_name=bank_id,
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        db.commit()


def _grant_account_admin(
    *,
    organization_id: str = ORG_1,
    user_id: UUID = USER_1,
    role_bundle: RoleBundle = RoleBundle.ACCOUNT_ADMIN,
) -> int:
    with _session(organization_id) as db:
        authorization.create_role_binding(
            db,
            organization_id=organization_id,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=role_bundle,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="exercise bank-scoped integration-key administration",
        )
        user = db.get(User, user_id)
        assert user is not None
        return user.authorization_version


def _admin_headers(
    *,
    organization_id: str = ORG_1,
    user_id: UUID = USER_1,
    role_bundle: RoleBundle = RoleBundle.ACCOUNT_ADMIN,
) -> dict[str, str]:
    version = _grant_account_admin(
        organization_id=organization_id,
        user_id=user_id,
        role_bundle=role_bundle,
    )
    return headers(
        organization_id,
        user_id=user_id,
        roles=("viewer",),
        authorization_version=version,
    )


def _issue(
    client: TestClient,
    *,
    bank_id: str = SAMPLE_BANK_ID,
    label: str = "Core banking nightly push",
    admin_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/integration-keys",
        headers=admin_headers or _admin_headers(),
        json={"bank_id": bank_id, "label": label},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(issued: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {issued['key']}"}


def _open(client: TestClient, bank_id: str, bearer: dict[str, str]):
    return client.post(
        f"/api/v1/banks/{bank_id}/push-batches",
        headers=bearer,
        json={
            "as_of_date": "2026-06-30",
            "idempotency_key": f"integration-key-{uuid4().hex}",
            "reason": "machine-principal authorization proof",
        },
    )


@pytest.mark.parametrize("role_bundle", [RoleBundle.ACCOUNT_ADMIN, RoleBundle.ORG_OWNER])
def test_scoped_account_authority_issues_exact_machine_binding(
    db_client: TestClient,
    role_bundle: RoleBundle,
) -> None:
    seed_bank(db_client)
    issued = _issue(db_client, admin_headers=_admin_headers(role_bundle=role_bundle))

    assert issued["key"].startswith("aeq_live_")
    assert issued["record"]["bank_id"] == SAMPLE_BANK_ID
    assert issued["key"] not in str(issued["record"])

    with _session() as db:
        key = db.get(IntegrationKey, UUID(issued["record"]["id"]))
        assert key is not None
        assert key.bank_id == SAMPLE_BANK_ID
        assert key.key_hash == hash_key(issued["key"])
        service_user = db.get(User, key.service_user_id)
        assert service_user is not None
        assert service_user.auth_provider == "service"
        assert service_user.role == "viewer"
        assert service_user.is_active
        bindings = list(
            db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.principal_user_id == service_user.id
                )
            )
        )
        assert len(bindings) == 1
        binding = bindings[0]
        assert binding.principal_type == "machine"
        assert binding.role_bundle == "integration_writer"
        assert binding.institution_scope == "institution"
        assert binding.institution_id == SAMPLE_BANK_ID
        assert binding.module_scope == "data"
        assert binding.sensitivity_scope == "restricted"
        assert binding.status == "active"
        assert binding.granted_by_type == "tenant_user"
        assert binding.granted_by_id == str(USER_1)


def test_scalar_administrator_cannot_issue_and_unknown_bank_is_hidden(
    db_client: TestClient,
) -> None:
    seed_bank(db_client)
    scalar_only = db_client.post(
        "/api/v1/integration-keys",
        headers=headers(roles=("admin",)),
        json={"bank_id": SAMPLE_BANK_ID, "label": "legacy scalar"},
    )
    assert scalar_only.status_code == 403

    unknown = db_client.post(
        "/api/v1/integration-keys",
        headers=_admin_headers(),
        json={"bank_id": "BK-UNKNOWN1", "label": "unknown target"},
    )
    assert unknown.status_code == 404


def test_issuance_rolls_back_service_user_key_and_binding_together(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_bank(db_client)
    admin_headers = _admin_headers()
    with _session() as db:
        baseline_users = db.scalar(
            select(func.count(User.id)).where(User.auth_provider == "service")
        )
        baseline_keys = db.scalar(select(func.count(IntegrationKey.id)))
        baseline_bindings = db.scalar(
            select(func.count(AuthorizationBinding.id)).where(
                AuthorizationBinding.principal_type == "machine"
            )
        )

    def fail_binding(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected binding failure")

    monkeypatch.setattr(
        "app.services.integration_keys.authorization.create_role_binding",
        fail_binding,
    )
    response = db_client.post(
        "/api/v1/integration-keys",
        headers=admin_headers,
        json={"bank_id": SAMPLE_BANK_ID, "label": "must roll back"},
    )
    assert response.status_code == 500

    with _session() as db:
        assert (
            db.scalar(select(func.count(User.id)).where(User.auth_provider == "service"))
            == baseline_users
        )
        assert db.scalar(select(func.count(IntegrationKey.id))) == baseline_keys
        assert (
            db.scalar(
                select(func.count(AuthorizationBinding.id)).where(
                    AuthorizationBinding.principal_type == "machine"
                )
            )
            == baseline_bindings
        )


def test_all_push_routes_require_and_accept_the_issued_machine(
    db_client: TestClient,
) -> None:
    seed_bank(db_client)
    issued = _issue(db_client)
    bearer = _bearer(issued)

    opened = _open(db_client, SAMPLE_BANK_ID, bearer)
    assert opened.status_code == 201, opened.text
    push_id = opened.json()["push_batch_id"]
    staged = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches/{push_id}/records",
        headers=bearer,
        json={
            "entities": {
                "gl_account": [
                    {
                        "source_reference": "1000",
                        "account_code": "1000",
                        "name": "Cash",
                        "account_class": "ASSET",
                    }
                ]
            }
        },
    )
    assert staged.status_code == 200, staged.text
    status_response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches/{push_id}",
        headers=bearer,
    )
    assert status_response.status_code == 200, status_response.text
    committed = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches/{push_id}/commit",
        headers=bearer,
    )
    assert committed.status_code == 201, committed.text


def test_integration_key_cannot_read_ordinary_tenant_data(
    db_client: TestClient,
) -> None:
    seed_bank(db_client)
    issued = _issue(db_client)
    bearer = _bearer(issued)

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods/{uuid4()}/facts",
        headers=bearer,
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == (
        "Integration keys are valid only for API Push."
    )
    with _session() as db:
        key = db.get(IntegrationKey, UUID(issued["record"]["id"]))
        assert key is not None
        assert key.last_used_at is None


def test_human_analyst_binding_cannot_satisfy_any_machine_push_route(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_bank(db_client)
    with _session() as db:
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ANALYST,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.DATA,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="prove human Analyst cannot satisfy machine ingest",
        )
        user = db.get(User, USER_1)
        assert user is not None
        analyst_headers = headers(
            roles=("analyst",),
            authorization_version=user.authorization_version,
        )

    def unexpected_service_call(*_args: object, **_kwargs: object) -> None:
        pytest.fail("machine authorization denial must precede the push service")

    monkeypatch.setattr(
        "app.features.push_data.push_ingestion.open_push_batch",
        unexpected_service_call,
    )
    monkeypatch.setattr(
        "app.features.push_data.push_ingestion.stage_push_records",
        unexpected_service_call,
    )
    monkeypatch.setattr(
        "app.features.push_data.push_ingestion.commit_push_batch",
        unexpected_service_call,
    )
    monkeypatch.setattr(
        "app.features.push_data.push_ingestion.get_push_batch",
        unexpected_service_call,
    )
    push_id = uuid4()
    responses = [
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches",
            headers=analyst_headers,
            json={
                "as_of_date": "2026-06-30",
                "idempotency_key": "human-open-denied",
                "reason": "must not reach service",
            },
        ),
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches/{push_id}/records",
            headers=analyst_headers,
            json={
                "entities": {
                    "gl_account": [
                        {
                            "source_reference": "1000",
                            "account_code": "1000",
                            "name": "Cash",
                            "account_class": "ASSET",
                        }
                    ]
                }
            },
        ),
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches/{push_id}/commit",
            headers=analyst_headers,
        ),
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches/{push_id}",
            headers=analyst_headers,
        ),
    ]
    assert {response.status_code for response in responses} == {403}


def test_wrong_bank_is_404_and_denial_has_no_side_effect(
    db_client: TestClient,
    storage_engine: InMemoryStorageClient,
) -> None:
    seed_bank(db_client)
    _add_bank(OTHER_BANK_ID)
    issued = _issue(db_client)
    key_id = UUID(issued["record"]["id"])

    response = _open(db_client, OTHER_BANK_ID, _bearer(issued))
    assert response.status_code == 404

    with _session() as db:
        key = db.get(IntegrationKey, key_id)
        assert key is not None
        assert key.last_used_at is None
        push_events = db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.event_type.like("push_batch.%"))
        )
        assert push_events == 0
    assert not storage_engine._objects


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("module_scope", "liq"),
        ("sensitivity_scope", "confidential"),
        ("institution_id", OTHER_BANK_ID),
        ("status", "suspended"),
        ("valid_until", "expired"),
    ],
)
def test_wrong_or_inactive_binding_denies_before_side_effects(
    db_client: TestClient,
    storage_engine: InMemoryStorageClient,
    change: str,
    value: str,
) -> None:
    seed_bank(db_client)
    _add_bank(OTHER_BANK_ID)
    issued = _issue(db_client)
    key_id = UUID(issued["record"]["id"])
    with _session() as db:
        key = db.get(IntegrationKey, key_id)
        assert key is not None
        binding = db.scalar(
            select(AuthorizationBinding).where(
                AuthorizationBinding.principal_user_id == key.service_user_id
            )
        )
        assert binding is not None
        if change == "valid_until":
            binding.valid_from = utc_now() - timedelta(days=2)
            binding.valid_until = utc_now() - timedelta(days=1)
        else:
            setattr(binding, change, value)
        db.commit()

    response = _open(db_client, SAMPLE_BANK_ID, _bearer(issued))
    assert response.status_code == 403

    with _session() as db:
        key = db.get(IntegrationKey, key_id)
        assert key is not None
        assert key.last_used_at is None
        assert (
            db.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.event_type.like("push_batch.%"))
            )
            == 0
        )
    assert not storage_engine._objects


def test_partial_bindings_do_not_compose_machine_ingest(
    db_client: TestClient,
) -> None:
    seed_bank(db_client)
    issued = _issue(db_client)
    key_id = UUID(issued["record"]["id"])
    with _session() as db:
        key = db.get(IntegrationKey, key_id)
        assert key is not None
        original = db.scalar(
            select(AuthorizationBinding).where(
                AuthorizationBinding.principal_user_id == key.service_user_id
            )
        )
        assert original is not None
        original.module_scope = "liq"
        db.add(
            AuthorizationBinding(
                organization_id=ORG_1,
                principal_user_id=key.service_user_id,
                principal_type="machine",
                role_bundle="integration_writer",
                institution_scope="institution",
                institution_id=SAMPLE_BANK_ID,
                module_scope="data",
                sensitivity_scope="confidential",
                granted_by_type="system",
                granted_by_id="test-suite",
                grant_reason="anti-composition proof",
                status="active",
                valid_from=utc_now(),
            )
        )
        db.commit()

    response = _open(db_client, SAMPLE_BANK_ID, _bearer(issued))
    assert response.status_code == 403


def test_legacy_unscoped_key_is_listable_revocable_and_denied(
    db_client: TestClient,
) -> None:
    seed_bank(db_client)
    raw = "aeq_live_legacyKeyForMachinePrincipalTestOnly"
    with _session() as db:
        service_user = User(
            organization_id=ORG_1,
            email="legacy-integration@service.aequoros.invalid",
            display_name="Legacy integration",
            role="analyst",
            auth_provider="service",
            is_active=True,
        )
        db.add(service_user)
        db.flush()
        key = IntegrationKey(
            organization_id=ORG_1,
            bank_id=None,
            service_user_id=service_user.id,
            label="Legacy unscoped feed",
            key_prefix="aeq_live_LEGA…",
            key_hash=hash_key(raw),
            created_by=USER_1,
        )
        db.add(key)
        db.commit()
        key_id = key.id

    admin_headers = _admin_headers()
    listed = db_client.get("/api/v1/integration-keys", headers=admin_headers)
    assert listed.status_code == 200
    legacy = next(item for item in listed.json()["keys"] if item["id"] == str(key_id))
    assert legacy["bank_id"] is None

    denied = _open(
        db_client,
        SAMPLE_BANK_ID,
        {"Authorization": f"Bearer {raw}"},
    )
    assert denied.status_code == 404
    with _session() as db:
        stored = db.get(IntegrationKey, key_id)
        assert stored is not None
        assert stored.last_used_at is None

    revoked = db_client.post(
        f"/api/v1/integration-keys/{key_id}/revoke",
        headers=admin_headers,
        json={"reason": "rotate legacy unscoped key"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None


def test_revocation_ends_key_binding_identity_and_machine_sessions(
    db_client: TestClient,
) -> None:
    seed_bank(db_client)
    admin_headers = _admin_headers()
    issued = _issue(db_client, admin_headers=admin_headers)
    key_id = UUID(issued["record"]["id"])
    with _session() as db:
        key = db.get(IntegrationKey, key_id)
        assert key is not None
        service_user = db.get(User, key.service_user_id)
        assert service_user is not None
        tokens = authentication.issue_tokens(db, service_user)
        version = service_user.authorization_version
        binding_id = db.scalar(
            select(AuthorizationBinding.id).where(
                AuthorizationBinding.principal_user_id == service_user.id
            )
        )
        assert binding_id is not None
        service_user_id = service_user.id

    response = db_client.post(
        f"/api/v1/integration-keys/{key_id}/revoke",
        headers=admin_headers,
        json={"reason": "middleware rotation complete"},
    )
    assert response.status_code == 200, response.text

    with _session() as db:
        key = db.get(IntegrationKey, key_id)
        binding = db.get(AuthorizationBinding, binding_id)
        service_user = db.get(User, service_user_id)
        assert key is not None and key.revoked_at is not None
        assert binding is not None
        assert binding.status == BindingStatus.REVOKED.value
        assert binding.revoked_by_type == GrantorType.TENANT_USER.value
        assert binding.revoked_by_id == str(USER_1)
        assert binding.revoked_reason == "middleware rotation complete"
        assert service_user is not None and not service_user.is_active
        assert service_user.authorization_version == version + 1
        refresh = db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_key(tokens.refresh_token))
        )
        assert refresh is not None
        assert refresh.revoked_reason == "authorization_changed"

    assert _open(db_client, SAMPLE_BANK_ID, _bearer(issued)).status_code == 401


def test_revocation_rolls_back_every_machine_state_on_failure(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_bank(db_client)
    admin_headers = _admin_headers()
    issued = _issue(db_client, admin_headers=admin_headers)
    key_id = UUID(issued["record"]["id"])

    def fail_invalidation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected invalidation failure")

    monkeypatch.setattr(
        "app.services.integration_keys.authorization.invalidate_user_authorization",
        fail_invalidation,
    )
    response = db_client.post(
        f"/api/v1/integration-keys/{key_id}/revoke",
        headers=admin_headers,
        json={"reason": "must roll back"},
    )
    assert response.status_code == 500

    with _session() as db:
        key = db.get(IntegrationKey, key_id)
        assert key is not None
        service_user = db.get(User, key.service_user_id)
        binding = db.scalar(
            select(AuthorizationBinding).where(
                AuthorizationBinding.principal_user_id == key.service_user_id
            )
        )
        assert key.revoked_at is None
        assert service_user is not None and service_user.is_active
        assert binding is not None and binding.status == BindingStatus.ACTIVE.value


def test_integration_writer_cannot_be_revoked_outside_key_lifecycle(
    db_client: TestClient,
) -> None:
    seed_bank(db_client)
    owner_headers = _admin_headers(role_bundle=RoleBundle.ORG_OWNER)
    issued = _issue(db_client, admin_headers=owner_headers)
    with _session() as db:
        key = db.get(IntegrationKey, UUID(issued["record"]["id"]))
        assert key is not None
        binding_id = db.scalar(
            select(AuthorizationBinding.id).where(
                AuthorizationBinding.principal_user_id == key.service_user_id
            )
        )
        assert binding_id is not None

    response = db_client.post(
        f"/api/v1/authorization/bindings/{binding_id}/revoke",
        headers=owner_headers,
        json={"reason": "attempt split revocation"},
    )
    assert response.status_code == 409
    with _session() as db:
        binding = db.get(AuthorizationBinding, binding_id)
        key = db.get(IntegrationKey, UUID(issued["record"]["id"]))
        assert binding is not None and binding.status == BindingStatus.ACTIVE.value
        assert key is not None and key.revoked_at is None


def test_other_tenant_account_admin_cannot_see_or_revoke_key(
    db_client: TestClient,
) -> None:
    seed_bank(db_client)
    issued = _issue(db_client)
    _add_bank(OTHER_TENANT_BANK_ID, ORG_2)
    other_admin = _admin_headers(organization_id=ORG_2, user_id=USER_2)

    listed = db_client.get("/api/v1/integration-keys", headers=other_admin)
    revoked = db_client.post(
        f"/api/v1/integration-keys/{issued['record']['id']}/revoke",
        headers=other_admin,
        json={"reason": "cross-tenant probe"},
    )
    assert listed.status_code == 200
    assert listed.json()["keys"] == []
    assert revoked.status_code == 404


def test_lifecycle_audit_names_bank_binding_and_service_identity(
    db_client: TestClient,
) -> None:
    seed_bank(db_client)
    admin_headers = _admin_headers()
    issued = _issue(db_client, admin_headers=admin_headers)
    key_id = UUID(issued["record"]["id"])
    db_client.post(
        f"/api/v1/integration-keys/{key_id}/revoke",
        headers=admin_headers,
        json={"reason": "audit proof"},
    )

    with _session() as db:
        events = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_id == str(key_id))
                .order_by(AuditEvent.created_at)
            )
        )
        assert [event.event_type for event in events] == [
            "integration_key.issued",
            "integration_key.revoked",
        ]
        assert events[0].details["bank_id"] == SAMPLE_BANK_ID
        assert events[0].details["authorization_binding_id"]
        assert events[1].details["authorization_binding_ids"]
        assert events[1].details["service_user_id"]
