"""DATA configure cutover: mapping and connection lifecycle need exact grants.

Mapping-config creation and database-direct/Temenos connection lifecycle and
credential validation (create, patch, enable, disable, revoke, validate) are
configuration duties: they require a complete active
``data/configure/restricted`` exact-bank binding. Denials happen BEFORE any
side effect — no vault write, no network call, no mapping mutation, no audit
success row. Operational test/schema/sync/pull keep their existing
VALIDATE/RUN gates and gain nothing from Configurator (no broadening).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core import outbound
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
from app.data_engine.adapters.database_direct.config import Backend
from app.data_engine.adapters.database_direct.credential_vault import (
    DatabaseDirectCredentialVault,
)
from app.data_engine.adapters.database_direct.drivers.base import (
    ColumnSchema,
    DatabaseDriver,
    TableSchema,
)
from app.data_engine.adapters.database_direct.fixtures import Dump, OfflineDumpDriver
from app.data_engine.adapters.temenos_t24.credential_vault import TemenosCredentialVault
from app.data_engine.schemas.database_connection import DatabaseConnectionRead
from app.data_engine.schemas.ingestion import MappingConfigRead
from app.data_engine.schemas.temenos_connections import TemenosConnectionRead
from app.data_engine.service import database_connections as database_connections_service
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    DatabaseDirectConnection,
    MappingConfigRecord,
    TemenosConnection,
    User,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.outbound import stub_public_dns
from tests.support.helpers import ORG_1, USER_1, error_envelope, headers
from tests.support.inmemory_storage import InMemoryStorageClient

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
SIBLING = "BK-DATA0003"
DB_BASE = f"{BASE}/database-direct/connections"
T24_BASE = f"{BASE}/temenos/connections"

MASTER_KEY = "data-configure-test-master-key"
SECRET = "svc-password-that-must-never-leak"

MAPPING_PAYLOAD: dict[str, Any] = {
    "source_system": "EXCEL_CSV",
    "name": "Configure cutover mapping",
    "config": {
        "field_mappings": {
            "gl_account": {
                "source_table": "GL",
                "fields": {
                    "source_reference": "Code",
                    "account_code": "Code",
                    "name": "Label",
                    "account_class": "Class",
                },
            }
        },
    },
    "activate": True,
    "reason": "Configure-cutover regression mapping",
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
                name="Configure sibling fixture",
                short_name="ConfigureSibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type="universal_bank",
            )
        )
        db.commit()


def offline_driver_for(dump: Dump) -> Callable[[Backend], DatabaseDriver]:
    """A typed ``driver_for`` stand-in that always answers with the offline dump."""
    dump_driver = OfflineDumpDriver(dump)
    return lambda _backend: dump_driver


@pytest.fixture(autouse=True)
def offline_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the db-direct driver seam so nothing here needs a live core."""
    dump = Dump(
        database="COREBANK",
        tables=(
            TableSchema(
                name="GL_ACCOUNTS",
                schema="DBO",
                columns=(ColumnSchema(name="ACCT_CODE", data_type="varchar", nullable=False),),
            ),
        ),
        rows={"DBO.GL_ACCOUNTS": [{"ACCT_CODE": "1000"}]},
    )
    monkeypatch.setattr(database_connections_service, "driver_for", offline_driver_for(dump))


@pytest.fixture(autouse=True)
def resolvable_core(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_public_dns(monkeypatch, "core-db.internal", "sample-bank")


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


def _grant(
    bundle: RoleBundle,
    *,
    module: ModuleScope = ModuleScope.DATA,
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
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "data-configure-test"),
            reason="DATA configure-cutover regression authority",
        )
        db.commit()


def _auth() -> dict[str, str]:
    # Scalar roles ("admin", "approver") must grant nothing by themselves:
    # only the explicit scoped binding may authorize these routes.
    with get_sessionmaker()() as db:
        user = db.get(User, USER_1)
        assert user is not None
        return headers(
            roles=("admin", "approver"), authorization_version=user.authorization_version
        )


def _counts() -> tuple[int, int, int, int]:
    with get_sessionmaker()() as db:
        return (
            db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
            db.scalar(select(func.count()).select_from(MappingConfigRecord)) or 0,
            db.scalar(select(func.count()).select_from(DatabaseDirectConnection)) or 0,
            db.scalar(select(func.count()).select_from(TemenosConnection)) or 0,
        )


def _seed_db_connection() -> UUID:
    with get_sessionmaker()() as db:
        row = DatabaseDirectConnection(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            backend="sqlserver",
            display_name=f"Configure fixture [{uuid4().hex[:8]}]",
            host="core-db.internal",
            database="COREBANK",
            vault_path="vault://fixture/database",
            status="ACTIVE",
        )
        db.add(row)
        db.commit()
        return row.id


def _seed_temenos_connection() -> UUID:
    with get_sessionmaker()() as db:
        row = TemenosConnection(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            connection_mode="OFS",
            display_name=f"Configure T24 fixture [{uuid4().hex[:8]}]",
            endpoint="ofs://sample-bank",
            default_currency="GHS",
            vault_path="",
            status="ACTIVE",
        )
        db.add(row)
        db.commit()
        return row.id


def _db_create_payload() -> dict[str, Any]:
    return {
        "backend": "sqlserver",
        "display_name": f"Configure create [{uuid4().hex[:8]}]",
        "host": "core-db.internal",
        "port": 1433,
        "database": "COREBANK",
        "schemas": ["DBO"],
        "credentials": {"username": "AEQUOROS_RO", "password": SECRET},
        "extraction_spec": {
            "tables": [{"table": "DBO.GL_ACCOUNTS", "record_kind": "gl_account"}],
            "default_mode": "full",
        },
    }


def _temenos_create_payload() -> dict[str, Any]:
    return {
        "connection_mode": "OFS",
        "display_name": f"Configure T24 create [{uuid4().hex[:8]}]",
        "endpoint": "ofs://sample-bank",
        "default_currency": "GHS",
        "credentials": {"username": "SVC.AEQUOROS", "password": SECRET},
    }


def _refuse_side_effects(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Fail loudly if a denied configure action writes the vault or dials out."""
    calls: dict[str, list[str]] = {"vault": [], "network": []}

    def refuse_seal(*_args: object, **_kwargs: object) -> None:
        calls["vault"].append("seal")
        raise AssertionError("Denied configure action wrote the vault")

    def refuse_store(*_args: object, **_kwargs: object) -> None:
        calls["vault"].append("store")
        raise AssertionError("Denied configure action wrote the vault")

    def refuse_dns(*_args: object, **_kwargs: object) -> None:
        calls["network"].append("dns")
        raise AssertionError("Denied configure action resolved DNS")

    monkeypatch.setattr(DatabaseDirectCredentialVault, "seal", refuse_seal)
    monkeypatch.setattr(TemenosCredentialVault, "store", refuse_store)
    monkeypatch.setattr(outbound, "resolve_host", refuse_dns)
    return calls


# --- Mapping-config creation -------------------------------------------------


def test_mapping_creation_requires_data_configure(db_client: TestClient) -> None:
    path = f"{BASE}/mapping-configs"
    before = _counts()
    assert db_client.post(path, headers=_auth(), json=MAPPING_PAYLOAD).status_code in {403, 404}
    assert _counts() == before

    _grant(RoleBundle.ANALYST)
    before = _counts()
    denied = db_client.post(path, headers=_auth(), json=MAPPING_PAYLOAD)
    assert denied.status_code == 403, denied.text
    assert error_envelope(denied)["message"] == "This action requires an active DATA binding."
    assert _counts() == before


@pytest.mark.usefixtures("vault_key")
def test_mapping_creation_succeeds_with_configurator_binding(db_client: TestClient) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    created = db_client.post(f"{BASE}/mapping-configs", headers=_auth(), json=MAPPING_PAYLOAD)
    assert created.status_code == 200, created.text
    read = MappingConfigRead.model_validate_json(created.text)
    assert read.status == "active"
    with get_sessionmaker()() as db:
        row = db.get(MappingConfigRecord, read.id)
        assert row is not None
        assert row.source_system == "EXCEL_CSV"


# --- Database-direct lifecycle ----------------------------------------------


def test_database_direct_lifecycle_denied_without_configure(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = _seed_db_connection()
    _grant(RoleBundle.ANALYST)
    before = _counts()
    calls = _refuse_side_effects(monkeypatch)

    denied_create = db_client.post(DB_BASE, headers=_auth(), json=_db_create_payload())
    assert denied_create.status_code == 403, denied_create.text
    object_ops = (
        ("PATCH", f"{DB_BASE}/{connection_id}", {"display_name": "Renamed"}),
        ("POST", f"{DB_BASE}/{connection_id}/disable", None),
        ("POST", f"{DB_BASE}/{connection_id}/enable", None),
        ("DELETE", f"{DB_BASE}/{connection_id}", None),
    )
    for method, path, payload in object_ops:
        hidden = db_client.request(method, path, headers=_auth(), json=payload)
        assert hidden.status_code == 404, (path, hidden.text)
        assert error_envelope(hidden)["message"] == "Data object not found."
    assert calls == {"vault": [], "network": []}
    assert _counts() == before
    with get_sessionmaker()() as db:
        row = db.get(DatabaseDirectConnection, connection_id)
        assert row is not None
        assert row.status == "ACTIVE"


@pytest.mark.usefixtures("vault_key")
def test_database_direct_lifecycle_succeeds_with_configurator_binding(
    db_client: TestClient,
) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    created = db_client.post(DB_BASE, headers=_auth(), json=_db_create_payload())
    assert created.status_code == 201, created.text
    assert SECRET not in created.text
    read = DatabaseConnectionRead.model_validate_json(created.text)
    connection_id = str(read.id)
    assert read.status == "ACTIVE"

    patched = db_client.request(
        "PATCH", f"{DB_BASE}/{connection_id}", headers=_auth(), json={"display_name": "Renamed"}
    )
    assert patched.status_code == 200, patched.text
    patched_read = DatabaseConnectionRead.model_validate_json(patched.text)
    assert patched_read.display_name == "Renamed"
    disabled = db_client.post(f"{DB_BASE}/{connection_id}/disable", headers=_auth())
    assert DatabaseConnectionRead.model_validate_json(disabled.text).status == "DISABLED"
    enabled = db_client.post(f"{DB_BASE}/{connection_id}/enable", headers=_auth())
    assert DatabaseConnectionRead.model_validate_json(enabled.text).status == "ACTIVE"
    revoked = db_client.delete(f"{DB_BASE}/{connection_id}", headers=_auth())
    assert DatabaseConnectionRead.model_validate_json(revoked.text).status == "REVOKED"


# --- Temenos lifecycle and credential validation -----------------------------


def test_temenos_lifecycle_denied_without_configure(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = _seed_temenos_connection()
    _grant(RoleBundle.ANALYST)
    before = _counts()
    calls = _refuse_side_effects(monkeypatch)

    denied_create = db_client.post(T24_BASE, headers=_auth(), json=_temenos_create_payload())
    assert denied_create.status_code == 403, denied_create.text
    # Credential validation is a configure duty (D2): denied before any check.
    denied_validate = db_client.post(f"{T24_BASE}/{connection_id}/validate", headers=_auth())
    assert denied_validate.status_code == 404, denied_validate.text
    object_ops = (
        ("PATCH", f"{T24_BASE}/{connection_id}", {"display_name": "Renamed"}),
        ("POST", f"{T24_BASE}/{connection_id}/disable", None),
        ("POST", f"{T24_BASE}/{connection_id}/enable", None),
        ("DELETE", f"{T24_BASE}/{connection_id}", None),
    )
    for method, path, payload in object_ops:
        hidden = db_client.request(method, path, headers=_auth(), json=payload)
        assert hidden.status_code == 404, (path, hidden.text)
    assert calls == {"vault": [], "network": []}
    assert _counts() == before
    with get_sessionmaker()() as db:
        row = db.get(TemenosConnection, connection_id)
        assert row is not None
        assert row.status == "ACTIVE"


@pytest.mark.usefixtures("vault_key")
def test_temenos_lifecycle_and_validation_succeed_with_configurator_binding(
    db_client: TestClient,
) -> None:
    _grant(RoleBundle.CONFIGURATOR)
    created = db_client.post(T24_BASE, headers=_auth(), json=_temenos_create_payload())
    assert created.status_code == 201, created.text
    assert SECRET not in created.text
    read = TemenosConnectionRead.model_validate_json(created.text)
    connection_id = str(read.id)
    assert read.status == "ACTIVE"

    validated = db_client.post(f"{T24_BASE}/{connection_id}/validate", headers=_auth())
    assert validated.status_code == 200, validated.text
    assert TemenosConnectionRead.model_validate_json(validated.text).status == "ACTIVE"

    patched = db_client.request(
        "PATCH", f"{T24_BASE}/{connection_id}", headers=_auth(), json={"display_name": "Renamed"}
    )
    assert patched.status_code == 200, patched.text
    disabled = db_client.post(f"{T24_BASE}/{connection_id}/disable", headers=_auth())
    assert TemenosConnectionRead.model_validate_json(disabled.text).status == "DISABLED"
    enabled = db_client.post(f"{T24_BASE}/{connection_id}/enable", headers=_auth())
    assert TemenosConnectionRead.model_validate_json(enabled.text).status == "ACTIVE"
    revoked = db_client.delete(f"{T24_BASE}/{connection_id}", headers=_auth())
    assert TemenosConnectionRead.model_validate_json(revoked.text).status == "REVOKED"


# --- Exact binding dimensions ------------------------------------------------


@pytest.mark.parametrize(
    ("module", "sensitivity", "bank_id"),
    [
        (ModuleScope.CREDIT, SensitivityScope.RESTRICTED, SAMPLE_BANK_ID),
        (ModuleScope.DATA, SensitivityScope.CONFIDENTIAL, SAMPLE_BANK_ID),
        (ModuleScope.DATA, SensitivityScope.RESTRICTED, SIBLING),
    ],
)
def test_mismatched_configure_binding_dimensions_are_denied(
    db_client: TestClient, module: ModuleScope, sensitivity: SensitivityScope, bank_id: str
) -> None:
    _grant(RoleBundle.CONFIGURATOR, module=module, sensitivity=sensitivity, bank_id=bank_id)
    before = _counts()
    # A binding elsewhere in the organization still covers the request context
    # (403 on missing permission); object-scoped routes stay opaque 404.
    assert (
        db_client.post(f"{BASE}/mapping-configs", headers=_auth(), json=MAPPING_PAYLOAD).status_code
        == 403
    )
    assert _counts() == before


@pytest.mark.parametrize("lifecycle", ["suspended", "revoked", "expired", "future"])
def test_binding_lifecycle_fail_closed_for_data_configure(
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
            binding.revoked_by_id = "data-configure-test"
            binding.revoked_reason = "Configure-cutover revocation test"
        elif lifecycle == "expired":
            binding.valid_from = utc_now() - timedelta(days=2)
            binding.valid_until = utc_now() - timedelta(seconds=1)
        else:
            binding.valid_from = utc_now() + timedelta(days=1)
        db.commit()
    before = _counts()
    denied = db_client.post(f"{BASE}/mapping-configs", headers=_auth(), json=MAPPING_PAYLOAD)
    assert denied.status_code in {403, 404}, denied.text
    assert _counts() == before


# --- Operational boundaries unchanged ---------------------------------------


@pytest.mark.parametrize(
    "operation", ["db-test", "db-schema", "db-sync", "t24-test", "t24-pull", "t24-backfill"]
)
def test_configurator_gains_no_operational_run_authority(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Operational test/schema/sync/pull keep their VALIDATE/RUN gates (D2):
    a configure-only binding must not reach the driver, storage, or core."""
    connection_id = _seed_db_connection()
    temenos_id = _seed_temenos_connection()
    _grant(RoleBundle.CONFIGURATOR)
    before = _counts()
    calls = _refuse_side_effects(monkeypatch)

    def refuse_storage(*_args: object, **_kwargs: object) -> None:
        calls["vault"].append("storage")
        raise AssertionError("Denied operational action provisioned storage")

    monkeypatch.setattr(InMemoryStorageClient, "write", refuse_storage)

    kind, action = operation.split("-", 1)
    path = (
        f"{DB_BASE}/{connection_id}/{action}"
        if kind == "db"
        else f"{T24_BASE}/{temenos_id}/{action}"
    )
    if action == "schema":
        response = db_client.get(path, headers=_auth())
    else:
        payload = (
            {"start_date": "2026-01-01", "end_date": "2026-01-02"} if action == "backfill" else {}
        )
        response = db_client.post(path, headers=_auth(), json=payload)
    assert response.status_code == 404, (path, response.text)
    assert calls == {"vault": [], "network": []}
    assert _counts() == before
