from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
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
from app.core.key_management.types import KeyReference, KeyStatus
from app.identity.models.user import User
from app.identity.service import authorization
from tests.api import test_impersonation_auth as impersonation
from tests.core import test_bank_field_encryption as fields
from tests.support.helpers import ORG_1, ORG_2, headers

provider = fields.provider
URL = f"/api/v1/banks/{fields.BANK_A}"


@pytest.fixture
def numeric_viewer(db_session: Session, provider: LocalKeyProvider) -> dict[str, str]:
    user = User(id=uuid4(), organization_id=ORG_1, email="numeric-viewer@example.test")
    db_session.add(user)
    db_session.flush()
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            fields.BANK_A,
            ModuleScope.LIQUIDITY,
            SensitivityScope.CONFIDENTIAL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Synthetic numeric-only recovery",
    )
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            fields.BANK_A,
            ModuleScope.DATA,
            SensitivityScope.CONFIDENTIAL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Synthetic reporting-period read without identity access",
    )
    db_session.commit()
    return headers(
        user_id=user.id, roles=("viewer",), authorization_version=user.authorization_version
    )


@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
def test_bank_detail_probes_readiness_for_numeric_viewer_with_empty_register(
    db_client: TestClient,
    db_session: Session,
    provider: LocalKeyProvider,
    numeric_viewer: dict[str, str],
    status: KeyStatus,
) -> None:
    fields.connect(db_session, provider)
    initial = db_client.get(URL, headers=numeric_viewer)
    assert initial.status_code == 200, initial.text
    assert initial.json()["liquidity_monitoring_access"] is True
    assert json.loads(initial.headers["X-Aequoros-Protected-Data"]) == {fields.BANK_A: "available"}
    assert db_client.get(f"{URL}/encryption-key", headers=numeric_viewer).status_code == 403
    provider.set_status(fields.KEY_A, status)
    locked = db_client.get(URL, headers=numeric_viewer)
    assert locked.status_code == 200, locked.text
    assert locked.json() == initial.json()
    assert json.loads(locked.headers["X-Aequoros-Protected-Data"]) == {
        fields.BANK_A: "revoked" if status == KeyStatus.DISABLED else "unavailable"
    }
    assert locked.headers["cache-control"] == "no-store"
    numeric = db_client.get(f"{URL}/reporting-periods", headers=numeric_viewer)
    assert numeric.status_code == 200, numeric.text
    assert "X-Aequoros-Protected-Data" not in numeric.headers
    provider.set_status(fields.KEY_A, KeyStatus.ACTIVE)
    recovered = db_client.get(URL, headers=numeric_viewer)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json() == initial.json()
    assert json.loads(recovered.headers["X-Aequoros-Protected-Data"]) == {
        fields.BANK_A: "available"
    }


@pytest.mark.parametrize("first_restored", [fields.KEY_A, fields.NEXT])
def test_bank_readiness_preserves_current_and_retained_index_severity(
    db_client: TestClient,
    db_session: Session,
    provider: LocalKeyProvider,
    first_restored: KeyReference,
) -> None:
    fields.connect(db_session, provider)
    row = registry.scoped_key(db_session, bank_id=fields.BANK_A, organization_id=ORG_1)
    registry.rotate(db_session, row, fields.NEXT, lambda _name: provider)
    db_session.commit()
    provider.set_status(first_restored, KeyStatus.DISABLED)
    remaining = fields.NEXT if first_restored == fields.KEY_A else fields.KEY_A
    provider.set_status(remaining, KeyStatus.UNAVAILABLE)
    for expected in ("revoked", "unavailable", "available"):
        response = db_client.get(URL, headers=headers(roles=("viewer",)))
        assert response.status_code == 200, response.text
        assert json.loads(response.headers["X-Aequoros-Protected-Data"]) == {
            fields.BANK_A: expected
        }
        provider.set_status(
            first_restored if expected == "revoked" else remaining, KeyStatus.ACTIVE
        )


def test_bank_readiness_never_unwraps_for_support_or_uncovered_banks(
    db_client: TestClient,
    db_session: Session,
    provider: LocalKeyProvider,
    numeric_viewer: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields.connect(db_session, provider)

    def refuse_unwrap(*_args: object, **_kwargs: object) -> bytes:
        pytest.fail("No provider access is allowed for support or uncovered banks")

    monkeypatch.setattr(provider, "unwrap", refuse_unwrap)
    for url, auth in (
        (URL, headers(ORG_2)),
        (URL.replace(fields.BANK_A, fields.BANK_B), numeric_viewer),
    ):
        denied = db_client.get(url, headers=auth)
        assert denied.status_code == 404, denied.text
        assert "X-Aequoros-Protected-Data" not in denied.headers
    response = db_client.get(
        URL,
        headers=impersonation._bearer(impersonation._impersonation_token(ORG_1)),  # pyright: ignore[reportPrivateUsage]
    )
    assert response.status_code == 200, response.text
    assert json.loads(response.headers["X-Aequoros-Protected-Data"]) == {fields.BANK_A: "support"}
