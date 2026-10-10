from __future__ import annotations

from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.types import KeyReference, KeyStatus
from app.models.operator import OperatorAuditLog
from app.operator.deps import OperatorContext, get_operator_context
from tests.operator.conftest import BANK_KEY, operator_headers, provision_payload, start_inspection

_BASE = "/operator/v1/tenants/{org_id}/banks/{bank_id}/encryption-key"
# This is the staff-plane IDOR census. Each new reference-bearing route gets
# the same executable wrong-parent test, in addition to the tenant census.
KEY_REFERENCE_CENSUS = (
    ("GET", _BASE),
    ("PUT", _BASE),
    ("POST", _BASE + "/rotate"),
    ("POST", _BASE + "/check"),
)


def _provision(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    body = cast(dict[str, object], response.json())
    assert body["succeeded"] is True
    start_inspection(client, str(body["organization_id"]))
    return str(body["organization_id"]), str(body["bank_id"])


def test_connected_key_is_bank_scoped_and_audited(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    url = _BASE.format(org_id=organization_id, bank_id=bank_id)
    response = operator_client.get(url, headers=operator_headers())
    assert response.status_code == 200
    body = cast(dict[str, object], response.json())
    assert body["bank_id"] == bank_id
    assert body["key_id"] == BANK_KEY.key_id
    assert body["owner_account"] == BANK_KEY.owner_account
    row = operator_db.scalar(select(BankEncryptionKey).where(BankEncryptionKey.bank_id == bank_id))
    assert row is not None and row.organization_id == organization_id
    audit = operator_db.scalar(
        select(OperatorAuditLog).where(OperatorAuditLog.action == "tenants.provision")
    )
    assert audit is not None
    assert audit.detail["encryption_key"]["key_id"] == BANK_KEY.key_id


@pytest.mark.parametrize(("method", "path"), KEY_REFERENCE_CENSUS)
def test_bank_key_routes_refuse_a_bank_under_another_organization(
    operator_client: TestClient,
    method: str,
    path: str,
) -> None:
    _organization_id, bank_id = _provision(operator_client)
    other_organization, _other_bank = _provision(operator_client)
    payload = {
        "provider": "aws_kms",
        "key_id": BANK_KEY.key_id,
        "region": BANK_KEY.region,
        "owner_account": BANK_KEY.owner_account,
    }
    if path.endswith("/rotate"):
        payload["reason"] = "Bank's planned key rotation"
    response = operator_client.request(
        method,
        path.format(org_id=other_organization, bank_id=bank_id),
        headers=operator_headers(),
        json=payload if method == "PUT" or path.endswith("/rotate") else None,
    )
    assert response.status_code == 404


def test_bank_key_configuration_requires_operator_administration(
    operator_client: TestClient,
) -> None:
    organization_id, bank_id = _provision(operator_client)
    app = cast(FastAPI, operator_client.app)
    app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        email="developer@example.test",
        auth_mode="dev",
        role="developer",
    )
    response = operator_client.get(
        _BASE.format(org_id=organization_id, bank_id=bank_id),
        headers=operator_headers(),
    )
    assert response.status_code == 403


def test_rotation_and_health_are_audited_and_owner_account_cannot_change(
    operator_client: TestClient,
    operator_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id, bank_id = _provision(operator_client)
    keys = LocalKeyProvider()
    keys.add_key(BANK_KEY)
    replacement = KeyReference(
        "aws_kms", BANK_KEY.key_id + "-replacement", BANK_KEY.region, BANK_KEY.owner_account
    )
    keys.add_key(replacement)

    def providers(_name: str) -> LocalKeyProvider:
        return keys

    monkeypatch.setattr(registry, "provider_for", providers)
    base = _BASE.format(org_id=organization_id, bank_id=bank_id)
    response = operator_client.post(
        base + "/rotate",
        headers=operator_headers(),
        json={
            "key_id": replacement.key_id,
            "region": replacement.region,
            "owner_account": replacement.owner_account,
            "reason": "Bank's planned key replacement",
        },
    )
    assert response.status_code == 200, response.text
    assert cast(dict[str, object], response.json())["key_id"] == replacement.key_id
    keys.set_status(replacement, KeyStatus.DISABLED)
    response = operator_client.post(base + "/check", headers=operator_headers())
    assert response.status_code == 200
    assert cast(dict[str, object], response.json())["status"] == "disabled"
    response = operator_client.post(
        base + "/rotate",
        headers=operator_headers(),
        json={
            "key_id": BANK_KEY.key_id,
            "region": BANK_KEY.region,
            "owner_account": "999999999999",
            "reason": "Attempt to replace bank ownership",
        },
    )
    assert response.status_code == 503
    actions = set(operator_db.scalars(select(OperatorAuditLog.action)))
    assert {"bank_key.rotated", "bank_key.checked"} <= actions


def test_bank_key_routes_require_an_active_inspection(operator_client: TestClient) -> None:
    response = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    body = cast(dict[str, object], response.json())
    response = operator_client.get(
        _BASE.format(org_id=body["organization_id"], bank_id=body["bank_id"]),
        headers=operator_headers(),
    )
    assert response.status_code == 403
