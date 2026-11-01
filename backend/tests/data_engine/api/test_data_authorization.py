"""Human DATA cutover: exact grants, opaque identifiers and effect-free denials."""

from __future__ import annotations

from datetime import timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select

from app.core import authorization as authorization_policy
from app.core import outbound
from app.core.authorization import (
    BindingStatus,
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    Permission,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.data_engine.api import database_connections as connection_api
from app.data_engine.schemas.ingestion import LineageWalkRead
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    CanonicalPositionSnapshot,
    DatabaseDirectConnection,
    IngestionBatch,
    Job,
    LineageRecord,
    TemenosConnection,
    User,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.support.helpers import ORG_1, USER_1, headers
from tests.support.inmemory_storage import InMemoryStorageClient

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
SIBLING = "BK-DATA0002"


@pytest.fixture(autouse=True)
def book_without_authority(db_client: TestClient) -> None:
    _ = db_client
    with get_sessionmaker()() as db:
        db.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        materialize_canonical_test_book(db)
        seed_canonical_fixture(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        db.add(
            Bank(
                id=SIBLING,
                organization_id=ORG_1,
                name="Sibling fixture",
                short_name="Sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type="universal_bank",
            )
        )
        db.commit()


def _grant(
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
    *,
    module: ModuleScope = ModuleScope.DATA,
    bank_id: str | None = SAMPLE_BANK_ID,
    bundle: RoleBundle = RoleBundle.VIEWER,
) -> UUID:
    with get_sessionmaker()() as db:
        binding = authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION if bank_id is None else InstitutionScope.INSTITUTION,
                bank_id,
                module,
                sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "data-test"),
            reason="Explicit DATA regression authority",
        )
        return binding.id


def _auth(*, version: int | None = None) -> dict[str, str]:
    with get_sessionmaker()() as db:
        user = db.get(User, USER_1)
        assert user is not None
        return headers(
            roles=("admin", "approver"),
            authorization_version=(user.authorization_version if version is None else version),
        )


def _counts() -> tuple[int, ...]:
    with get_sessionmaker()() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model)) or 0
            for model in (AuditEvent, Job, IngestionBatch, CanonicalPositionSnapshot)
        )


@pytest.mark.parametrize(
    "sensitivity",
    [
        SensitivityScope.AGGREGATED,
        SensitivityScope.CONFIDENTIAL,
        SensitivityScope.RESTRICTED,
    ],
)
def test_sensitivities_are_exact_not_hierarchical(
    db_client: TestClient,
    sensitivity: SensitivityScope,
) -> None:
    _grant(sensitivity)
    for path, required in [
        ("ingestion-summary", SensitivityScope.AGGREGATED),
        ("data-activations", SensitivityScope.AGGREGATED),
        ("ingestion-batches", SensitivityScope.CONFIDENTIAL),
        ("mapping-configs", SensitivityScope.CONFIDENTIAL),
        ("reporting-periods", SensitivityScope.CONFIDENTIAL),
        ("canonical-withdrawals", SensitivityScope.CONFIDENTIAL),
        ("system-of-record", SensitivityScope.CONFIDENTIAL),
        ("reconciliation/exceptions", SensitivityScope.CONFIDENTIAL),
        ("canonical-positions?limit=1&offset=1", SensitivityScope.RESTRICTED),
        ("canonical-positions/facets", SensitivityScope.RESTRICTED),
        ("database-direct/connections", SensitivityScope.RESTRICTED),
        ("temenos/connections", SensitivityScope.RESTRICTED),
        ("temenos/domains", SensitivityScope.RESTRICTED),
    ]:
        result = db_client.get(f"{BASE}/{path}", headers=_auth())
        assert result.status_code == (200 if sensitivity == required else 403), (path, result.text)


def test_scalar_roles_and_partial_bindings_cannot_compose(db_client: TestClient) -> None:
    before = _counts()
    assert db_client.get(f"{BASE}/ingestion-summary", headers=_auth()).status_code == 403
    _grant(SensitivityScope.RESTRICTED, bank_id=SIBLING, bundle=RoleBundle.ANALYST)
    _grant(SensitivityScope.CONFIDENTIAL, bundle=RoleBundle.ANALYST)
    _grant(SensitivityScope.RESTRICTED, module=ModuleScope.MARKETS, bundle=RoleBundle.ANALYST)
    after_grants = _counts()
    assert after_grants[1:] == before[1:]
    assert db_client.get(f"{BASE}/canonical-positions/facets", headers=_auth()).status_code == 403
    result = db_client.post(
        f"{BASE}/ingestion-uploads",
        headers=_auth(),
        files={"file": ("positions.csv", b"x\n1\n", "text/csv")},
    )
    assert result.status_code == 403
    assert _counts() == after_grants


@pytest.mark.parametrize("lifecycle", ["suspended", "revoked", "expired", "future", "inactive"])
def test_binding_and_principal_lifecycle_fail_closed(
    db_client: TestClient,
    lifecycle: str,
) -> None:
    binding_id = _grant()
    with get_sessionmaker()() as db:
        binding = db.get(AuthorizationBinding, binding_id)
        user = db.get(User, USER_1)
        assert binding is not None and user is not None
        if lifecycle in {"suspended", "revoked"}:
            binding.status = BindingStatus(lifecycle).value
            if lifecycle == "revoked":
                binding.revoked_at = utc_now()
                binding.revoked_by_type = GrantorType.SYSTEM.value
                binding.revoked_by_id = "data-test"
                binding.revoked_reason = "Lifecycle refusal test"
        elif lifecycle == "expired":
            binding.valid_from = utc_now() - timedelta(days=2)
            binding.valid_until = utc_now() - timedelta(seconds=1)
        elif lifecycle == "future":
            binding.valid_from = utc_now() + timedelta(days=1)
        else:
            user.is_active = False
        db.commit()
    before = _counts()
    result = db_client.get(f"{BASE}/canonical-positions/facets", headers=_auth())
    assert result.status_code in {401, 403}, result.text
    assert _counts() == before


def test_explicit_organization_target_and_stale_session(db_client: TestClient) -> None:
    _grant(bank_id=None)
    result = db_client.get(f"{BASE}/canonical-positions?limit=1&offset=1", headers=_auth())
    assert result.status_code == 200, result.text
    assert len(cast("list[object]", result.json()["positions"])) == 1
    assert result.json()["total"] > 1
    before = _counts()
    assert db_client.get(f"{BASE}/canonical-positions", headers=_auth(version=1)).status_code == 401
    assert _counts() == before


def test_narrow_data_scope_cannot_leak_counts(db_client: TestClient) -> None:
    binding_id = _grant(module=ModuleScope.CREDIT)
    with get_sessionmaker()() as db:
        binding = db.get(AuthorizationBinding, binding_id)
        assert binding is not None
        binding.data_scope_kind = DataScope.BRANCH.value
        binding.data_scope_values = ["FIXTURE-BRANCH"]
        db.commit()
    for path in ("canonical-positions?limit=1&offset=1", "canonical-positions/facets"):
        result = db_client.get(f"{BASE}/{path}", headers=_auth())
        assert result.status_code == 403, result.text
        assert "total" not in result.json()


def test_confidential_metadata_omits_raw_diagnostics_and_opaque_reads_deny(
    db_client: TestClient,
) -> None:
    _grant(SensitivityScope.CONFIDENTIAL)
    with get_sessionmaker()() as db:
        batch = db.scalar(select(IngestionBatch).where(IngestionBatch.bank_id == SAMPLE_BANK_ID))
        lineage = db.scalar(select(LineageRecord).where(LineageRecord.bank_id == SAMPLE_BANK_ID))
        assert batch is not None and lineage is not None
        batch.validation_report = {"failures": [{"raw": "RESTRICTED-DIAGNOSTIC"}]}
        batch.error_message = "RESTRICTED-DIAGNOSTIC"
        batch_id, lineage_id = batch.id, lineage.id
        db.commit()
    metadata = db_client.get(f"{BASE}/ingestion-batches/{batch_id}", headers=_auth())
    assert metadata.status_code == 200, metadata.text
    assert "RESTRICTED-DIAGNOSTIC" not in metadata.text
    assert metadata.json()["raw_artifact_path"] is None
    for suffix in (str(batch_id), str(uuid4())):
        assert (
            db_client.get(
                f"{BASE}/ingestion-batches/{suffix}/translation-failures", headers=_auth()
            ).status_code
            == 404
        )
    loaded: list[UUID] = []

    def observe_payload_load(row: LineageRecord, _context: object) -> None:
        loaded.append(row.id)

    event.listen(LineageRecord, "load", observe_payload_load)
    try:
        assert db_client.get(f"/api/v1/lineage/{lineage_id}", headers=_auth()).status_code == 404
    finally:
        event.remove(LineageRecord, "load", observe_payload_load)
    assert loaded == []


def test_bounded_lineage_omits_unauthorized_unvisited_parents(db_client: TestClient) -> None:
    _grant()
    with get_sessionmaker()() as db:
        root = db.scalar(select(LineageRecord).where(LineageRecord.bank_id == SAMPLE_BANK_ID))
        assert root is not None
        foreign = LineageRecord(
            organization_id=ORG_1,
            bank_id=SIBLING,
            operation_type=root.operation_type,
            operation_ref="Unrelated institution source",
            input_lineage_ids=[],
        )
        db.add(foreign)
        db.flush()
        root_id, foreign_id = root.id, foreign.id
        root.input_lineage_ids = [str(root_id)] * 49 + [str(foreign_id)] * 100
        db.commit()
    response = db_client.get(f"/api/v1/lineage/{root_id}", headers=_auth())
    assert response.status_code == 200, response.text
    walk = LineageWalkRead.model_validate_json(response.text)
    assert [node.id for node in walk.nodes] == [root_id]
    assert walk.nodes[0].input_lineage_ids == [root_id] * 49
    assert str(foreign_id) not in response.text
    with get_sessionmaker()() as db:
        stored = db.get(LineageRecord, root_id)
        assert stored is not None
        assert stored.input_lineage_ids == [str(root_id)] * 49 + [str(foreign_id)] * 100


def test_read_only_denials_do_not_touch_storage_or_enqueue(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _grant()
    calls: list[str] = []

    def refuse_storage(*_args: object, **_kwargs: object) -> None:
        calls.append("storage")
        raise AssertionError("Denied action reached storage")

    monkeypatch.setattr(InMemoryStorageClient, "write", refuse_storage)
    monkeypatch.setattr(InMemoryStorageClient, "read", refuse_storage)
    before = _counts()
    upload = db_client.post(
        f"{BASE}/ingestion-uploads",
        headers=_auth(),
        files={"file": ("positions.csv", b"x\n1\n", "text/csv")},
    )
    assert upload.status_code == 403, upload.text
    start = db_client.post(
        f"{BASE}/ingestion-batches",
        headers=_auth(),
        json={
            "source_system": "EXCEL_CSV",
            "location": "temp://denied",
            "as_of_date": FIXTURE_AS_OF.isoformat(),
            "reason": "Unauthorized attempt",
        },
    )
    assert start.status_code == 403, start.text
    for path, payload in [
        (
            "data-activations",
            {
                "as_of_date": FIXTURE_AS_OF.isoformat(),
                "reason": "Unauthorized activation",
                "run_calculations": False,
            },
        ),
        (
            f"position-snapshots/{uuid4()}/override",
            {"field": "balance", "value": "1", "reason": "Unauthorized override"},
        ),
    ]:
        result = db_client.post(f"{BASE}/{path}", headers=_auth(), json=payload)
        assert result.status_code == (404 if "override" in path else 403), result.text
    assert calls == []
    assert _counts() == before


@pytest.mark.parametrize(
    "operation", ["db-test", "db-schema", "db-sync", "t24-test", "t24-pull", "t24-backfill"]
)
def test_connector_denials_do_not_call_network_or_change_state(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    # Isolate permissions that the shipped Analyst bundle currently grants together.
    opposite = (
        Permission.RUN if operation in {"db-test", "db-schema", "t24-test"} else Permission.VALIDATE
    )
    monkeypatch.setattr(
        authorization_policy,
        "ROLE_PERMISSIONS",
        {
            **authorization_policy.ROLE_PERMISSIONS,
            RoleBundle.VIEWER: frozenset({Permission.VIEW, opposite}),
        },
    )
    _grant()
    with get_sessionmaker()() as db:
        direct = DatabaseDirectConnection(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            backend="sqlserver",
            display_name="Fixture replica",
            host="replica.example.test",
            database="fixture",
            vault_path="vault://fixture/database",
            status="ACTIVE",
        )
        temenos = TemenosConnection(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            connection_mode="OFS",
            display_name="Fixture OFS",
            endpoint="ofs://fixture.example.test",
            default_currency="GHS",
            vault_path="",
            status="ACTIVE",
        )
        db.add_all([direct, temenos])
        db.commit()
        direct_id, temenos_id = direct.id, temenos.id
    calls: list[str] = []

    def refuse_network(*_args: object, **_kwargs: object) -> None:
        calls.append("network")
        raise AssertionError("Denied connector action reached DNS")

    def refuse_storage() -> None:
        calls.append("storage")
        raise AssertionError("Denied connector action provisioned storage")

    monkeypatch.setattr(outbound, "resolve_host", refuse_network)
    monkeypatch.setattr(connection_api, "get_storage_client", refuse_storage)
    before = _counts()
    kind, action = operation.split("-", 1)
    path = (
        f"{BASE}/database-direct/connections/{direct_id}/{action}"
        if kind == "db"
        else f"{BASE}/temenos/connections/{temenos_id}/{action}"
    )
    if action == "schema":
        response = db_client.get(path, headers=_auth())
    else:
        payload = (
            {"start_date": "2026-01-01", "end_date": "2026-01-02"} if action == "backfill" else {}
        )
        response = db_client.post(path, headers=_auth(), json=payload)
    assert response.status_code == 404, response.text
    assert calls == []
    assert _counts() == before
    with get_sessionmaker()() as db:
        for row in (
            db.get(DatabaseDirectConnection, direct_id),
            db.get(TemenosConnection, temenos_id),
        ):
            assert row is not None
            assert row.status == "ACTIVE"
            assert row.last_validated_at is None
