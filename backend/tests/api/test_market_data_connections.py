"""Market data connection management API (market_data_adapter.md §9.3/§10) on
the ACTUAL primary.

Credential handling is the load-bearing concern: credentials go in through
request bodies, round-trip the encrypted vault, and must NEVER appear in any
response — only status, fingerprint, and expiry do. Every vendor call stays on
the offline defaults (SimulatedTokenProvider + UnconfiguredTransport) — nothing
reaches a vendor. Invariants: manual_upload activates without credentials;
vendor connections validate → ACTIVE (bad shape → TESTING, bank-facing error
only); one connection per vendor (409); rotation swaps the fingerprint and fails
atomically; disable/enable/revoke state machine; scope catalog + quota shape;
tenant isolation. The vault key is set by the test (never read from .env).
Opt-in via REAL_DATA_DATABASE_URL, rolled back (tests/real_data.py).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.real_data import REAL_BANK_ID, other_headers, real_headers, requires_real_data

pytestmark = requires_real_data

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

BASE = f"/api/v1/banks/{REAL_BANK_ID}/market-data/connections"


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


def _create(  # noqa: PLR0913 - one helper carries the full request shape
    client: TestClient,
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
    return client.post(BASE, headers=real_headers(), json=payload)


def _listed(client: TestClient) -> dict[str, Any]:
    response = client.get(BASE, headers=real_headers())
    assert response.status_code == 200, response.text
    return response.json()


def _by_id(client: TestClient, connection_id: str) -> dict[str, Any]:
    return next(item for item in _listed(client)["connections"] if item["id"] == connection_id)


# -- create ---------------------------------------------------------------------


def test_create_manual_upload_is_active_immediately(real_client: TestClient) -> None:
    response = _create(
        real_client,
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


@pytest.mark.usefixtures("vault_key")
def test_create_vendor_connection_validates_and_activates(real_client: TestClient) -> None:
    response = _create(
        real_client,
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


@pytest.mark.usefixtures("vault_key")
def test_create_with_bad_credentials_stays_testing(real_client: TestClient) -> None:
    response = _create(real_client, credentials={**REFINITIV_CREDENTIALS, "client_secret": ""})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "TESTING"
    assert body["validation_error"]  # bank-facing message
    assert "invalid_client" not in body["validation_error"]  # raw vendor detail stays internal


@pytest.mark.usefixtures("vault_key")
def test_create_vendor_without_credentials_is_400(real_client: TestClient) -> None:
    response = _create(real_client, credentials=None)
    assert response.status_code == 400
    assert "required" in response.json()["error"]["message"]


def test_create_manual_upload_with_credentials_is_400(real_client: TestClient) -> None:
    response = _create(
        real_client,
        vendor="manual_upload",
        credentials={"anything": "x"},
        scopes=["YIELD_CURVE_GHS"],
    )
    assert response.status_code == 400


@pytest.mark.usefixtures("vault_key")
def test_create_duplicate_vendor_is_409(real_client: TestClient) -> None:
    assert _create(real_client, credentials=REFINITIV_CREDENTIALS).status_code == 201
    duplicate = _create(
        real_client, display_name="Second terminal", credentials=REFINITIV_CREDENTIALS
    )
    assert duplicate.status_code == 409


@pytest.mark.usefixtures("vault_key")
def test_create_unknown_scope_is_400(real_client: TestClient) -> None:
    response = _create(real_client, credentials=REFINITIV_CREDENTIALS, scopes=["BOND_LADDER"])
    assert response.status_code == 400


@pytest.mark.usefixtures("vault_key")
def test_create_unsupported_scope_for_vendor_is_400(real_client: TestClient) -> None:
    # The Refinitiv RIC catalog documents KES curves as unsupported (§16.9).
    response = _create(real_client, credentials=REFINITIV_CREDENTIALS, scopes=["YIELD_CURVE_KES"])
    assert response.status_code == 400
    assert "not supported" in response.json()["error"]["message"]


# -- list -----------------------------------------------------------------------


@pytest.mark.usefixtures("vault_key")
def test_list_never_contains_credential_material(real_client: TestClient) -> None:
    before = _listed(real_client)["total"]
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS)
    assert created.status_code == 201
    response = real_client.get(BASE, headers=real_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == before + 1
    connection = next(c for c in body["connections"] if c["id"] == created.json()["id"])
    assert connection["credential_fingerprint"]
    assert "credential_ciphertext" not in response.text
    assert "client_secret" not in response.text
    assert SECRET not in response.text


# -- validate / test --------------------------------------------------------------


@pytest.mark.usefixtures("vault_key")
def test_validate_refreshes_status_and_timestamp(real_client: TestClient) -> None:
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS).json()
    response = real_client.post(f"{BASE}/{created['id']}/validate", headers=real_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["last_validated_at"] is not None
    assert body["last_validated_at"] >= created["last_validated_at"]
    assert body["validation_error"] is None
    assert SECRET not in response.text


@pytest.mark.usefixtures("vault_key")
def test_test_pull_returns_bank_facing_result(real_client: TestClient) -> None:
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS).json()
    response = real_client.post(f"{BASE}/{created['id']}/test", headers=real_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    # Auth succeeds against the simulated token provider; the default
    # transport is unconfigured, so the pull half reports VENDOR_UNAVAILABLE
    # as a bank-facing message — never a stack trace or raw vendor error.
    assert body["success"] is False
    assert body["error"]
    assert "Traceback" not in body["error"]
    assert SECRET not in response.text


def test_test_pull_on_manual_upload_is_400(real_client: TestClient) -> None:
    created = _create(real_client, vendor="manual_upload", scopes=["YIELD_CURVE_GHS"]).json()
    response = real_client.post(f"{BASE}/{created['id']}/test", headers=real_headers())
    assert response.status_code == 400
    assert "upload endpoint" in response.json()["error"]["message"]


# -- update / rotate --------------------------------------------------------------


@pytest.mark.usefixtures("vault_key")
def test_update_scopes_schedule_and_name(real_client: TestClient) -> None:
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS).json()
    response = real_client.patch(
        f"{BASE}/{created['id']}",
        headers=real_headers(),
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


@pytest.mark.usefixtures("vault_key")
def test_rotate_credentials_swaps_fingerprint(real_client: TestClient) -> None:
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS).json()
    rotated = real_client.patch(
        f"{BASE}/{created['id']}",
        headers=real_headers(),
        json={"credentials": {**REFINITIV_CREDENTIALS, "client_secret": "rotated-secret"}},
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert body["status"] == "ACTIVE"
    assert body["credential_fingerprint"] != created["credential_fingerprint"]
    assert "rotated-secret" not in rotated.text


@pytest.mark.usefixtures("vault_key")
def test_rotate_with_invalid_credentials_is_422_and_unchanged(real_client: TestClient) -> None:
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS).json()
    rotated = real_client.patch(
        f"{BASE}/{created['id']}",
        headers=real_headers(),
        json={"credentials": {**REFINITIV_CREDENTIALS, "client_secret": ""}},
    )
    assert rotated.status_code == 422, rotated.text
    # §10.4: on failure nothing changes — the old credentials stay in place.
    listed = _by_id(real_client, created["id"])
    assert listed["credential_fingerprint"] == created["credential_fingerprint"]
    assert listed["status"] == "ACTIVE"


# -- disable / enable / revoke -----------------------------------------------------


@pytest.mark.usefixtures("vault_key")
def test_disable_and_enable_roundtrip(real_client: TestClient) -> None:
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS).json()
    disabled = real_client.post(f"{BASE}/{created['id']}/disable", headers=real_headers())
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "DISABLED"

    # Disabled connections refuse validation and test pulls until re-enabled.
    assert (
        real_client.post(f"{BASE}/{created['id']}/test", headers=real_headers()).status_code == 409
    )

    enabled = real_client.post(f"{BASE}/{created['id']}/enable", headers=real_headers())
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "ACTIVE"  # re-validated on enable


@pytest.mark.usefixtures("vault_key")
def test_enable_requires_disabled_state(real_client: TestClient) -> None:
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS).json()
    response = real_client.post(f"{BASE}/{created['id']}/enable", headers=real_headers())
    assert response.status_code == 409


@pytest.mark.usefixtures("vault_key")
def test_revoke_wipes_credentials_and_keeps_row(real_client: TestClient) -> None:
    before = _listed(real_client)["total"]
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS).json()
    revoked = real_client.delete(f"{BASE}/{created['id']}", headers=real_headers())
    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["status"] == "REVOKED"
    assert body["credential_fingerprint"] is None  # ciphertext cryptographically wiped

    # §10.5: the row is retained for audit and still listed.
    listed = _listed(real_client)
    assert listed["total"] == before + 1
    assert _by_id(real_client, created["id"])["status"] == "REVOKED"

    # A revoked connection cannot be validated, tested, or updated.
    assert (
        real_client.post(f"{BASE}/{created['id']}/validate", headers=real_headers()).status_code
        == 409
    )

    # Re-adding the vendor reuses the retained row with fresh credentials.
    recreated = _create(real_client, display_name="Replacement", credentials=REFINITIV_CREDENTIALS)
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["id"] == created["id"]
    assert recreated.json()["status"] == "ACTIVE"
    assert recreated.json()["display_name"] == "Replacement"
    assert _listed(real_client)["total"] == before + 1


# -- scopes / quota ----------------------------------------------------------------


def test_scope_catalog_reports_support_and_quota_units(real_client: TestClient) -> None:
    response = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/market-data/scopes", headers=real_headers()
    )
    assert response.status_code == 200, response.text
    scopes = {entry["scope"]: entry for entry in response.json()["scopes"]}
    ghs_curve = scopes["YIELD_CURVE_GHS"]
    assert ghs_curve["category"] == "YIELD_CURVE"
    assert ghs_curve["default_frequency"] == "END_OF_DAY"
    assert ghs_curve["quota_units"] > 0
    assert {"bloomberg", "manual_upload", "refinitiv"} <= set(ghs_curve["supported_by"])
    # Every taxonomy scope is present, even vendor-unsupported ones.
    assert "YIELD_CURVE_KES" in scopes


def test_quota_summary_lists_every_vendor(real_client: TestClient) -> None:
    """Shape and consistency, not magnitudes — the real bank's month may hold
    pulls (the desk publishes into every tenant)."""
    response = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/market-data/quota", headers=real_headers()
    )
    assert response.status_code == 200, response.text
    vendors = {entry["vendor"]: entry for entry in response.json()["vendors"]}
    assert set(vendors) == {"bloomberg", "refinitiv", "manual_upload", "aequor_desk"}
    for entry in vendors.values():
        assert entry["units_consumed"] >= 0
        assert entry["pull_count"] >= 0
        assert entry["monthly_cap"] is None or entry["monthly_cap"] > 0
        assert len(entry["month"]) == 7  # YYYY-MM
        if entry["pull_count"] == 0:
            assert entry["units_consumed"] == 0
    # The desk vendor and manual uploads never consume quota units.
    assert vendors["aequor_desk"]["units_consumed"] == 0
    assert vendors["manual_upload"]["units_consumed"] == 0
    assert len({entry["month"] for entry in vendors.values()}) == 1


# -- tenant isolation --------------------------------------------------------------


@pytest.mark.usefixtures("vault_key")
def test_connections_are_tenant_scoped(real_client: TestClient) -> None:
    created = _create(real_client, credentials=REFINITIV_CREDENTIALS).json()
    other = other_headers()

    assert real_client.get(BASE, headers=other).status_code == 404
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/market-data/scopes", headers=other
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/market-data/quota", headers=other
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            BASE,
            headers=other,
            json={"vendor": "manual_upload", "display_name": "Intruder", "scopes": []},
        ).status_code
        == 404
    )
    for action in ("validate", "test", "disable", "enable"):
        assert (
            real_client.post(f"{BASE}/{created['id']}/{action}", headers=other).status_code == 404
        )
    assert (
        real_client.patch(
            f"{BASE}/{created['id']}",
            headers=other,
            json={"display_name": "Hijacked"},
        ).status_code
        == 404
    )
    assert real_client.delete(f"{BASE}/{created['id']}", headers=other).status_code == 404


@pytest.mark.usefixtures("vault_key")
def test_bloomberg_credential_shape_validates(real_client: TestClient) -> None:
    response = _create(
        real_client,
        vendor="bloomberg",
        display_name="B-PIPE",
        credentials=BLOOMBERG_CREDENTIALS,
        scopes=["YIELD_CURVE_GHS"],
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "ACTIVE"
    assert "certificate" not in response.text
    assert "BEGIN CERTIFICATE" not in response.text
