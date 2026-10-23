"""Key administration using synthetic banks and local key material."""

from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.schemas import BankKeyConnect
from app.core.key_management.types import KeyStatus, KeyUnavailableError
from app.core.tenancy import TenantContext
from app.identity.models.bank import Bank
from app.identity.service import authorization, bank_encryption
from app.models import AuditEvent, User
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

BANK = "BK-KEYUX001"
OTHER = "BK-KEYUX002"


def admin_headers(db: Session) -> dict[str, str]:
    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ACCOUNT_ADMIN,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION, None, ModuleScope.ACCOUNT, SensitivityScope.RESTRICTED
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise bank encryption administration.",
    )
    user = db.get(User, USER_1)
    assert user is not None
    db.commit()
    return headers(
        ORG_1, user_id=USER_1, roles=("viewer",), authorization_version=user.authorization_version
    )


CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
KEY = BankKeyConnect(
    key_id="arn:aws:kms:us-east-1:123456789012:key/synthetic-one",
    region="us-east-1",
    owner_account="123456789012",
)
NEXT = BankKeyConnect(
    key_id="arn:aws:kms:us-east-1:123456789012:key/synthetic-two",
    region="us-east-1",
    owner_account="123456789012",
)


@pytest.fixture
def local_keys(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> LocalKeyProvider:
    for identity, organization in ((BANK, ORG_1), (OTHER, ORG_2)):
        db_session.add(
            Bank(
                id=identity,
                organization_id=organization,
                name="Synthetic key UX bank",
                short_name="Synthetic",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
                storage_slug=f"synthetic-{identity.lower()}",
            )
        )
    db_session.commit()
    provider = LocalKeyProvider()
    provider.add_key(KEY.reference())
    provider.add_key(NEXT.reference())

    def factory(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", factory)
    return provider


def test_admin_connect_test_rotate_and_confirm_schedule(
    db_client: TestClient, db_session: Session, local_keys: LocalKeyProvider
) -> None:
    admin = admin_headers(db_session)
    url = f"/api/v1/banks/{BANK}/encryption-key"
    assert db_client.get(url, headers=admin).json()["connected"] is False
    probe = db_client.post(f"{url}/test", headers=admin, json=KEY.model_dump())
    assert probe.status_code == 200, probe.text
    assert probe.json()["passed"] is True
    connected = db_client.put(url, headers=admin, json=KEY.model_dump())
    assert connected.status_code == 200, connected.text
    old_version = cast(str, connected.json()["current_version"])
    assert old_version and connected.json()["last_successful_use_at"]
    assert db_client.put(url, headers=admin, json=NEXT.model_dump()).status_code == 409
    report = {
        "key_id": KEY.key_id,
        "period_days": 365,
        "reason": "Synthetic administrator enabled AWS rotation.",
    }
    confirmed = db_client.post(f"{url}/schedule-confirmation", headers=admin, json=report)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["rotation_period_days"] == 365
    assert confirmed.json()["rotation_confirmed_at"]
    rotated = db_client.post(
        f"{url}/rotate",
        headers=admin,
        json={**NEXT.model_dump(), "reason": "Synthetic planned bank key replacement."},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["current_version"] != old_version
    assert rotated.json()["last_rotated_at"]
    assert rotated.json()["rotation_confirmed_at"] is None
    assert (
        db_client.post(f"{url}/schedule-confirmation", headers=admin, json=report).status_code
        == 409
    )
    events = set(
        db_session.scalars(select(AuditEvent.event_type).where(AuditEvent.entity_id == BANK))
    )
    assert {
        "bank_key.connected",
        "bank_key.tested",
        "bank_key.rotated",
        "bank_key.schedule_confirmed",
    } <= events


@pytest.mark.parametrize(
    "suffix,method",
    [
        ("", "get"),
        ("", "put"),
        ("/test", "post"),
        ("/rotate", "post"),
        ("/schedule-confirmation", "post"),
    ],
)
def test_non_admin_and_other_tenant_are_denied(
    db_client: TestClient,
    db_session: Session,
    local_keys: LocalKeyProvider,
    suffix: str,
    method: str,
) -> None:
    payload = {**KEY.model_dump(), "reason": "Synthetic authorization probe."}
    denied = db_client.request(
        method.upper(),
        f"/api/v1/banks/{BANK}/encryption-key{suffix}",
        headers=headers(),
        json=payload if method != "get" else None,
    )
    assert denied.status_code == 403, denied.text
    foreign = db_client.request(
        method.upper(),
        f"/api/v1/banks/{OTHER}/encryption-key{suffix}",
        headers=admin_headers(db_session),
        json=payload if method != "get" else None,
    )
    assert foreign.status_code == 404, foreign.text


def test_disabled_probe_preserves_last_success(
    db_session: Session, local_keys: LocalKeyProvider
) -> None:
    before = bank_encryption.connect(db_session, CTX, BANK, KEY)
    local_keys.set_status(KEY.reference(), KeyStatus.DISABLED)
    result = bank_encryption.test_connection(db_session, CTX, BANK, KEY)
    assert not result.passed and "disabled" in result.message
    after = bank_encryption.status(db_session, CTX, BANK)
    assert after.current_version == before.current_version
    assert after.last_successful_use_at == before.last_successful_use_at


def test_probe_really_unwraps(
    db_session: Session, local_keys: LocalKeyProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bytes] = []

    def refuse(_key: object, wrapped: bytes, _context: object) -> bytes:
        calls.append(wrapped)
        raise KeyUnavailableError("Access denied for kms:Decrypt. Add this permission and retry.")

    monkeypatch.setattr(local_keys, "unwrap", refuse)
    result = bank_encryption.test_connection(db_session, CTX, BANK, KEY)
    assert not result.passed and "kms:Decrypt" in result.message
    assert len(calls) == 1 and calls[0]
    assert not bank_encryption.status(db_session, CTX, BANK).connected
