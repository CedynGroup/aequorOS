"""MARKETS configure cutover: connection lifecycle and source-preference writes
need exact grants.

Vendor connection lifecycle (create, validate, patch, disable, enable, revoke)
and the per-bank source-preference upsert are configuration duties: they
require a complete active ``markets/configure/restricted`` exact-bank binding,
denied BEFORE any vault write, vendor call, status mutation or audit row. The
read boundaries (published/confidential/restricted views) and the operational
``/test`` pull keep their existing gates and gain nothing from Configurator
(no broadening).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.market_data.adapters.credential_manager import EncryptedDbVault
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    MarketDataConnection,
    MarketDataSourcePreference,
    User,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.helpers import ORG_1, USER_1, error_envelope, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
SIBLING = "BK-MARKET3"
CONNECTIONS = f"{BASE}/market-data/connections"
PREFERENCES = f"{BASE}/market-data/source-preferences"

MASTER_KEY = "markets-configure-test-master-key"
SECRET = "terminal-secret-that-must-never-leak"

REFINITIV_CREDENTIALS: dict[str, Any] = {
    "client_id": "aequoros-app-001",
    "client_secret": SECRET,
    "scope": "trapi",
    "subscription_type": "rdp",
    "refresh_token": "",
    "token_endpoint": "",
    "contact_admin": "treasury-ops@bank.test",
}


@pytest.fixture(autouse=True)
def book_without_authority(db_client: TestClient) -> None:
    _ = db_client
    with get_sessionmaker()() as db:
        materialize_canonical_test_book(db)
        db.add(
            Bank(
                id=SIBLING,
                organization_id=ORG_1,
                name="Markets configure sibling fixture",
                short_name="MarketsSibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type="universal_bank",
            )
        )
        db.commit()


@pytest.fixture(autouse=True)
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


def _grant(
    bundle: RoleBundle,
    *,
    module: ModuleScope = ModuleScope.MARKETS,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
    bank_id: str = SAMPLE_BANK_ID,
) -> None:
    with get_sessionmaker()() as db:
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION, bank_id, module, sensitivity
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "markets-configure-test"),
            reason="MARKETS configure-cutover regression authority",
        )
        db.commit()


def _current_version() -> int:
    with get_sessionmaker()() as db:
        user = db.get(User, USER_1)
        assert user is not None
        return user.authorization_version


def _auth() -> dict[str, str]:
    # Scalar roles ("admin", "approver") must grant nothing by themselves:
    # only the explicit scoped binding may authorize these routes.
    return headers(
        roles=("admin", "approver"), authorization_version=_current_version()
    )


def _counts() -> tuple[int, int, int]:
    with get_sessionmaker()() as db:
        return (
            db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
            db.scalar(select(func.count()).select_from(MarketDataConnection)) or 0,
            db.scalar(select(func.count()).select_from(MarketDataSourcePreference)) or 0,
        )


def _refuse_vendor_and_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a denied configure action touches the vault or a vendor."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Denied MARKETS configure action reached the vault or vendor")

    monkeypatch.setattr(EncryptedDbVault, "store", refuse)
    monkeypatch.setattr(EncryptedDbVault, "delete", refuse)
    monkeypatch.setattr(EncryptedDbVault, "retrieve", refuse)
    monkeypatch.setattr(
        "app.market_data.service.market_data_connections.get_market_data_adapter_class", refuse
    )


def _seed_connection(vendor: str = "refinitiv", status: str = "ACTIVE") -> UUID:
    with get_sessionmaker()() as db:
        row = MarketDataConnection(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            vendor=vendor,
            display_name=f"Configure fixture [{uuid4().hex[:8]}]",
            status=status,
            vault_path=f"vault://fixture/{vendor}" if vendor != "manual_upload" else "",
            scopes=["YIELD_CURVE_GHS", "FX_SPOT_USD_GHS"],
        )
        db.add(row)
        db.commit()
        return row.id


def _vendor_payload() -> dict[str, Any]:
    return {
        "vendor": "refinitiv",
        "display_name": f"Configure create [{uuid4().hex[:8]}]",
        "scopes": ["YIELD_CURVE_GHS", "FX_SPOT_USD_GHS"],
        "credentials": REFINITIV_CREDENTIALS,
    }


# --- Connection lifecycle denials --------------------------------------------


def test_zero_grants_denied_on_connection_create(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture viewer binding covers the bank but holds no configure right."""
    _refuse_vendor_and_vault(monkeypatch)
    before = _counts()
    denied = db_client.post(CONNECTIONS, headers=_auth(), json=_vendor_payload())
    assert denied.status_code == 403, denied.text
    assert (
        error_envelope(denied)["message"] == "This action requires an active MARKETS binding."
    )
    assert _counts() == before


def test_connection_lifecycle_denied_without_configure(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = _seed_connection()
    _grant(RoleBundle.ANALYST)
    before = _counts()
    _refuse_vendor_and_vault(monkeypatch)

    denied_create = db_client.post(CONNECTIONS, headers=_auth(), json=_vendor_payload())
    assert denied_create.status_code == 403, denied_create.text
    assert (
        error_envelope(denied_create)["message"]
        == "This action requires an active MARKETS binding."
    )
    object_ops = (
        ("POST", f"{CONNECTIONS}/{connection_id}/validate", None),
        ("PATCH", f"{CONNECTIONS}/{connection_id}", {"display_name": "Renamed"}),
        ("POST", f"{CONNECTIONS}/{connection_id}/disable", None),
        ("POST", f"{CONNECTIONS}/{connection_id}/enable", None),
        ("DELETE", f"{CONNECTIONS}/{connection_id}", None),
    )
    for method, path, payload in object_ops:
        hidden = db_client.request(method, path, headers=_auth(), json=payload)
        assert hidden.status_code == 404, (path, hidden.text)
        assert error_envelope(hidden)["message"] == "Market data object not found."
    assert _counts() == before
    with get_sessionmaker()() as db:
        row = db.get(MarketDataConnection, connection_id)
        assert row is not None
        assert row.status == "ACTIVE"


# --- Source-preference PUT denials -------------------------------------------


def test_source_preference_put_denied_without_configure(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _grant(RoleBundle.ANALYST)
    before = _counts()
    _refuse_vendor_and_vault(monkeypatch)

    denied = db_client.put(
        PREFERENCES,
        headers=_auth(),
        json={"curves": {"source": "vendor"}, "reason": "configure-cutover denial"},
    )
    assert denied.status_code == 403, denied.text
    assert (
        error_envelope(denied)["message"] == "This action requires an active MARKETS binding."
    )
    assert _counts() == before


# --- Configurator binding allows ---------------------------------------------


def test_source_preference_put_succeeds_with_configurator_binding(
    db_client: TestClient,
) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    put = db_client.put(
        PREFERENCES,
        headers=_auth(),
        json={
            "curves": {"source": "vendor", "overlay": False},
            "fx": {"source": "bank"},
            "reason": "licensed feed for curves, own marks for FX",
        },
    )
    assert put.status_code == 200, put.text
    # Write-only acknowledgement (D34): only the submitted fields come back.
    body = put.json()
    assert body["curves"] == {"source": "vendor", "overlay": False}
    assert body["fx"] == {"source": "bank", "overlay": None}
    assert body["rates"] is None
    # The resolved row is read through the view-gated GET (ambient viewer binding).
    again = db_client.get(PREFERENCES, headers=_auth())
    assert again.status_code == 200, again.text
    assert again.json()["curves"] == {"source": "vendor", "overlay": False}
    assert again.json()["fx"]["source"] == "bank"


def test_connection_lifecycle_and_validation_succeed_with_configurator_binding(
    db_client: TestClient,
) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    created = db_client.post(CONNECTIONS, headers=_auth(), json=_vendor_payload())
    assert created.status_code == 201, created.text
    assert SECRET not in created.text
    # Write-only acknowledgement (D34): the stored configuration is never echoed.
    body = created.json()
    assert set(body) == {"id", "bank_id", "status", "validation_error"}
    assert body["status"] == "ACTIVE"
    connection_id = body["id"]

    validated = db_client.post(f"{CONNECTIONS}/{connection_id}/validate", headers=_auth())
    assert validated.status_code == 200, validated.text
    assert set(validated.json()) == {"id", "bank_id", "status", "validation_error"}
    assert validated.json()["status"] == "ACTIVE"

    patched = db_client.request(
        "PATCH", f"{CONNECTIONS}/{connection_id}", headers=_auth(), json={"display_name": "Renamed"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] is None  # a no-rotation patch writes no status
    disabled = db_client.post(f"{CONNECTIONS}/{connection_id}/disable", headers=_auth())
    assert disabled.json()["status"] == "DISABLED"
    enabled = db_client.post(f"{CONNECTIONS}/{connection_id}/enable", headers=_auth())
    assert enabled.json()["status"] == "ACTIVE"
    revoked = db_client.delete(f"{CONNECTIONS}/{connection_id}", headers=_auth())
    assert revoked.json()["status"] == "REVOKED"


def test_manual_upload_connection_onboards_with_configurator_binding(
    db_client: TestClient,
) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    created = db_client.post(
        CONNECTIONS,
        headers=_auth(),
        json={
            "vendor": "manual_upload",
            "display_name": "Treasury uploads",
            "scopes": ["YIELD_CURVE_GHS"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == {"id", "bank_id", "status", "validation_error"}
    assert body["status"] == "ACTIVE"


# --- Mutation responses are write-only acknowledgements (D34) -----------------


def _configure_only_auth() -> dict[str, str]:
    """Strip every ambient binding (including the fixture viewer grant) and keep
    exactly one markets/configure/restricted binding: a configure-only principal."""
    with get_sessionmaker()() as db:
        db.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        db.commit()
    _grant(RoleBundle.CONFIGURATOR)
    return headers(roles=("viewer",), authorization_version=_current_version())


MARKETS_ACK_FIELDS = {"id", "bank_id", "status", "validation_error"}


@pytest.mark.usefixtures("vault_key")
def test_configure_only_connection_responses_are_write_only(
    db_client: TestClient,
) -> None:
    """No configure-gated connection response echoes stored view-protected
    content (D34): no scopes, schedule, credential metadata or pull history —
    including on a no-op disable of an already-disabled connection."""
    auth = _configure_only_auth()
    created = db_client.post(CONNECTIONS, headers=auth, json=_vendor_payload())
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == MARKETS_ACK_FIELDS
    connection_id = body["id"]
    assert body["status"] == "ACTIVE"
    # Not even the credential material this payload carried is echoed.
    assert "aequoros-app-001" not in created.text
    assert SECRET not in created.text

    validated = db_client.post(f"{CONNECTIONS}/{connection_id}/validate", headers=auth)
    assert validated.status_code == 200, validated.text
    assert set(validated.json()) == MARKETS_ACK_FIELDS
    assert validated.json()["status"] == "ACTIVE"

    patched = db_client.request(
        "PATCH", f"{CONNECTIONS}/{connection_id}", headers=auth, json={"display_name": "Renamed"}
    )
    assert patched.status_code == 200, patched.text
    assert set(patched.json()) == MARKETS_ACK_FIELDS
    assert patched.json()["status"] is None  # a no-rotation patch writes no status
    assert "Renamed" not in patched.text

    disabled = db_client.post(f"{CONNECTIONS}/{connection_id}/disable", headers=auth)
    assert disabled.json()["status"] == "DISABLED"
    assert "YIELD_CURVE_GHS" not in disabled.text

    # No-op disable of an already-disabled seeded connection (a different vendor:
    # the credential vault is keyed per vendor, one connection each).
    disabled_id = _seed_connection(vendor="bloomberg", status="DISABLED")
    again = db_client.post(f"{CONNECTIONS}/{disabled_id}/disable", headers=auth)
    assert again.status_code == 200, again.text
    assert set(again.json()) == MARKETS_ACK_FIELDS
    assert again.json()["status"] == "DISABLED"
    assert "YIELD_CURVE_GHS" not in again.text

    enabled = db_client.post(f"{CONNECTIONS}/{connection_id}/enable", headers=auth)
    assert enabled.json()["status"] == "ACTIVE"
    revoked = db_client.delete(f"{CONNECTIONS}/{connection_id}", headers=auth)
    assert revoked.json()["status"] == "REVOKED"

    # The configure-only read boundary is intact: no read-back of the stored row.
    denied_list = db_client.get(CONNECTIONS, headers=auth)
    assert denied_list.status_code in {403, 404}, denied_list.text
    with get_sessionmaker()() as db:
        row = db.get(MarketDataConnection, UUID(connection_id))
        assert row is not None
        assert row.display_name == "Renamed"
        assert row.status == "REVOKED"


def test_source_preference_partial_put_returns_only_submitted_fields(
    db_client: TestClient,
) -> None:
    """A partial PUT must never read stored categories back (D34): a principal
    denied the published-view GET cannot read existing preferences through it."""
    auth = _configure_only_auth()
    with get_sessionmaker()() as db:
        db.add(
            MarketDataSourcePreference(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                curves_source="vendor",
                curves_overlay=False,
                fx_source="bank",
                fx_overlay=True,
                rates_source="aequor",
                rates_overlay=False,
            )
        )
        db.commit()

    put = db_client.put(
        PREFERENCES,
        headers=auth,
        json={"curves": {"source": "aequor"}, "reason": "write-only ack regression"},
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert set(body) == {"bank_id", "curves", "fx", "rates"}
    # Only the submitted field is echoed; the stored fx/rates rows stay hidden.
    assert body["curves"] == {"source": "aequor", "overlay": None}
    assert body["fx"] is None
    assert body["rates"] is None

    # The configure-only read boundary is intact.
    denied = db_client.get(PREFERENCES, headers=auth)
    assert denied.status_code in {403, 404}, denied.text


def test_source_preference_put_read_back_for_configure_plus_view(
    db_client: TestClient,
) -> None:
    """A configure+view caller gets the same partial acknowledgement and then
    reads the full resolved preference through the published-view GET."""
    _grant(RoleBundle.CONFIGURATOR)  # the ambient fixture viewer binding remains
    put = db_client.put(
        PREFERENCES, headers=_auth(), json={"fx": {"overlay": False}, "reason": "overlay off"}
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["fx"] == {"source": None, "overlay": False}
    assert body["curves"] is None and body["rates"] is None
    again = db_client.get(PREFERENCES, headers=_auth())
    assert again.status_code == 200, again.text
    assert again.json()["fx"]["overlay"] is False


# --- Exact binding dimensions ------------------------------------------------


@pytest.mark.parametrize(
    ("module", "sensitivity", "bank_id"),
    [
        (ModuleScope.DATA, SensitivityScope.RESTRICTED, SAMPLE_BANK_ID),
        (ModuleScope.MARKETS, SensitivityScope.CONFIDENTIAL, SAMPLE_BANK_ID),
        (ModuleScope.MARKETS, SensitivityScope.RESTRICTED, SIBLING),
    ],
)
def test_mismatched_configure_binding_dimensions_are_denied(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    bank_id: str,
) -> None:
    _grant(RoleBundle.CONFIGURATOR, module=module, sensitivity=sensitivity, bank_id=bank_id)
    connection_id = _seed_connection()
    before = _counts()
    _refuse_vendor_and_vault(monkeypatch)

    # A binding elsewhere in the organization still covers the request context
    # (403 on missing permission); object-scoped routes stay opaque 404.
    assert db_client.post(CONNECTIONS, headers=_auth(), json=_vendor_payload()).status_code == 403
    hidden = db_client.post(f"{CONNECTIONS}/{connection_id}/disable", headers=_auth())
    assert hidden.status_code == 404, hidden.text
    assert _counts() == before


@pytest.mark.parametrize("lifecycle", ["suspended", "revoked", "expired", "future"])
def test_binding_lifecycle_fail_closed_for_markets_configure(
    db_client: TestClient, lifecycle: str
) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    with get_sessionmaker()() as db:
        binding = db.scalar(
            select(AuthorizationBinding)
            .where(
                AuthorizationBinding.principal_user_id == USER_1,
                AuthorizationBinding.role_bundle == RoleBundle.CONFIGURATOR.value,
            )
            .order_by(AuthorizationBinding.created_at.desc())
            .limit(1)
        )
        assert binding is not None
        if lifecycle == "suspended":
            binding.status = BindingStatus.SUSPENDED.value
        elif lifecycle == "revoked":
            binding.status = BindingStatus.REVOKED.value
            binding.revoked_at = utc_now()
            binding.revoked_by_type = GrantorType.SYSTEM.value
            binding.revoked_by_id = "markets-configure-test"
            binding.revoked_reason = "Configure-cutover revocation test"
        elif lifecycle == "expired":
            binding.valid_from = utc_now() - timedelta(days=2)
            binding.valid_until = utc_now() - timedelta(seconds=1)
        else:
            binding.valid_from = utc_now() + timedelta(days=1)
        db.commit()
    before = _counts()
    denied = db_client.post(CONNECTIONS, headers=_auth(), json=_vendor_payload())
    assert denied.status_code in {403, 404}, denied.text
    assert _counts() == before


# --- Read/run boundaries unchanged -------------------------------------------


def test_configurator_gains_no_read_or_test_pull_authority(db_client: TestClient) -> None:
    """Configure alone neither reads connection metadata nor runs a test pull.

    The connection list keeps its exact MARKETS/restricted VIEW gate, and the
    ``/test`` representative pull keeps its legacy operational gate (D2): a
    configure-only principal (no analyst-or-higher scalar role) is refused
    there. The ambient fixture viewer binding is removed so the principal is
    genuinely configure-only.
    """
    with get_sessionmaker()() as db:
        db.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        db.commit()
    _grant(RoleBundle.CONFIGURATOR)
    connection_id = _seed_connection(vendor="manual_upload")
    auth = headers(roles=("viewer",), authorization_version=_current_version())

    denied_list = db_client.get(CONNECTIONS, headers=auth)
    assert denied_list.status_code == 403, denied_list.text
    assert error_envelope(denied_list)["message"] == (
        "Market data connection access requires an active scoped binding."
    )

    denied_test = db_client.post(f"{CONNECTIONS}/{connection_id}/test", headers=auth)
    assert denied_test.status_code == 403, denied_test.text
    assert error_envelope(denied_test)["message"] == (
        "This action requires the 'analyst' role or higher."
    )


def test_restricted_viewer_keeps_test_pull_through_the_operational_gate(
    db_client: TestClient,
) -> None:
    """The existing operational /test boundary is preserved: a Markets-restricted
    analyst still reaches the service (its 400 is the service answering)."""
    _grant(RoleBundle.VIEWER)
    connection_id = _seed_connection(vendor="manual_upload")

    response = db_client.post(
        f"{CONNECTIONS}/{connection_id}/test",
        headers=headers(roles=("analyst",), authorization_version=_current_version()),
    )
    assert response.status_code == 400, response.text
    assert error_envelope(response)["message"] == (
        "Manual upload connections are tested by uploading a file; "
        "use the upload endpoint."
    )
