"""SSO connection config endpoints: admin CRUD (write-only secret), public
status probe, and the internal-key-gated client-config fetch for the dashboard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.api.helpers import headers

MASTER_KEY = "sso-config-test-master-key"
SECRET = "google-client-secret-that-must-never-leak"
INTERNAL_KEY = "internal-key-for-dashboard-fetch"

_PAYLOAD = {
    "issuer": "https://accounts.google.com",
    "client_id": "abc.apps.googleusercontent.com",
    "client_secret": SECRET,
    "allowed_email_domains": ["Bank.Example", "bank.example", " "],
    "enabled": True,
}


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


@pytest.fixture
def internal_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSO_INTERNAL_KEY", INTERNAL_KEY)
    get_settings.cache_clear()


def test_admin_upserts_connection_and_secret_is_write_only(
    db_client: TestClient, vault_key: None
) -> None:
    put = db_client.put("/api/v1/auth/sso/connection", json=_PAYLOAD, headers=headers())
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["client_secret_set"] is True
    assert body["enabled"] is True
    assert body["allowed_email_domains"] == ["bank.example"]  # normalized + deduped
    assert SECRET not in put.text  # the secret never comes back

    got = db_client.get("/api/v1/auth/sso/connection", headers=headers())
    assert got.status_code == 200
    assert got.json()["client_secret_set"] is True
    assert SECRET not in got.text

    # Update without a secret keeps the stored one.
    update = {**_PAYLOAD, "client_secret": None, "client_id": "rotated-client-id"}
    put2 = db_client.put("/api/v1/auth/sso/connection", json=update, headers=headers())
    assert put2.status_code == 200
    assert put2.json()["client_id"] == "rotated-client-id"
    assert put2.json()["client_secret_set"] is True


def test_enabling_without_a_secret_is_refused(db_client: TestClient, vault_key: None) -> None:
    payload = {**_PAYLOAD, "client_secret": None}
    r = db_client.put("/api/v1/auth/sso/connection", json=payload, headers=headers())
    assert r.status_code == 422


def test_jit_requires_a_domain_allow_list(db_client: TestClient, vault_key: None) -> None:
    refused = db_client.put(
        "/api/v1/auth/sso/connection",
        json={**_PAYLOAD, "allowed_email_domains": [], "jit_enabled": True},
        headers=headers(),
    )
    assert refused.status_code == 422
    assert "domain" in refused.json()["error"]["message"].lower()

    ok = db_client.put(
        "/api/v1/auth/sso/connection",
        json={**_PAYLOAD, "jit_enabled": True},
        headers=headers(),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["jit_enabled"] is True


def test_pasted_email_is_reduced_to_its_domain(db_client: TestClient, vault_key: None) -> None:
    r = db_client.put(
        "/api/v1/auth/sso/connection",
        json={**_PAYLOAD, "allowed_email_domains": ["eric@aequoros.com"]},
        headers=headers(),
    )
    assert r.status_code == 200
    assert r.json()["allowed_email_domains"] == ["aequoros.com"]


def test_non_admin_cannot_read_or_write_connection(db_client: TestClient) -> None:
    for role in ("approver", "analyst", "viewer"):
        assert (
            db_client.get("/api/v1/auth/sso/connection", headers=headers(roles=(role,))).status_code
            == 403
        )
        assert (
            db_client.put(
                "/api/v1/auth/sso/connection", json=_PAYLOAD, headers=headers(roles=(role,))
            ).status_code
            == 403
        )
        assert (
            db_client.get(
                "/api/v1/auth/sso/access-requests", headers=headers(roles=(role,))
            ).status_code
            == 403
        )


def test_public_status_reflects_enabled_connection(db_client: TestClient, vault_key: None) -> None:
    assert db_client.get("/api/v1/auth/sso/status").json() == {"enabled": False}
    db_client.put("/api/v1/auth/sso/connection", json=_PAYLOAD, headers=headers())
    assert db_client.get("/api/v1/auth/sso/status").json() == {"enabled": True}


def test_client_config_requires_the_internal_key(
    db_client: TestClient, vault_key: None, internal_key: None
) -> None:
    db_client.put("/api/v1/auth/sso/connection", json=_PAYLOAD, headers=headers())

    assert db_client.get("/api/v1/auth/sso/client-config").status_code == 401
    assert (
        db_client.get(
            "/api/v1/auth/sso/client-config", headers={"X-Internal-Auth": "wrong"}
        ).status_code
        == 401
    )

    ok = db_client.get("/api/v1/auth/sso/client-config", headers={"X-Internal-Auth": INTERNAL_KEY})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["enabled"] is True
    assert body["issuer"] == "https://accounts.google.com"
    assert body["client_secret"] == SECRET  # round-trips through the vault


def test_client_config_is_disabled_when_no_internal_key_configured(
    db_client: TestClient,
) -> None:
    r = db_client.get("/api/v1/auth/sso/client-config", headers={"X-Internal-Auth": "anything"})
    assert r.status_code == 404
