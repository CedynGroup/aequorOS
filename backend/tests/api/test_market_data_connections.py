"""Market data connection management API (market_data_adapter.md §9.3/§10).

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

MASTER_KEY = "api-test-master-key"

# Well-formed §7.1 Refinitiv credential shape: the SimulatedTokenProvider
# requires non-empty client_id + client_secret and succeeds otherwise.
SECRET = "s3cret-value-that-must-never-leak"
REFINITIV_CREDENTIALS = {
    "client_id": "aequoros-app-001",
    "client_secret": SECRET,
    "scope": "trapi",
    "subscription_type": "rdp",
    "refresh_token": "",
    "token_endpoint": "",
    "contact_admin": "treasury-ops@bank.test",
}

BLOOMBERG_CREDENTIALS = {
    "application_identifier": "aequoros-blp-001",
    "serial_number": "123456",
    "authentication_endpoint": "https://blp.example.test/auth",
    "certificate": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
    "subscription_tier": "data-license",
    "contact_admin": "blp-admin@bank.test",
}


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


def _seed_bank(client: TestClient) -> str:
    response = client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    return response.json()["bank_id"]


def _base(bank_id: str) -> str:
    return f"/api/v1/banks/{bank_id}/market-data/connections"


def _create(  # noqa: PLR0913 - one helper carries the full request shape
    client: TestClient,
    bank_id: str,
    *,
    vendor: str = "refinitiv",
    display_name: str = "Primary terminal",
    credentials: dict[str, Any] | None = None,
    scopes: list[str] | None = None,
    schedule: dict[str, str] | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "vendor": vendor,
        "display_name": display_name,
        "scopes": scopes if scopes is not None else ["YIELD_CURVE_GHS", "FX_SPOT_USD_GHS"],
    }
    if credentials is not None:
        payload["credentials"] = credentials
    if schedule is not None:
        payload["schedule"] = schedule
    return client.post(_base(bank_id), headers=headers(), json=payload)


# -- create ---------------------------------------------------------------------


def test_create_manual_upload_is_active_immediately(db_client: TestClient) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(
        db_client,
        bank_id,
        vendor="manual_upload",
        display_name="Treasury uploads",
        scopes=["YIELD_CURVE_GHS"],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["vendor"] == "manual_upload"
    assert body["status"] == "ACTIVE"
    assert body["credential_fingerprint"] is None
    assert body["validation_error"] is None


def test_create_vendor_connection_validates_and_activates(
    db_client: TestClient, vault_key: None
) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(
        db_client,
        bank_id,
        credentials=REFINITIV_CREDENTIALS,
        schedule={"YIELD_CURVE": "END_OF_DAY"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["last_validated_at"] is not None
    assert body["validation_error"] is None
    assert body["scopes"] == ["FX_SPOT_USD_GHS", "YIELD_CURVE_GHS"]
    assert body["schedule"] == {"YIELD_CURVE": "END_OF_DAY"}
    # Fingerprint is the only credential representation that may surface.
    assert isinstance(body["credential_fingerprint"], str)
    assert len(body["credential_fingerprint"]) == 64
    # WRITE-ONLY: no credential material anywhere in the response.
    assert SECRET not in response.text
    assert "client_secret" not in response.text
    assert "client_id" not in response.text


def test_create_with_bad_credentials_stays_testing(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(
        db_client,
        bank_id,
        credentials={**REFINITIV_CREDENTIALS, "client_secret": ""},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "TESTING"
    assert body["validation_error"]  # bank-facing message
    assert "invalid_client" not in body["validation_error"]  # raw vendor detail stays internal


def test_create_vendor_without_credentials_is_400(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(db_client, bank_id, credentials=None)
    assert response.status_code == 400
    assert "required" in response.json()["error"]["message"]


def test_create_manual_upload_with_credentials_is_400(db_client: TestClient) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(
        db_client,
        bank_id,
        vendor="manual_upload",
        credentials={"anything": "x"},
        scopes=["YIELD_CURVE_GHS"],
    )
    assert response.status_code == 400


def test_create_duplicate_vendor_is_409(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    assert _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).status_code == 201
    duplicate = _create(
        db_client, bank_id, display_name="Second terminal", credentials=REFINITIV_CREDENTIALS
    )
    assert duplicate.status_code == 409


def test_create_unknown_scope_is_400(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(
        db_client, bank_id, credentials=REFINITIV_CREDENTIALS, scopes=["BOND_LADDER"]
    )
    assert response.status_code == 400


def test_create_unsupported_scope_for_vendor_is_400(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    # The Refinitiv RIC catalog documents KES curves as unsupported (§16.9).
    response = _create(
        db_client, bank_id, credentials=REFINITIV_CREDENTIALS, scopes=["YIELD_CURVE_KES"]
    )
    assert response.status_code == 400
    assert "not supported" in response.json()["error"]["message"]


# -- list -----------------------------------------------------------------------


def test_list_never_contains_credential_material(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    assert _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).status_code == 201
    response = db_client.get(_base(bank_id), headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    connection = body["connections"][0]
    assert connection["credential_fingerprint"]
    assert "credential_ciphertext" not in response.text
    assert "client_secret" not in response.text
    assert SECRET not in response.text


# -- validate / test --------------------------------------------------------------


def test_validate_refreshes_status_and_timestamp(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).json()
    response = db_client.post(f"{_base(bank_id)}/{created['id']}/validate", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["last_validated_at"] is not None
    assert body["validation_error"] is None
    assert SECRET not in response.text


def test_test_pull_returns_bank_facing_result(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).json()
    response = db_client.post(f"{_base(bank_id)}/{created['id']}/test", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    # Auth succeeds against the simulated token provider; the default
    # transport is unconfigured, so the pull half reports VENDOR_UNAVAILABLE
    # as a bank-facing message — never a stack trace or raw vendor error.
    assert body["success"] is False
    assert body["error"]
    assert "Traceback" not in body["error"]
    assert SECRET not in response.text


def test_test_pull_on_manual_upload_is_400(db_client: TestClient) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, vendor="manual_upload", scopes=["YIELD_CURVE_GHS"]).json()
    response = db_client.post(f"{_base(bank_id)}/{created['id']}/test", headers=headers())
    assert response.status_code == 400
    assert "upload endpoint" in response.json()["error"]["message"]


# -- update / rotate --------------------------------------------------------------


def test_update_scopes_schedule_and_name(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).json()
    response = db_client.patch(
        f"{_base(bank_id)}/{created['id']}",
        headers=headers(),
        json={
            "display_name": "Renamed terminal",
            "scopes": ["YIELD_CURVE_GHS"],
            "schedule": {"YIELD_CURVE": "WEEKLY"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Renamed terminal"
    assert body["scopes"] == ["YIELD_CURVE_GHS"]
    assert body["schedule"] == {"YIELD_CURVE": "WEEKLY"}
    # Credentials untouched by a non-rotation update.
    assert body["credential_fingerprint"] == created["credential_fingerprint"]


def test_rotate_credentials_swaps_fingerprint(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).json()
    rotated = db_client.patch(
        f"{_base(bank_id)}/{created['id']}",
        headers=headers(),
        json={"credentials": {**REFINITIV_CREDENTIALS, "client_secret": "rotated-secret"}},
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert body["status"] == "ACTIVE"
    assert body["credential_fingerprint"] != created["credential_fingerprint"]
    assert "rotated-secret" not in rotated.text


def test_rotate_with_invalid_credentials_is_422_and_unchanged(
    db_client: TestClient, vault_key: None
) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).json()
    rotated = db_client.patch(
        f"{_base(bank_id)}/{created['id']}",
        headers=headers(),
        json={"credentials": {**REFINITIV_CREDENTIALS, "client_secret": ""}},
    )
    assert rotated.status_code == 422, rotated.text
    # §10.4: on failure nothing changes — the old credentials stay in place.
    listed = db_client.get(_base(bank_id), headers=headers()).json()
    assert listed["connections"][0]["credential_fingerprint"] == created["credential_fingerprint"]
    assert listed["connections"][0]["status"] == "ACTIVE"


# -- disable / enable / revoke -----------------------------------------------------


def test_disable_and_enable_roundtrip(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).json()
    disabled = db_client.post(f"{_base(bank_id)}/{created['id']}/disable", headers=headers())
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "DISABLED"

    # Disabled connections refuse validation and test pulls until re-enabled.
    assert (
        db_client.post(f"{_base(bank_id)}/{created['id']}/test", headers=headers()).status_code
        == 409
    )

    enabled = db_client.post(f"{_base(bank_id)}/{created['id']}/enable", headers=headers())
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "ACTIVE"  # re-validated on enable


def test_enable_requires_disabled_state(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).json()
    response = db_client.post(f"{_base(bank_id)}/{created['id']}/enable", headers=headers())
    assert response.status_code == 409


def test_revoke_wipes_credentials_and_keeps_row(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).json()
    revoked = db_client.delete(f"{_base(bank_id)}/{created['id']}", headers=headers())
    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["status"] == "REVOKED"
    assert body["credential_fingerprint"] is None  # ciphertext cryptographically wiped

    # §10.5: the row is retained for audit and still listed.
    listed = db_client.get(_base(bank_id), headers=headers()).json()
    assert listed["total"] == 1
    assert listed["connections"][0]["status"] == "REVOKED"

    # A revoked connection cannot be validated, tested, or updated.
    assert (
        db_client.post(f"{_base(bank_id)}/{created['id']}/validate", headers=headers()).status_code
        == 409
    )

    # Re-adding the vendor reuses the retained row with fresh credentials.
    recreated = _create(
        db_client, bank_id, display_name="Replacement", credentials=REFINITIV_CREDENTIALS
    )
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["status"] == "ACTIVE"
    assert recreated.json()["display_name"] == "Replacement"


# -- scopes / quota ----------------------------------------------------------------


def test_scope_catalog_reports_support_and_quota_units(db_client: TestClient) -> None:
    bank_id = _seed_bank(db_client)
    response = db_client.get(f"/api/v1/banks/{bank_id}/market-data/scopes", headers=headers())
    assert response.status_code == 200, response.text
    scopes = {entry["scope"]: entry for entry in response.json()["scopes"]}
    ghs_curve = scopes["YIELD_CURVE_GHS"]
    assert ghs_curve["category"] == "YIELD_CURVE"
    assert ghs_curve["default_frequency"] == "END_OF_DAY"
    assert ghs_curve["quota_units"] > 0
    assert {"bloomberg", "manual_upload", "refinitiv"} <= set(ghs_curve["supported_by"])
    # Every taxonomy scope is present, even vendor-unsupported ones.
    assert "YIELD_CURVE_KES" in scopes


def test_quota_summary_lists_every_vendor(db_client: TestClient) -> None:
    bank_id = _seed_bank(db_client)
    response = db_client.get(f"/api/v1/banks/{bank_id}/market-data/quota", headers=headers())
    assert response.status_code == 200, response.text
    vendors = {entry["vendor"]: entry for entry in response.json()["vendors"]}
    assert set(vendors) == {"bloomberg", "refinitiv", "manual_upload"}
    for entry in vendors.values():
        assert entry["units_consumed"] == 0
        assert entry["pull_count"] == 0
        assert entry["monthly_cap"] is None
        assert len(entry["month"]) == 7  # YYYY-MM


# -- tenant isolation --------------------------------------------------------------


def test_connections_are_tenant_scoped(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    created = _create(db_client, bank_id, credentials=REFINITIV_CREDENTIALS).json()
    org2 = headers(org_id=ORG_2, user_id=USER_2)

    assert db_client.get(_base(bank_id), headers=org2).status_code == 404
    assert (
        db_client.get(f"/api/v1/banks/{bank_id}/market-data/scopes", headers=org2).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{bank_id}/market-data/quota", headers=org2).status_code == 404
    )
    assert (
        db_client.post(
            _base(bank_id),
            headers=org2,
            json={"vendor": "manual_upload", "display_name": "Intruder", "scopes": []},
        ).status_code
        == 404
    )
    for action in ("validate", "test", "disable", "enable"):
        assert (
            db_client.post(f"{_base(bank_id)}/{created['id']}/{action}", headers=org2).status_code
            == 404
        )
    assert (
        db_client.patch(
            f"{_base(bank_id)}/{created['id']}",
            headers=org2,
            json={"display_name": "Hijacked"},
        ).status_code
        == 404
    )
    assert db_client.delete(f"{_base(bank_id)}/{created['id']}", headers=org2).status_code == 404


def test_bloomberg_credential_shape_validates(db_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(db_client)
    response = _create(
        db_client,
        bank_id,
        vendor="bloomberg",
        display_name="B-PIPE",
        credentials=BLOOMBERG_CREDENTIALS,
        scopes=["YIELD_CURVE_GHS"],
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "ACTIVE"
    assert "certificate" not in response.text
    assert "BEGIN CERTIFICATE" not in response.text
