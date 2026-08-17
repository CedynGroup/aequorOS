"""Temenos core-banking connection management API on the ACTUAL primary.

Credential handling is the load-bearing concern: credentials go in through
request bodies, round-trip the encrypted vault, and must NEVER appear in any
response — only status, fingerprint, and expiry do. The T24 session provider is
the offline SimulatedSessionProvider — no core is ever contacted. Invariants:
activation on valid credentials (TESTING on a bad shape), unique display names
(409), rotation validates first, disable/enable/revoke states, the test endpoint
reports the pull plan, onboarding leaves exactly ONE active T24 mapping, pull /
backfill enqueue one job per date, tenant isolation. The vault key is set by the
test (never read from .env). Opt-in via REAL_DATA_DATABASE_URL, rolled back
(tests/real_data.py).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.real_data import REAL_BANK_ID, other_headers, real_headers, requires_real_data

pytestmark = requires_real_data

MASTER_KEY = "temenos-api-test-master-key"
SECRET = "svc-password-that-must-never-leak"
OFS_CREDENTIALS = {"username": "SVC.AEQUOROS", "password": SECRET}
BASE = f"/api/v1/banks/{REAL_BANK_ID}/temenos/connections"


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


def _unique_name(stem: str = "Core OFS") -> str:
    """Never collide with a connection the real bank may already hold."""
    return f"{stem} [{uuid4().hex[:8]}]"


def _create(
    client: TestClient,
    *,
    mode: str = "OFS",
    display_name: str | None = None,
    credentials: dict[str, Any] | None = None,
    domains: list[str] | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "connection_mode": mode,
        "display_name": display_name or _unique_name(),
        "endpoint": "ofs://sample-bank",
        "credentials": credentials if credentials is not None else OFS_CREDENTIALS,
    }
    if domains is not None:
        payload["domains"] = domains
    return client.post(BASE, headers=real_headers(), json=payload)


def _by_id(client: TestClient, connection_id: str) -> dict[str, Any]:
    response = client.get(BASE, headers=real_headers())
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["connections"] if item["id"] == connection_id)


@pytest.mark.usefixtures("vault_key")
def test_create_activates_on_valid_credentials(real_client: TestClient) -> None:
    response = _create(real_client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["connection_mode"] == "OFS"
    assert body["credential_fingerprint"]
    # every supported OFS domain enabled by default
    assert "POSITIONS_LOANS" in body["domains"]


@pytest.mark.usefixtures("vault_key")
def test_credentials_never_appear_in_any_response(real_client: TestClient) -> None:
    created = _create(real_client)
    assert created.status_code == 201, created.text
    assert SECRET not in created.text
    assert "password" not in created.json()
    listed = real_client.get(BASE, headers=real_headers())
    assert listed.status_code == 200
    assert SECRET not in listed.text
    assert "credential_ciphertext" not in listed.text


@pytest.mark.usefixtures("vault_key")
def test_create_with_bad_credential_shape_stays_testing(real_client: TestClient) -> None:
    response = _create(real_client, credentials={"username": "SVC"})  # no password
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "TESTING"
    assert body["validation_error"]


@pytest.mark.usefixtures("vault_key")
def test_duplicate_display_name_conflicts(real_client: TestClient) -> None:
    name = _unique_name()
    assert _create(real_client, display_name=name).status_code == 201
    dup = _create(real_client, display_name=name)
    assert dup.status_code == 409


@pytest.mark.usefixtures("vault_key")
def test_rotate_credentials_validates_first(real_client: TestClient) -> None:
    conn_id = _create(real_client).json()["id"]
    # bad rotation is rejected, nothing changes
    before = _by_id(real_client, conn_id)
    bad = real_client.patch(
        f"{BASE}/{conn_id}",
        headers=real_headers(),
        json={"credentials": {"username": "SVC"}},
    )
    assert bad.status_code == 422
    assert (
        _by_id(real_client, conn_id)["credential_fingerprint"] == before["credential_fingerprint"]
    )
    # good rotation swaps the fingerprint
    good = real_client.patch(
        f"{BASE}/{conn_id}",
        headers=real_headers(),
        json={"credentials": {"username": "SVC.NEW", "password": "another-secret"}},
    )
    assert good.status_code == 200, good.text
    assert good.json()["credential_fingerprint"] != before["credential_fingerprint"]
    assert "another-secret" not in good.text


@pytest.mark.usefixtures("vault_key")
def test_disable_enable_revoke_lifecycle(real_client: TestClient) -> None:
    conn_id = _create(real_client).json()["id"]
    assert (
        real_client.post(f"{BASE}/{conn_id}/disable", headers=real_headers()).json()["status"]
        == "DISABLED"
    )
    assert (
        real_client.post(f"{BASE}/{conn_id}/enable", headers=real_headers()).json()["status"]
        == "ACTIVE"
    )
    revoked = real_client.delete(f"{BASE}/{conn_id}", headers=real_headers())
    assert revoked.json()["status"] == "REVOKED"
    # revoked row is kept but its credential is wiped
    assert revoked.json()["credential_fingerprint"] is None
    assert _by_id(real_client, conn_id)["status"] == "REVOKED"


@pytest.mark.usefixtures("vault_key")
def test_test_endpoint_reports_pull_plan(real_client: TestClient) -> None:
    conn_id = _create(real_client).json()["id"]
    response = real_client.post(f"{BASE}/{conn_id}/test", headers=real_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["sample_values"]["connection_mode"] == "OFS"
    assert SECRET not in response.text


@pytest.mark.usefixtures("vault_key")
def test_unknown_domain_is_rejected(real_client: TestClient) -> None:
    response = _create(real_client, domains=["NOT_A_DOMAIN"])
    assert response.status_code == 400


def test_list_domains_reports_catalog(real_client: TestClient) -> None:
    response = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/temenos/domains",
        headers=real_headers(),
        params={"mode": "OFS"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    supported = {d["domain"] for d in body["domains"] if d["supported"]}
    assert "POSITIONS_LOANS" in supported
    assert {"domain", "category", "entity_type", "default_cadence"} <= set(body["domains"][0])


@pytest.mark.usefixtures("vault_key")
def test_create_seeds_default_t24_mapping(real_client: TestClient) -> None:
    created = _create(real_client)
    assert created.status_code == 201, created.text
    configs = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/mapping-configs", headers=real_headers()
    ).json()["configs"]
    t24 = [c for c in configs if c["source_system"] == "T24" and c["status"] == "active"]
    # Onboarding seeded a default mapping (connection is pull-ready), and the
    # single-active-per-source guarantee holds however many T24 versions the
    # real bank carries.
    assert len(t24) == 1


@pytest.mark.usefixtures("vault_key")
def test_trigger_pull_enqueues_a_job(real_client: TestClient) -> None:
    conn_id = _create(real_client).json()["id"]
    response = real_client.post(
        f"{BASE}/{conn_id}/pull", headers=real_headers(), json={"as_of_date": "2026-06-30"}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["count"] == 1
    assert len(body["job_ids"]) == 1


@pytest.mark.usefixtures("vault_key")
def test_trigger_backfill_enqueues_one_job_per_date(real_client: TestClient) -> None:
    conn_id = _create(real_client).json()["id"]
    response = real_client.post(
        f"{BASE}/{conn_id}/backfill",
        headers=real_headers(),
        json={"start_date": "2026-06-28", "end_date": "2026-06-30"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["count"] == 3
    assert len(set(body["job_ids"])) == 3


@pytest.mark.usefixtures("vault_key")
def test_tenant_isolation(real_client: TestClient) -> None:
    created = _create(real_client)
    assert created.status_code == 201, created.text
    # a different org cannot see (or drive) this bank's connections
    other = real_client.get(BASE, headers=other_headers())
    assert other.status_code == 404
    assert (
        real_client.post(f"{BASE}/{created.json()['id']}/test", headers=other_headers()).status_code
        == 404
    )
