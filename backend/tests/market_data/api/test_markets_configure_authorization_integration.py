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

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from hypothesis import settings, stateful
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
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
from app.market_data.schemas.market_data_connections import (
    MarketDataConnectionWriteAcknowledgement,
)
from app.market_data.schemas.market_data_sources import (
    SourcePreferencesRead,
    SourcePreferenceWriteAcknowledgement,
)
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
from tests.support.stateful import workflow_settings

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
SIBLING = "BK-MARKET3"
CONNECTIONS = f"{BASE}/market-data/connections"
PREFERENCES = f"{BASE}/market-data/source-preferences"

MASTER_KEY = "markets-configure-test-master-key"
SECRET = "terminal-secret-that-must-never-leak"

MARKETS_ACK_FIELDS = {"id", "bank_id", "status", "validation_error"}

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
    return headers(roles=("admin", "approver"), authorization_version=_current_version())


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
    assert error_envelope(denied)["message"] == "This action requires an active MARKETS binding."
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
    assert error_envelope(denied)["message"] == "This action requires an active MARKETS binding."
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
    body = SourcePreferenceWriteAcknowledgement.model_validate_json(put.text)
    assert body.curves is not None
    assert (body.curves.source, body.curves.overlay) == ("vendor", False)
    assert body.fx is not None
    assert (body.fx.source, body.fx.overlay) == ("bank", None)
    assert body.rates is None
    # The resolved row is read through the view-gated GET (ambient viewer binding).
    again = db_client.get(PREFERENCES, headers=_auth())
    assert again.status_code == 200, again.text
    resolved = SourcePreferencesRead.model_validate_json(again.text)
    assert (resolved.curves.source, resolved.curves.overlay) == ("vendor", False)
    assert resolved.fx.source == "bank"


def test_connection_lifecycle_and_validation_succeed_with_configurator_binding(
    db_client: TestClient,
) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    created = db_client.post(CONNECTIONS, headers=_auth(), json=_vendor_payload())
    assert created.status_code == 201, created.text
    assert SECRET not in created.text
    # Write-only acknowledgement (D34): the stored configuration is never echoed.
    created_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(created.text)
    assert set(created_ack.model_dump()) == MARKETS_ACK_FIELDS
    assert created_ack.status == "ACTIVE"
    connection_id = created_ack.id

    validated = db_client.post(f"{CONNECTIONS}/{connection_id}/validate", headers=_auth())
    assert validated.status_code == 200, validated.text
    validated_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(validated.text)
    assert set(validated_ack.model_dump()) == MARKETS_ACK_FIELDS
    assert validated_ack.status == "ACTIVE"

    patched = db_client.request(
        "PATCH", f"{CONNECTIONS}/{connection_id}", headers=_auth(), json={"display_name": "Renamed"}
    )
    assert patched.status_code == 200, patched.text
    patched_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(patched.text)
    assert patched_ack.status is None  # a no-rotation patch writes no status
    disabled = db_client.post(f"{CONNECTIONS}/{connection_id}/disable", headers=_auth())
    disabled_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(disabled.text)
    assert disabled_ack.status == "DISABLED"
    enabled = db_client.post(f"{CONNECTIONS}/{connection_id}/enable", headers=_auth())
    enabled_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(enabled.text)
    assert enabled_ack.status == "ACTIVE"
    revoked = db_client.delete(f"{CONNECTIONS}/{connection_id}", headers=_auth())
    revoked_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(revoked.text)
    assert revoked_ack.status == "REVOKED"


# --- Lifecycle transition model (state machine) -------------------------------

REVOKED_EDIT_MESSAGE = "This connection is revoked; add a new connection instead."
DISABLED_VALIDATE_MESSAGE = "This connection is disabled; enable it first."
ENABLE_CONFLICT_MESSAGE = "Only a disabled connection can be enabled."


@dataclass(frozen=True)
class _Written:
    """A successful command: the acknowledgement's written status (None for a
    no-rotation patch) and the audit event it appends (None for an idempotent
    replay such as disabling an already-disabled connection)."""

    status: str | None
    event: str | None


@dataclass(frozen=True)
class _Conflict:
    """A rejected command: a 409 whose message never gains stored content.
    The stored status and the success-event trail stay untouched."""

    message: str


type _Outcome = _Written | _Conflict


def _connection_transitions() -> dict[tuple[str, str], _Outcome]:
    """The reviewed MARKETS connection lifecycle contract."""
    enable_conflict = _Conflict(ENABLE_CONFLICT_MESSAGE)
    return {
        ("ACTIVE", "patch"): _Written(None, "updated"),
        ("DISABLED", "patch"): _Written(None, "updated"),
        ("REVOKED", "patch"): _Conflict(REVOKED_EDIT_MESSAGE),
        ("ACTIVE", "disable"): _Written("DISABLED", "disabled"),
        ("DISABLED", "disable"): _Written("DISABLED", None),
        ("REVOKED", "disable"): _Conflict(REVOKED_EDIT_MESSAGE),
        ("ACTIVE", "enable"): enable_conflict,
        ("DISABLED", "enable"): _Written("ACTIVE", "enabled"),
        ("REVOKED", "enable"): enable_conflict,
        ("ACTIVE", "revoke"): _Written("REVOKED", "revoked"),
        ("DISABLED", "revoke"): _Written("REVOKED", "revoked"),
        ("REVOKED", "revoke"): _Written("REVOKED", None),
        ("ACTIVE", "validate"): _Written("ACTIVE", "validated"),
        ("DISABLED", "validate"): _Conflict(DISABLED_VALIDATE_MESSAGE),
        ("REVOKED", "validate"): _Conflict(REVOKED_EDIT_MESSAGE),
    }


class _StateMachineRunner(Protocol):
    """The hypothesis runner's own signature, which ships unannotated."""

    def __call__(
        self, factory: Callable[[], ConnectionLifecycleMachine], *, settings: settings
    ) -> None: ...


def _run_lifecycle_machine(factory: Callable[[], ConnectionLifecycleMachine]) -> None:
    # Boundary: hypothesis's runner arguments are unannotated upstream.
    runner = cast(
        _StateMachineRunner,
        stateful.run_state_machine_as_test,  # pyright: ignore[reportUnknownMemberType]
    )
    runner(factory, settings=workflow_settings())


class ConnectionLifecycleMachine(RuleBasedStateMachine):
    """Arbitrary command sequences against one real connection row: every
    acknowledgement is checked against the transition table, and the stored
    status plus success-event trail are re-read after every step."""

    def __init__(self, client: TestClient) -> None:
        super().__init__()
        self.client = client
        self.transitions = _connection_transitions()
        self.display_name = f"State machine connection [{uuid4().hex[:8]}]"
        self.create_body = _vendor_payload()
        self.create_body["display_name"] = self.display_name
        self.connection_id: UUID | None = None
        self.status = "ACTIVE"
        self.events = ["market_data_connection.created"]
        # Hypothesis examples share the database; the connection row IS the
        # credential vault (vendor-keyed), so clear prior rows for a fresh
        # row and audit trail in every example.
        with get_sessionmaker()() as db:
            db.execute(
                delete(MarketDataConnection).where(
                    MarketDataConnection.organization_id == ORG_1,
                    MarketDataConnection.bank_id == SAMPLE_BANK_ID,
                )
            )
            db.commit()
        created = client.post(CONNECTIONS, headers=_auth(), json=self.create_body)
        assert created.status_code == 201, created.text
        assert SECRET not in created.text
        ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(created.text)
        assert set(ack.model_dump()) == MARKETS_ACK_FIELDS
        assert ack.status == "ACTIVE"
        self.connection_id = ack.id

    @invariant()
    def persisted_state_matches_the_model(self) -> None:
        connection_id = self.connection_id
        assert connection_id is not None
        with get_sessionmaker()() as db:
            row = db.get(MarketDataConnection, connection_id)
            assert row is not None
            assert row.status == self.status
            events = db.scalars(
                select(AuditEvent.event_type).where(AuditEvent.entity_id == str(connection_id))
            ).all()
        assert sorted(events) == sorted(self.events)

    def _apply(
        self, command: str, response: Response
    ) -> MarketDataConnectionWriteAcknowledgement | None:
        """Check one command's response against the transition table and update
        the model. Returns the parsed acknowledgement, or None on a conflict."""
        outcome = self.transitions[(self.status, command)]
        if isinstance(outcome, _Conflict):
            assert response.status_code == 409, response.text
            assert error_envelope(response)["message"] == outcome.message
            return None
        assert response.status_code in {200, 201}, response.text
        assert SECRET not in response.text
        ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(response.text)
        assert set(ack.model_dump()) == MARKETS_ACK_FIELDS
        assert ack.bank_id == SAMPLE_BANK_ID
        assert ack.status == outcome.status
        assert ack.validation_error is None
        if outcome.status is not None:
            self.status = outcome.status
        if outcome.event is not None:
            self.events.append("market_data_connection." + outcome.event)
        return ack

    @rule()
    def validate(self) -> None:
        response = self.client.post(f"{CONNECTIONS}/{self.connection_id}/validate", headers=_auth())
        self._apply("validate", response)

    @rule()
    def patch_display_name(self) -> None:
        # Replaces rather than appends: repeated appends would exceed the
        # 120-character display-name limit and turn the patch into a 422.
        renamed = f"State machine connection [{uuid4().hex[:8]}]"
        response = self.client.request(
            "PATCH",
            f"{CONNECTIONS}/{self.connection_id}",
            headers=_auth(),
            json={"display_name": renamed},
        )
        ack = self._apply("patch", response)
        if ack is not None:
            self.display_name = renamed

    @rule()
    def disable(self) -> None:
        response = self.client.post(f"{CONNECTIONS}/{self.connection_id}/disable", headers=_auth())
        self._apply("disable", response)

    @rule()
    def enable(self) -> None:
        response = self.client.post(f"{CONNECTIONS}/{self.connection_id}/enable", headers=_auth())
        self._apply("enable", response)

    @rule()
    def revoke(self) -> None:
        response = self.client.delete(f"{CONNECTIONS}/{self.connection_id}", headers=_auth())
        self._apply("revoke", response)

    @rule()
    def recreate_with_same_identity(self) -> None:
        """A second create for the same vendor: 409 while a live row holds it;
        the retained revoked row is reused with fresh credentials after
        revocation (same row id, never a second row)."""
        body = dict(self.create_body)
        body["display_name"] = self.display_name
        response = self.client.post(CONNECTIONS, headers=_auth(), json=body)
        if self.status != "REVOKED":
            assert response.status_code == 409, response.text
            assert (
                error_envelope(response)["message"]
                == "A refinitiv connection already exists for this bank."
            )
            return
        assert response.status_code == 201, response.text
        assert SECRET not in response.text
        ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(response.text)
        assert set(ack.model_dump()) == MARKETS_ACK_FIELDS
        assert ack.status == "ACTIVE"
        assert ack.id == self.connection_id
        self.status = "ACTIVE"
        self.events.append("market_data_connection.created")


def test_connection_lifecycle_state_machine(db_client: TestClient) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    _run_lifecycle_machine(lambda: ConnectionLifecycleMachine(db_client))


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
    created_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(created.text)
    assert set(created_ack.model_dump()) == MARKETS_ACK_FIELDS
    assert created_ack.status == "ACTIVE"


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
    created_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(created.text)
    assert set(created_ack.model_dump()) == MARKETS_ACK_FIELDS
    connection_id = created_ack.id
    assert created_ack.status == "ACTIVE"
    # Not even the credential material this payload carried is echoed.
    assert "aequoros-app-001" not in created.text
    assert SECRET not in created.text

    validated = db_client.post(f"{CONNECTIONS}/{connection_id}/validate", headers=auth)
    assert validated.status_code == 200, validated.text
    validated_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(validated.text)
    assert set(validated_ack.model_dump()) == MARKETS_ACK_FIELDS
    assert validated_ack.status == "ACTIVE"

    patched = db_client.request(
        "PATCH", f"{CONNECTIONS}/{connection_id}", headers=auth, json={"display_name": "Renamed"}
    )
    assert patched.status_code == 200, patched.text
    patched_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(patched.text)
    assert set(patched_ack.model_dump()) == MARKETS_ACK_FIELDS
    assert patched_ack.status is None  # a no-rotation patch writes no status
    assert "Renamed" not in patched.text

    disabled = db_client.post(f"{CONNECTIONS}/{connection_id}/disable", headers=auth)
    disabled_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(disabled.text)
    assert disabled_ack.status == "DISABLED"
    assert "YIELD_CURVE_GHS" not in disabled.text

    # No-op disable of an already-disabled seeded connection (a different vendor:
    # the credential vault is keyed per vendor, one connection each).
    disabled_id = _seed_connection(vendor="bloomberg", status="DISABLED")
    again = db_client.post(f"{CONNECTIONS}/{disabled_id}/disable", headers=auth)
    assert again.status_code == 200, again.text
    again_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(again.text)
    assert set(again_ack.model_dump()) == MARKETS_ACK_FIELDS
    assert again_ack.status == "DISABLED"
    assert "YIELD_CURVE_GHS" not in again.text

    enabled = db_client.post(f"{CONNECTIONS}/{connection_id}/enable", headers=auth)
    enabled_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(enabled.text)
    assert enabled_ack.status == "ACTIVE"
    revoked = db_client.delete(f"{CONNECTIONS}/{connection_id}", headers=auth)
    revoked_ack = MarketDataConnectionWriteAcknowledgement.model_validate_json(revoked.text)
    assert revoked_ack.status == "REVOKED"

    # The configure-only read boundary is intact: no read-back of the stored row.
    denied_list = db_client.get(CONNECTIONS, headers=auth)
    assert denied_list.status_code in {403, 404}, denied_list.text
    with get_sessionmaker()() as db:
        row = db.get(MarketDataConnection, connection_id)
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
    body = SourcePreferenceWriteAcknowledgement.model_validate_json(put.text)
    assert set(body.model_dump()) == {"bank_id", "curves", "fx", "rates"}
    # Only the submitted field is echoed; the stored fx/rates rows stay hidden.
    assert body.curves is not None
    assert (body.curves.source, body.curves.overlay) == ("aequor", None)
    assert body.fx is None
    assert body.rates is None

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
    body = SourcePreferenceWriteAcknowledgement.model_validate_json(put.text)
    assert body.fx is not None
    assert (body.fx.source, body.fx.overlay) == (None, False)
    assert body.curves is None and body.rates is None
    again = db_client.get(PREFERENCES, headers=_auth())
    assert again.status_code == 200, again.text
    resolved = SourcePreferencesRead.model_validate_json(again.text)
    assert resolved.fx.overlay is False


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
        "Manual upload connections are tested by uploading a file; use the upload endpoint."
    )
