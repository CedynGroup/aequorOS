"""Synthetic recovery through migrated seals, real versioned S3 and bank keys."""

from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import hashlib
import importlib.util
import io
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, Protocol, cast
from uuid import UUID, uuid4

import boto3
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from botocore.client import BaseClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from moto import mock_aws
from sqlalchemy import insert, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.core.audit_integrity import verify_chains
from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.types import KeyStatus, KeyUnavailableError
from app.core.tenancy import TenantContext
from app.db.base import Base
from app.reporting.service.artifact_versions import read_version_bytes
from app.storage.client import StorageLocation
from app.storage.config import StorageEngineSettings
from app.storage.encryption import ObjectEncryption
from app.storage.provisioning import ProvisioningClient
from app.storage.s3_compatible import S3CompatibleStorageClient
from scripts import backup_storage
from scripts.backup_storage import RestoredVersion, RestoreReport, StorageBackupReport
from scripts.dr_common import DisasterRecoveryError
from scripts.reconcile_storage_versions import load_mapping, reconcile_storage_versions
from tests.db.test_governance_append_only import (
    governance_schema as governance_schema,  # noqa: PLC0414 - shared fixture
)
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    postgres_administrator_url,
    postgres_schema_url,
)
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import KEY, NEXT_KEY
from tests.support.database import managed_engine
from tests.support.helpers import ORG_1, ORG_2

pytestmark = [
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required"),
    pytest.mark.database_contract,
]

TABLES = (
    "regulatory_artifact_versions",
    "regulatory_package_attachments",
    "icaap_attachments",
    "bank_supervisory_addons",
)
BANKS = ((ORG_1, "BK-REST0001"), (ORG_1, "BK-REST0002"), (ORG_2, "BK-REST0003"))
NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def recovery_migration() -> ModuleType:
    path = Path(__file__).parents[2] / "alembic/versions/202611010104_storage_version_recovery.py"
    spec = importlib.util.spec_from_file_location("storage_recovery_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def production_governance_schema(
    request: pytest.FixtureRequest,
    governance_schema: MigratedPostgresSchema,
    recovery_migration: ModuleType,
) -> MigratedPostgresSchema:
    engine = managed_engine(
        request,
        postgres_schema_url(postgres_administrator_url(), governance_schema.schema_name),
        administrative=True,
    )
    with engine.begin() as connection:
        connection.execute(
            text(f'ALTER SCHEMA "{governance_schema.schema_name}" OWNER TO aqs_owner')
        )
        for table in ("banks", "regulatory_packages", *TABLES):
            connection.execute(text(f"ALTER TABLE {table} OWNER TO aqs_owner"))
        connection.execute(text("ALTER FUNCTION aequoros_governed_row_guard() OWNER TO aqs_owner"))
        memberships: list[tuple[str, bool]] = []
        if (connection.dialect.server_version_info or (0,))[0] >= 16:
            memberships = cast(
                list[tuple[str, bool]],
                connection.execute(
                    text(
                        "SELECT parent.rolname, m.inherit_option FROM pg_auth_members m "
                        "JOIN pg_roles parent ON parent.oid = m.roleid "
                        "JOIN pg_roles member ON member.oid = m.member "
                        "WHERE member.rolname = 'aqs_owner' AND parent.rolname IN "
                        "('aequoros_audit_owner', 'aqs_storage_recovery_owner')"
                    )
                ).all(),
            )
            assert len(memberships) == 2
            for owner, _ in memberships:
                connection.execute(text(f"GRANT {owner} TO aqs_owner WITH INHERIT FALSE"))
        with Operations.context(MigrationContext.configure(connection)):
            cast(Callable[[], None], recovery_migration.downgrade)()
            connection.execute(text("SET SESSION AUTHORIZATION aqs_migrator"))
            connection.execute(text("SET LOCAL ROLE aqs_owner"))
            assert connection.scalar(text("SELECT session_user")) == "aqs_migrator"
            assert connection.scalar(text("SELECT current_user")) == "aqs_owner"
            for identity in ("aqs_owner", "aqs_migrator"):
                assert connection.scalar(
                    text(
                        "SELECT rolsuper OR rolcreaterole OR rolbypassrls OR rolinherit "
                        "FROM pg_roles WHERE rolname = :role"
                    ),
                    {"role": identity},
                ) is False
            for owner in ("aequoros_audit_owner", "aqs_storage_recovery_owner"):
                assert connection.scalar(
                    text("SELECT pg_has_role(current_user, :owner, 'USAGE')"),
                    {"owner": owner},
                ) is False
            savepoint = connection.begin_nested()
            with pytest.raises(DatabaseError, match="must be owner"):
                connection.execute(text("ALTER TABLE audit_events ADD COLUMN owner_probe boolean"))
            savepoint.rollback()
            for migrate in (
                cast(Callable[[], None], recovery_migration.upgrade),
                cast(Callable[[], None], recovery_migration.downgrade),
                cast(Callable[[], None], recovery_migration.upgrade),
            ):
                migrate()
                assert connection.scalar(text("SELECT current_user")) == "aqs_owner"
                assert connection.scalar(text("SELECT session_user")) == "aqs_migrator"
                for owner in ("aequoros_audit_owner", "aqs_storage_recovery_owner"):
                    assert connection.scalar(
                        text("SELECT has_schema_privilege(:owner, current_schema(), 'CREATE')"),
                        {"owner": owner},
                    ) is False
            connection.execute(text("RESET ROLE"))
            connection.execute(text("RESET SESSION AUTHORIZATION"))
        for owner, inherited in memberships:
            option = "TRUE" if inherited else "FALSE"
            connection.execute(text(f"GRANT {owner} TO aqs_owner WITH INHERIT {option}"))
    return governance_schema


@dataclass(frozen=True)
class Pinned:
    table: str
    id: UUID
    organization: str
    bank: str
    location: StorageLocation
    version: str
    payload: bytes

    @property
    def column(self) -> str:
        return "letter_storage_version_id" if self.table == TABLES[3] else "storage_version_id"


@pytest.fixture
def recovery_connection(
    request: pytest.FixtureRequest, production_governance_schema: MigratedPostgresSchema
) -> Iterator[Connection]:
    # The normal migration credential deliberately cannot bypass RLS. Use the
    # disposable cluster's administrator only for this incident-role contract.
    engine = managed_engine(
        request,
        postgres_schema_url(postgres_administrator_url(), production_governance_schema.schema_name),
        administrative=True,
    )
    with engine.connect() as connection, connection.begin():
        connection.execute(text("GRANT aqs_storage_recovery TO CURRENT_USER"))
        yield connection
        connection.rollback()


def _insert(connection: Connection, table: str, values: dict[str, object]) -> UUID:
    identifier = uuid4()
    connection.execute(insert(Base.metadata.tables[table]).values(id=identifier, **values))
    return identifier


def _records(  # noqa: PLR0915 - a synthetic filing fixture spans four record contracts
    connection: Connection,
    write: Callable[[StorageLocation, bytes], str] | None = None,
) -> list[Pinned]:
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, created_at, updated_at) "
            "VALUES (:org, 'Synthetic recovery tenant', now(), now())"
        ),
        {"org": ORG_2},
    )
    records: list[Pinned] = []
    for organization, bank in BANKS:
        connection.execute(
            insert(Base.metadata.tables["banks"]).values(
                id=bank,
                organization_id=organization,
                name="Synthetic recovery bank",
                short_name="Synthetic",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type="universal_bank",
                storage_slug=bank.lower(),
            )
        )
        package = _insert(
            connection,
            "regulatory_packages",
            {
                "organization_id": organization,
                "bank_id": bank,
                "return_family": "liquidity",
                "return_code": "LCR-NSFR",
                "reporting_date": date(2026, 10, 31),
                "frequency": "monthly",
                "basis": "solo",
                "status": "generated",
                "version": 1,
                "snapshot": {},
                "source_runs": [],
                "generated_by": uuid4(),
                "generated_at": NOW,
                "content_digest": "d" * 64,
            },
        )
        cycle = _insert(
            connection,
            "icaap_cycles",
            {
                "organization_id": organization,
                "bank_id": bank,
                "fiscal_year": 2026,
                "as_of_date": date(2026, 10, 31),
                "cycle_kind": "annual",
                "basis": "solo",
                "title": "Synthetic recovery cycle",
                "framework_code": "synthetic",
                "framework_version": "1",
                "framework_sha256": "c" * 64,
                "due_date_basis": "timely",
                "created_by": uuid4(),
            },
        )
        for table in TABLES:
            for revision in range(2):
                location = StorageLocation(bank.lower(), "outputs", table + "/document")
                payload = f"synthetic {bank} {table} revision {revision}".encode()
                version = write(location, payload) if write else f"old-{revision}"
                digest = hashlib.sha256(payload).hexdigest()
                values: dict[str, object] = {"organization_id": organization, "created_at": NOW}
                if table == TABLES[0]:
                    values.update(
                        package_id=package,
                        kind="pdf",
                        object_path=location.object_path,
                        storage_version_id=version,
                        checksum_sha256=digest,
                        size_bytes=len(payload),
                    )
                else:
                    values.update(bank_id=bank)
                    if table == TABLES[3]:
                        values.update(
                            status="active",
                            letter_reference=f"synthetic-{revision}",
                            letter_date=NOW.date(),
                            effective_from=NOW.date(),
                            applies_to_basis="solo",
                            basis="absolute",
                            basis_value=1,
                            currency="GHS",
                            letter_original_filename="synthetic.pdf",
                            letter_media_type="application/pdf",
                            letter_byte_size=len(payload),
                            letter_sha256=digest,
                            letter_storage_tier="outputs",
                            letter_object_path=location.object_path,
                            letter_storage_version_id=version,
                            created_by=uuid4(),
                            confirmed_by=uuid4(),
                            confirmed_at=NOW,
                            updated_at=NOW,
                        )
                    else:
                        values.update(
                            kind="other",
                            title="Synthetic recovery document",
                            original_filename="synthetic.pdf",
                            media_type="application/pdf",
                            byte_size=len(payload),
                            sha256=digest,
                            storage_tier="outputs",
                            object_path=location.object_path,
                            storage_version_id=version,
                            attributes={},
                        )
                        if table == TABLES[1]:
                            values.update(
                                package_id=package,
                                package_version=1,
                                source="package_upload",
                                gate="optional",
                                attached_by=uuid4(),
                            )
                        else:
                            values.update(cycle_id=cycle, uploaded_by=uuid4())
                identifier = _insert(connection, table, values)
                records.append(
                    Pinned(table, identifier, organization, bank, location, version, payload)
                )
    return records


def _report(records: list[Pinned]) -> RestoreReport:
    return RestoreReport(
        "synthetic",
        "synthetic",
        objects=len(records) + 1,
        versions=[
            RestoredVersion(
                r.location.bucket_name("dev"),
                r.location.object_path,
                r.version,
                "restored-" + r.version,
                hashlib.sha256(r.payload).hexdigest(),
            )
            for r in records
        ]
        + [RestoredVersion("aequoros-dev-unrelated-temp", "upload", "", "", "a" * 64)],
    )


def _snapshot(connection: Connection) -> dict[str, list[object]]:
    return {
        table: list(connection.scalars(text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY id")))
        for table in (*TABLES, "regulatory_packages", "attestation_signatures")
    }


def test_recovery_changes_only_version_references_and_chains_identity(
    recovery_connection: Connection,
) -> None:
    records = _records(recovery_connection)
    before = _snapshot(recovery_connection)
    assert reconcile_storage_versions(
        recovery_connection, _report(records), storage_env="dev", reason="synthetic incident"
    ) == len(records)
    after = _snapshot(recovery_connection)
    for table in TABLES:
        column = "letter_storage_version_id" if table == TABLES[3] else "storage_version_id"
        for old, new in zip(before[table], after[table], strict=True):
            old_row, new_row = cast(dict[str, object], old), cast(dict[str, object], new)
            assert new_row[column] == "restored-" + str(old_row[column])
            assert {k: v for k, v in old_row.items() if k != column} == {
                k: v for k, v in new_row.items() if k != column
            }
    assert before["regulatory_packages"] == after["regulatory_packages"]
    audit = cast(
        list[dict[str, str]],
        recovery_connection.execute(
            text("SELECT details FROM audit_events WHERE event_type = 'storage.version_reconciled'")
        )
        .scalars()
        .all(),
    )
    identity = cast(str, recovery_connection.scalar(text("SELECT session_user")))
    assert len(audit) == len(records)
    assert all(
        row["database_identity"] == identity
        and row["reason"] == "synthetic incident"
        and row["after"] == "restored-" + row["before"]
        for row in audit
    )
    with Session(recovery_connection) as session:
        assert all(chain.valid for chain in verify_chains(session))
    assert recovery_connection.scalar(text("SELECT count(*) FROM aqs_storage_recovery_permit")) == 0


@pytest.mark.parametrize("failure", ["missing", "duplicate", "collapsed", "empty-id", "zero-scope"])
def test_mapping_refusals_roll_back_every_reference_and_audit(
    recovery_connection: Connection,
    failure: str,
) -> None:
    records = _records(recovery_connection)
    report = _report(records)
    before = _snapshot(recovery_connection)
    if failure == "missing":
        # Force the missing mapping to be visited after every other reference,
        # proving that earlier rewrites and their audit entries roll back too.
        last = max(records, key=lambda record: (record.table, record.id))
        report.versions = [
            v
            for v in report.versions
            if (v.bucket, v.key, v.backed_up_version_id)
            != (last.location.bucket_name("dev"), last.location.object_path, last.version)
        ]
    elif failure == "duplicate":
        report.versions.append(report.versions[0])
    elif failure == "collapsed":
        report.versions[1].restored_version_id = report.versions[0].restored_version_id
    elif failure == "empty-id":
        report.versions[0].restored_version_id = ""
    savepoint = recovery_connection.begin_nested()
    with pytest.raises(DatabaseError, match="Missing|Ambiguous|Incomplete|Zero pinned"):
        reconcile_storage_versions(
            recovery_connection,
            report,
            storage_env="dev",
            reason="test",
            bank_id="BK-ABSENT" if failure == "zero-scope" else None,
        )
    savepoint.rollback()
    assert _snapshot(recovery_connection) == before
    assert (
        recovery_connection.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE event_type = 'storage.version_reconciled'"
            )
        )
        == 0
    )


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("actor", ["administrator", "runtime", "definer-role"])
def test_ordinary_updates_stay_forbidden(
    recovery_connection: Connection,
    governance_schema: MigratedPostgresSchema,
    table: str,
    actor: str,
) -> None:
    records = _records(recovery_connection)
    record = next(r for r in records if r.table == table)
    recovery_connection.execute(
        text("SELECT set_config('app.organization_id', :org, true)"), {"org": record.organization}
    )
    if actor == "runtime":
        recovery_connection.execute(text(f'SET LOCAL ROLE "{governance_schema.runtime_role}"'))
    elif actor == "definer-role":
        recovery_connection.execute(text("SET LOCAL ROLE aqs_storage_recovery_owner"))
    recovery_connection.execute(text("SELECT set_config('app.storage_recovery', 'true', true)"))
    savepoint = recovery_connection.begin_nested()
    with pytest.raises(DatabaseError, match="append-only|sealed|permission denied"):
        recovery_connection.execute(
            text(f"UPDATE {table} SET {record.column} = 'forged' WHERE id = :id"), {"id": record.id}
        )
    savepoint.rollback()


def test_scope_reconciles_one_bank_without_touching_its_sibling(
    recovery_connection: Connection,
) -> None:
    records = _records(recovery_connection)
    assert (
        reconcile_storage_versions(
            recovery_connection,
            _report(records),
            storage_env="dev",
            reason="bank-scoped",
            organization_id=ORG_1,
            bank_id=BANKS[0][1],
        )
        == 8
    )
    for record in records:
        actual = cast(
            str,
            recovery_connection.scalar(
                text(f"SELECT {record.column} FROM {record.table} WHERE id = :id"),
                {"id": record.id},
            ),
        )
        assert actual == (
            "restored-" + record.version if record.bank == BANKS[0][1] else record.version
        )


@pytest.mark.parametrize("role", ["aqs_api", "aqs_worker"])
def test_runtime_governed_update_succeeds_without_recovery_authority(
    recovery_connection: Connection, role: str
) -> None:
    record = next(r for r in _records(recovery_connection) if r.table == TABLES[3])
    recovery_connection.execute(
        text("SELECT set_config('app.organization_id', :org, true)"),
        {"org": record.organization},
    )
    for recovery_role in ("aqs_storage_recovery", "aqs_storage_recovery_owner"):
        assert recovery_connection.scalar(
            text("SELECT pg_has_role(:runtime, :recovery, 'MEMBER')"),
            {"runtime": role, "recovery": recovery_role},
        ) is False
        assert recovery_connection.scalar(
            text("SELECT rolcanlogin OR rolbypassrls FROM pg_roles WHERE rolname = :role"),
            {"role": recovery_role},
        ) is False
    recovery_connection.execute(text(f'SET LOCAL ROLE "{role}"'))
    result = cast(
        datetime,
        recovery_connection.execute(
            text(
                "UPDATE bank_supervisory_addons SET updated_at = updated_at + interval '1 second' "
                "WHERE id = :id RETURNING updated_at"
            ),
            {"id": record.id},
        ).scalar_one(),
    )
    assert result == NOW.replace(second=1)


def test_operation_requires_explicit_recovery_authority(
    recovery_connection: Connection,
    governance_schema: MigratedPostgresSchema,
) -> None:
    records = _records(recovery_connection)
    recovery_connection.execute(text("REVOKE aqs_storage_recovery FROM CURRENT_USER"))
    savepoint = recovery_connection.begin_nested()
    with pytest.raises(DatabaseError, match="Proved cross-tenant recovery session"):
        reconcile_storage_versions(
            recovery_connection, _report(records), storage_env="dev", reason="x"
        )
    savepoint.rollback()
    role = governance_schema.runtime_role
    recovery_connection.execute(text(f'SET LOCAL ROLE "{role}"'))
    with pytest.raises(DisasterRecoveryError, match="cross-tenant"):
        reconcile_storage_versions(
            recovery_connection, _report(records), storage_env="dev", reason="x"
        )
    # Even direct SQL cannot skip the application preflight.
    savepoint = recovery_connection.begin_nested()
    with pytest.raises(DatabaseError, match="permission denied"):
        recovery_connection.execute(text("SELECT aqs_reconcile_storage_versions('{}', 'dev', 'x')"))
    savepoint.rollback()


class _VersionedS3(ProvisioningClient, Protocol):
    def get_object(self, *, Bucket: str, Key: str, VersionId: str = ...) -> dict[str, object]: ...
    def put_object(
        self, *, Bucket: str, Key: str, Body: BinaryIO, Metadata: dict[str, str], ContentType: str
    ) -> dict[str, object]: ...
    def head_object(self, **kwargs: object) -> dict[str, object]: ...
    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, object]: ...
    def get_paginator(self, name: str) -> object: ...
    def list_object_versions(self, *, Bucket: str) -> dict[str, object]: ...
    def delete_object(self, **kwargs: object) -> object: ...
    def delete_bucket(self, *, Bucket: str) -> object: ...


def _signature(connection: Connection, record: Pinned, key: ec.EllipticCurvePrivateKey) -> None:
    package = cast(
        UUID,
        connection.scalar(
            text("SELECT package_id FROM regulatory_artifact_versions WHERE id = :id"),
            {"id": record.id},
        ),
    )
    digest = hashlib.sha256(record.payload).hexdigest()
    _insert(
        connection,
        "attestation_signatures",
        {
            "organization_id": record.organization,
            "bank_id": record.bank,
            "package_id": package,
            "package_version": 1,
            "signing_role": "preparer",
            "signer_id": "synthetic-signer",
            "signer_user_id": uuid4(),
            "signer_display_name": "Synthetic Signer",
            "binding_class": "engine_run",
            "certification_digest": digest,
            "content_digest": "d" * 64,
            "statement": "Synthetic recovery evidence",
            "attestation_payload": {"synthetic": True},
            "payload_digest": digest,
            "signature_method": "detached_ecdsa_p256_sha256",
            "signature_value": key.sign(record.payload, ec.ECDSA(hashes.SHA256())),
            "certificate_pem": key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode(),
            "certificate_sha256": "a" * 64,
            "declared_at": NOW,
            "artifact_version_id": record.id,
            "prev_hash": "0" * 64,
            "entry_hash": digest,
        },
    )


def test_encrypted_backup_loss_restore_reconcile_downloads_original_signed_revisions(  # noqa: PLR0915 - full recovery acceptance
    recovery_connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = LocalKeyProvider()
    provider.add_key(KEY)
    provider.add_key(NEXT_KEY)
    settings = StorageEngineSettings.model_validate(
        {
            "STORAGE_BACKEND": "s3",
            "STORAGE_ENV": "dev",
            "S3_REGION": "us-east-1",
            "S3_ENDPOINT": None,
            "STORAGE_KMS_KEY_ID": None,
        }
    )
    monkeypatch.setattr(backup_storage, "get_storage_settings", lambda: settings)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    signing_key = ec.generate_private_key(ec.SECP256R1())

    def session() -> Session:
        return Session(recovery_connection, join_transaction_mode="create_savepoint")

    with mock_aws():
        factory = cast(Callable[..., BaseClient], boto3.client)
        s3 = cast(_VersionedS3, cast(object, factory("s3", region_name="us-east-1")))
        store = registry.DatabaseEnvelopeStore(session, lambda _name: provider)
        storage = S3CompatibleStorageClient(
            settings,
            client_factory=lambda *_args, **_kwargs: s3,
            encryption=ObjectEncryption(store),
        )
        registered: set[str] = set()

        def write(location: StorageLocation, payload: bytes) -> str:
            if location.institution_slug not in registered:
                organization, bank = next(
                    (o, b) for o, b in BANKS if b.lower() == location.institution_slug
                )
                with session() as db:
                    registry.register(
                        db,
                        bank_id=bank,
                        organization_id=organization,
                        storage_slug=location.institution_slug,
                        key=KEY,
                        providers=lambda _: provider,
                    )
                    db.commit()
                storage.ensure_institution(location.institution_slug)
                registered.add(location.institution_slug)
            descriptor = storage.write(
                location,
                io.BytesIO(payload),
                metadata_for(location.institution_slug, "outputs", payload),
            )
            assert descriptor.version_id
            return descriptor.version_id

        records = _records(recovery_connection, write)
        for _, bank in BANKS:
            location = StorageLocation(bank.lower(), "temp", "upload")
            payload = b"synthetic pending upload"
            descriptor = storage.write(
                location, io.BytesIO(payload), metadata_for(bank.lower(), "temp", payload)
            )
            assert not descriptor.version_id
        for record in records:
            if record.table == TABLES[0]:
                _signature(recovery_connection, record, signing_key)
        before = _snapshot(recovery_connection)
        buckets = sorted(
            {r.location.bucket_name("dev") for r in records}
            | {StorageLocation(bank.lower(), "temp", "upload").bucket_name("dev") for _, bank in BANKS}
        )
        inventory = [backup_storage.inventory_bucket(s3, b, out_dir=tmp_path) for b in buckets]
        assert all(
            b.ok and b.objects == (1 if b.bucket.endswith("-temp") else 8) for b in inventory
        )
        assert all(b.recoverable for b in inventory if b.bucket.endswith("-outputs"))
        assert all(not b.recoverable for b in inventory if b.bucket.endswith("-temp"))
        manifest = tmp_path / "manifest.json"
        StorageBackupReport(
            "synthetic",
            "moto",
            True,
            inventory,
            format_version=backup_storage.STORAGE_FORMAT_VERSION,
        ).write(manifest)
        # Rotation leaves archived envelopes dependent on the original key.
        with session() as db:
            for organization, bank in BANKS:
                row = registry.scoped_key(db, organization_id=organization, bank_id=bank)
                registry.rotate(db, row, NEXT_KEY, lambda _: provider)
            db.commit()
        envelopes_before = list(
            recovery_connection.scalars(
                text("SELECT to_jsonb(e) FROM object_key_envelopes e ORDER BY id")
            )
        )
        # Physically lose all retained versions; a delete marker alone leaves
        # version-pinned downloads working and cannot prove this recovery gap.
        for bucket in buckets:
            versions = cast(
                list[dict[str, object]], s3.list_object_versions(Bucket=bucket)["Versions"]
            )
            for version in versions:
                s3.delete_object(Bucket=bucket, Key=version["Key"], VersionId=version["VersionId"])
            s3.delete_bucket(Bucket=bucket)
        artifact = next(r for r in records if r.table == TABLES[0])
        with session() as db, pytest.raises(HTTPException):
            read_version_bytes(
                db, TenantContext(artifact.organization), artifact.bank, artifact.id, storage
            )
        restored = backup_storage.restore_storage_backup(s3, manifest, out_dir=tmp_path)
        temp_versions = [v for v in restored.versions if v.bucket.endswith("-temp")]
        assert len(temp_versions) == len(BANKS)
        assert all(not v.restored_version_id for v in temp_versions)
        mapping = tmp_path / "restore.json"
        restored.write(mapping)
        assert reconcile_storage_versions(
            recovery_connection,
            load_mapping(mapping),
            storage_env="dev",
            reason="synthetic store loss",
        ) == len(records)
        after = _snapshot(recovery_connection)
        assert after["attestation_signatures"] == before["attestation_signatures"]
        assert after["regulatory_packages"] == before["regulatory_packages"]
        assert (
            list(
                recovery_connection.scalars(
                    text("SELECT to_jsonb(e) FROM object_key_envelopes e ORDER BY id")
                )
            )
            == envelopes_before
        )
        for record in records:
            current = cast(
                str,
                recovery_connection.scalar(
                    text(f"SELECT {record.column} FROM {record.table} WHERE id = :id"),
                    {"id": record.id},
                ),
            )
            assert current and current != record.version
            _, body = storage.read(record.location, version_id=str(current))
            with body:
                assert body.read() == record.payload
            if record.table == TABLES[0]:
                with session() as db:
                    row, payload = read_version_bytes(
                        db, TenantContext(record.organization), record.bank, record.id, storage
                    )
                    assert payload == record.payload
                    signature = cast(
                        bytes,
                        recovery_connection.scalar(
                            text(
                                "SELECT signature_value FROM attestation_signatures "
                                "WHERE artifact_version_id = :id"
                            ),
                            {"id": record.id},
                        ),
                    )
                    signing_key.public_key().verify(
                        bytes(signature), payload, ec.ECDSA(hashes.SHA256())
                    )
                    assert row.checksum_sha256 == hashlib.sha256(payload).hexdigest()
                for organization, other_bank in BANKS:
                    if other_bank != record.bank:
                        with session() as db, pytest.raises(HTTPException) as refusal:
                            read_version_bytes(
                                db, TenantContext(organization), other_bank, record.id, storage
                            )
                        assert refusal.value.status_code == 404
        # Repairing the pin does not grant access to a revoked/unavailable key.
        for state in (KeyStatus.DISABLED, KeyStatus.UNAVAILABLE):
            provider.set_status(KEY, state)
            with session() as db, pytest.raises(KeyUnavailableError):
                read_version_bytes(
                    db, TenantContext(artifact.organization), artifact.bank, artifact.id, storage
                )
            provider.set_status(KEY, KeyStatus.ACTIVE)
        with session() as db:
            assert all(chain.valid for chain in verify_chains(db))


def test_capability_without_cross_tenant_session_authority_is_refused(
    recovery_connection: Connection,
    governance_schema: MigratedPostgresSchema,
) -> None:
    role = governance_schema.runtime_role
    assert role is not None
    recovery_connection.execute(text(f'GRANT aqs_storage_recovery TO "{role}"'))
    recovery_connection.execute(text(f'SET SESSION AUTHORIZATION "{role}"'))
    savepoint = recovery_connection.begin_nested()
    with pytest.raises(DatabaseError, match="Proved cross-tenant recovery session"):
        recovery_connection.execute(text("SELECT aqs_reconcile_storage_versions('{}', 'dev', 'x')"))
    savepoint.rollback()
    recovery_connection.execute(text("RESET SESSION AUTHORIZATION"))


def test_runtime_cannot_forge_recovery_permit_or_assume_its_owner(
    recovery_connection: Connection,
    governance_schema: MigratedPostgresSchema,
) -> None:
    role = governance_schema.runtime_role
    assert role is not None
    recovery_connection.execute(text(f'SET SESSION AUTHORIZATION "{role}"'))
    for sql in (
        "INSERT INTO aqs_storage_recovery_permit VALUES "
        "(pg_backend_pid(), txid_current(), 'icaap_attachments', gen_random_uuid(), 'old', 'new')",
        "SET LOCAL ROLE aqs_storage_recovery_owner",
    ):
        savepoint = recovery_connection.begin_nested()
        with pytest.raises(DatabaseError, match="permission denied"):
            recovery_connection.execute(text(sql))
        savepoint.rollback()

    recovery_connection.execute(text("RESET SESSION AUTHORIZATION"))


@pytest.mark.parametrize("table", TABLES)
def test_even_an_exact_recovery_permit_cannot_change_sealed_content(
    recovery_connection: Connection,
    table: str,
) -> None:
    record = next(r for r in _records(recovery_connection) if r.table == table)
    checksum = (
        "letter_sha256"
        if table == TABLES[3]
        else "checksum_sha256"
        if table == TABLES[0]
        else "sha256"
    )
    recovery_connection.execute(
        text(
            "INSERT INTO aqs_storage_recovery_permit VALUES "
            "(pg_backend_pid(), txid_current(), :table, :id, :old, 'new')"
        ),
        {"table": table, "id": record.id, "old": record.version},
    )
    recovery_connection.execute(text("SET LOCAL ROLE aqs_storage_recovery_owner"))
    savepoint = recovery_connection.begin_nested()
    with pytest.raises(DatabaseError, match="append-only|sealed"):
        recovery_connection.execute(
            text(
                f"UPDATE {table} SET {record.column} = 'new', {checksum} = :checksum WHERE id = :id"
            ),
            {"checksum": "f" * 64, "id": record.id},
        )
    savepoint.rollback()


def test_recovery_migration_round_trip_restores_the_original_guards(
    recovery_connection: Connection,
    recovery_migration: ModuleType,
) -> None:
    records = _records(recovery_connection)
    before = _snapshot(recovery_connection)
    with Operations.context(MigrationContext.configure(recovery_connection)):
        cast(Callable[[], None], recovery_migration.downgrade)()
        for table in TABLES:
            record = next(r for r in records if r.table == table)
            checksum = (
                "letter_sha256"
                if table == TABLES[3]
                else "checksum_sha256"
                if table == TABLES[0]
                else "sha256"
            )
            for column, value in ((record.column, "forged"), (checksum, "f" * 64)):
                savepoint = recovery_connection.begin_nested()
                with pytest.raises(DatabaseError, match="append-only|sealed"):
                    recovery_connection.execute(
                        text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
                        {"value": value, "id": record.id},
                    )
                savepoint.rollback()
        assert _snapshot(recovery_connection) == before
        recovery_connection.execute(text("SET LOCAL ROLE aqs_owner"))
        cast(Callable[[], None], recovery_migration.upgrade)()
        recovery_connection.execute(text("RESET ROLE"))
    assert reconcile_storage_versions(
        recovery_connection,
        _report(records),
        storage_env="dev",
        reason="synthetic migration round trip",
    ) == len(records)
