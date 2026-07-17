"""Temenos core-banking connection management API.

Credential handling is the load-bearing concern: credentials go in through
request bodies, round-trip the encrypted vault, and must NEVER appear in any
response — only status, fingerprint, and expiry do.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.api.helpers import ORG_2, USER_2, headers

MASTER_KEY = "temenos-api-test-master-key"
SECRET = "svc-password-that-must-never-leak"
OFS_CREDENTIALS = {"username": "SVC.AEQUOROS", "password": SECRET}


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


def _seed_bank(client: TestClient) -> str:
    response = client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    return response.json()["bank_id"]


def _base(bank_id: str) -> str:
    return f"/api/v1/banks/{bank_id}/temenos/connections"


def _create(  # noqa: PLR0913 - one helper carries the full request shape
    client: TestClient,
    bank_id: str,
    *,
    mode: str = "OFS",
    display_name: str = "Core OFS",
    credentials: dict[str, Any] | None = None,
    domains: list[str] | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "connection_mode": mode,
        "display_name": display_name,
        "endpoint": "ofs://sample-bank",
        "credentials": credentials if credentials is not None else OFS_CREDENTIALS,
    }
    if domains is not None:
        payload["domains"] = domains
    return client.post(_base(bank_id), headers=headers(), json=payload)


def test_create_activates_on_valid_credentials(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(db_client, bank_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["connection_mode"] == "OFS"
    assert body["credential_fingerprint"]
    # every supported OFS domain enabled by default
    assert "POSITIONS_LOANS" in body["domains"]


def test_credentials_never_appear_in_any_response(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id)
    assert SECRET not in created.text
    assert "password" not in created.json()
    listed = db_client.get(_base(bank_id), headers=headers())
    assert SECRET not in listed.text


def test_create_with_bad_credential_shape_stays_testing(
    db_client: TestClient, vault_key: None
) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(db_client, bank_id, credentials={"username": "SVC"})  # no password
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "TESTING"
    assert body["validation_error"]


def test_duplicate_display_name_conflicts(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    assert _create(db_client, bank_id).status_code == 201
    dup = _create(db_client, bank_id)
    assert dup.status_code == 409


def test_rotate_credentials_validates_first(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    conn_id = _create(db_client, bank_id).json()["id"]
    # bad rotation is rejected, nothing changes
    bad = db_client.patch(
        f"{_base(bank_id)}/{conn_id}",
        headers=headers(),
        json={"credentials": {"username": "SVC"}},
    )
    assert bad.status_code == 422
    # good rotation swaps the fingerprint
    before = db_client.get(_base(bank_id), headers=headers()).json()["connections"][0]
    good = db_client.patch(
        f"{_base(bank_id)}/{conn_id}",
        headers=headers(),
        json={"credentials": {"username": "SVC.NEW", "password": "another-secret"}},
    )
    assert good.status_code == 200, good.text
    assert good.json()["credential_fingerprint"] != before["credential_fingerprint"]


def test_disable_enable_revoke_lifecycle(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    conn_id = _create(db_client, bank_id).json()["id"]
    assert (
        db_client.post(f"{_base(bank_id)}/{conn_id}/disable", headers=headers()).json()["status"]
        == "DISABLED"
    )
    assert (
        db_client.post(f"{_base(bank_id)}/{conn_id}/enable", headers=headers()).json()["status"]
        == "ACTIVE"
    )
    revoked = db_client.delete(f"{_base(bank_id)}/{conn_id}", headers=headers())
    assert revoked.json()["status"] == "REVOKED"
    # revoked row is kept but its credential is wiped
    assert revoked.json()["credential_fingerprint"] is None


def test_test_endpoint_reports_pull_plan(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    conn_id = _create(db_client, bank_id).json()["id"]
    response = db_client.post(f"{_base(bank_id)}/{conn_id}/test", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["sample_values"]["connection_mode"] == "OFS"


def test_unknown_domain_is_rejected(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(db_client, bank_id, domains=["NOT_A_DOMAIN"])
    assert response.status_code == 400


def test_list_domains_reports_catalog(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    response = db_client.get(
        f"/api/v1/banks/{bank_id}/temenos/domains", headers=headers(), params={"mode": "OFS"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    supported = {d["domain"] for d in body["domains"] if d["supported"]}
    assert "POSITIONS_LOANS" in supported
    assert {"domain", "category", "entity_type", "default_cadence"} <= set(body["domains"][0])


def test_create_seeds_default_t24_mapping(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    _create(db_client, bank_id)
    configs = db_client.get(
        f"/api/v1/banks/{bank_id}/mapping-configs", headers=headers()
    ).json()["configs"]
    t24 = [c for c in configs if c["source_system"] == "T24" and c["status"] == "active"]
    assert len(t24) == 1  # onboarding seeded a default mapping, connection is pull-ready


def test_trigger_pull_enqueues_a_job(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    conn_id = _create(db_client, bank_id).json()["id"]
    response = db_client.post(
        f"{_base(bank_id)}/{conn_id}/pull", headers=headers(), json={"as_of_date": "2026-06-30"}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["count"] == 1
    assert len(body["job_ids"]) == 1


def test_trigger_backfill_enqueues_one_job_per_date(
    db_client: TestClient, vault_key: None
) -> None:
    bank_id = _seed_bank(db_client)
    conn_id = _create(db_client, bank_id).json()["id"]
    response = db_client.post(
        f"{_base(bank_id)}/{conn_id}/backfill",
        headers=headers(),
        json={"start_date": "2026-06-28", "end_date": "2026-06-30"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["count"] == 3


def test_tenant_isolation(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    _create(db_client, bank_id)
    # a different org cannot see this bank's connections
    other = db_client.get(_base(bank_id), headers=headers(org_id=ORG_2, user_id=USER_2))
    assert other.status_code == 404
