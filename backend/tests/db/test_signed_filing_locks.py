"""Filed content stays sealed; corrections append a linked version."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.session import force_rls_suspended
from app.models import AttestationSignature, RegulatoryPackage, User
from app.services.attestation import digests, verify, workflow
from app.services.regulatory_reporting.generation import snapshot_content_hash
from tests.db import test_governance_append_only as governance_fixtures
from tests.db.test_postgres_migrations import MigratedPostgresSchema
from tests.support.helpers import ORG_1

governance_schema = governance_fixtures.governance_schema
tenant_connection = governance_fixtures.connection

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")


@pytest.fixture
def connection(tenant_connection: Connection) -> Iterator[Connection]:
    with force_rls_suspended(tenant_connection, "regulatory_packages", "regulatory_runs"):
        tenant_connection.execute(
            text(
                "GRANT UPDATE, DELETE, TRUNCATE ON regulatory_packages, "
                "regulatory_runs TO CURRENT_USER"
            )
        )
        tenant_connection.execute(text("GRANT DELETE ON attestation_signatures TO CURRENT_USER"))
        yield tenant_connection


def test_permission_layer_removes_financial_updates(tenant_connection: Connection) -> None:
    assert not tenant_connection.scalar(
        text(
            "SELECT has_column_privilege(current_user, 'regulatory_packages', 'snapshot', 'UPDATE')"
        )
    )
    assert tenant_connection.scalar(
        text("SELECT has_column_privilege(current_user, 'regulatory_packages', 'status', 'UPDATE')")
    )
    assert not tenant_connection.scalar(
        text("SELECT has_table_privilege(current_user, 'regulatory_runs', 'DELETE')")
    )


def insert_package(
    connection: Connection, *, version: int = 1, prior: UUID | None = None, figure: int = 7
) -> UUID:
    identifier = uuid4()
    connection.execute(
        text("""
        INSERT INTO regulatory_packages
          (id, organization_id, bank_id, return_family, return_code, reporting_date,
           frequency, basis, status, version, snapshot, source_runs, generated_by, generated_at,
           attestation_state, content_digest, supersedes_id, created_at, updated_at)
        VALUES (:id, :org, 'BK-SEAL001', 'bsd', 'BSD2', DATE '2026-09-30',
                'monthly', 'solo', 'generated', :version, CAST(:snapshot AS json),
                '[]', :actor, now(),
                'unsigned', repeat('a', 64), :prior, now(), now())
    """),
        {
            "id": identifier,
            "org": ORG_1,
            "version": version,
            "prior": prior,
            "actor": uuid4(),
            "snapshot": json.dumps({"figure": figure}),
        },
    )
    return identifier


def _refused(connection: Connection, sql: str, identifier: UUID) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="read-only"):
        connection.execute(text(sql), {"id": identifier})
    savepoint.rollback()


def test_signed_content_is_locked_and_resubmission_appends(connection: Connection) -> None:
    original = insert_package(connection)
    connection.execute(
        text("""
        UPDATE regulatory_packages SET attestation_state = 'fully_certified',
          fully_certified_at = now(), status = 'approved' WHERE id = :id
    """),
        {"id": original},
    )
    for assignment in (
        "snapshot = '{\"figure\": 8}'",
        "source_runs = '[{}]'",
        "content_digest = repeat('b', 64)",
        "version = 2",
    ):
        _refused(
            connection, f"UPDATE regulatory_packages SET {assignment} WHERE id = :id", original
        )
    _refused(connection, "DELETE FROM regulatory_packages WHERE id = :id", original)
    # The regulator's outcomes and supersession bookkeeping still advance.
    connection.execute(
        text("UPDATE regulatory_packages SET status = 'superseded' WHERE id = :id"),
        {"id": original},
    )
    correction = insert_package(connection, version=2, prior=original, figure=8)
    assert (
        connection.scalar(
            text("SELECT supersedes_id FROM regulatory_packages WHERE id = :id"), {"id": correction}
        )
        == original
    )
    assert (
        connection.scalar(
            text("SELECT snapshot->>'figure' FROM regulatory_packages WHERE id = :id"),
            {"id": original},
        )
        == "7"
    )
    assert (
        connection.scalar(
            text("SELECT snapshot->>'figure' FROM regulatory_packages WHERE id = :id"),
            {"id": correction},
        )
        == "8"
    )


def test_void_never_unlocks_financial_content(connection: Connection) -> None:
    identifier = insert_package(connection)
    connection.execute(
        text(
            "UPDATE regulatory_packages SET attestation_state = 'preparer_certified' WHERE id = :id"
        ),
        {"id": identifier},
    )
    connection.execute(
        text(
            "UPDATE regulatory_packages SET attestation_state = 'unsigned', "
            "attestation_cycle = attestation_cycle + 1 WHERE id = :id"
        ),
        {"id": identifier},
    )
    _refused(
        connection, "UPDATE regulatory_packages SET snapshot = '{}' WHERE id = :id", identifier
    )


def _master_data_package(
    db: Session, *, prior: RegulatoryPackage | None = None
) -> RegulatoryPackage:
    version = prior.version + 1 if prior is not None else 1
    snapshot = {"institution_name": f"Sealed Bank revision {version}"}
    actor = prior.generated_by if prior is not None else uuid4()
    if prior is None:
        db.add(User(id=actor, organization_id=ORG_1, email=f"{actor}@example.test"))
        db.flush()
    package = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id="BK-SEAL001",
        return_family="corporate",
        return_code="LRT-PROFILE",
        reporting_date=date(2026, 9, 30),
        frequency="annual",
        basis="solo",
        status="generated",
        version=version,
        supersedes_id=prior.id if prior is not None else None,
        snapshot=snapshot,
        source_runs=[],
        snapshot_sha256=snapshot_content_hash(snapshot),
        content_digest=digests.content_digest(snapshot),
        register_state_digest=digests.register_state_digest(
            [
                {
                    "table": "banks",
                    "id": "BK-SEAL001",
                    "updated_at": "2026-09-30T00:00:00Z",
                    "values": snapshot,
                }
            ]
        ),
        generated_by=actor,
        generated_at=datetime.now(UTC),
    )
    db.add(package)
    db.flush()
    return package


def _assert_master_data_sealed(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, *, runtime: bool
) -> None:
    binding = workflow.compute_binding(package)
    before = cast(dict[str, object], verify.export_verification_bundle(db, ctx, package))
    for replacement in ("repeat('d', 64)", "NULL"):
        savepoint = db.begin_nested()
        with pytest.raises(
            DatabaseError,
            match="permission denied" if runtime else "filing content is read-only",
        ):
            db.execute(
                text(
                    "UPDATE regulatory_packages SET register_state_digest = "
                    f"{replacement} WHERE id = :id"
                ),
                {"id": package.id},
            )
        savepoint.rollback()
    db.refresh(package)
    assert workflow.compute_binding(package) == binding
    after = cast(dict[str, object], verify.export_verification_bundle(db, ctx, package))
    assert after["package"] == before["package"]
    assert after["signatures"] == before["signatures"]


def _assert_signatures_and_content_sealed_under_temp_shadow(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, *, runtime: bool
) -> None:
    binding = workflow.compute_binding(package)
    before = cast(dict[str, object], verify.export_verification_bundle(db, ctx, package))
    schema = cast(str, db.scalar(text("SELECT current_schema()")))
    quoted_schema = db.get_bind().dialect.identifier_preparer.quote_identifier(schema)
    original_path = cast(str, db.scalar(text("SHOW search_path")))
    db.execute(
        text("CREATE TEMP TABLE attestation_signatures (package_id uuid, organization_id text)")
    )
    for search_path in (original_path, f"pg_temp, {quoted_schema}, pg_catalog"):
        db.execute(text("SELECT set_config('search_path', :path, true)"), {"path": search_path})
        db.execute(
            text(
                f"UPDATE {quoted_schema}.regulatory_packages "
                "SET content_digest = content_digest WHERE id = :id"
            ),
            {"id": package.id},
        )
        savepoint = db.begin_nested()
        with pytest.raises(
            DatabaseError, match="permission denied" if runtime else "append-only table"
        ):
            db.execute(
                text(f"DELETE FROM {quoted_schema}.attestation_signatures WHERE package_id = :id"),
                {"id": package.id},
            )
        savepoint.rollback()
        if runtime:
            savepoint = db.begin_nested()
            with pytest.raises(DatabaseError, match="permission denied"):
                db.execute(text(f"TRUNCATE {quoted_schema}.attestation_signatures"))
            savepoint.rollback()
        for assignment in ("content_digest = repeat('d', 64)", "content_digest = NULL"):
            savepoint = db.begin_nested()
            with pytest.raises(DatabaseError, match="signed filing is read-only"):
                db.execute(
                    text(
                        f"UPDATE {quoted_schema}.regulatory_packages "
                        f"SET {assignment} WHERE id = :id"
                    ),
                    {"id": package.id},
                )
            savepoint.rollback()
        if not runtime:
            savepoint = db.begin_nested()
            with pytest.raises(DatabaseError, match="signed filing is read-only"):
                db.execute(
                    text(f"DELETE FROM {quoted_schema}.regulatory_packages WHERE id = :id"),
                    {"id": package.id},
                )
            savepoint.rollback()
    db.execute(text("SELECT set_config('search_path', :path, true)"), {"path": original_path})
    db.execute(text("DROP TABLE pg_temp.attestation_signatures"))
    db.refresh(package)
    assert package.content_digest == binding.content_digest
    assert (
        db.scalar(
            text("SELECT count(*) FROM attestation_signatures WHERE package_id = :id"),
            {"id": package.id},
        )
        == 2
    )
    assert workflow.compute_binding(package) == binding
    after = cast(dict[str, object], verify.export_verification_bundle(db, ctx, package))
    assert after["package"] == before["package"]
    assert after["signatures"] == before["signatures"]


@pytest.mark.parametrize("runtime", [False, True], ids=["owner-trigger", "runtime-grants"])
def test_master_data_provenance_stays_sealed_through_void_and_correction(
    connection: Connection, governance_schema: MigratedPostgresSchema, runtime: bool
) -> None:
    role = governance_schema.runtime_role
    assert role is not None
    if runtime:
        connection.execute(text(f'SET LOCAL ROLE "{role}"'))
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not connection.scalar(
                text(
                    "SELECT has_table_privilege(current_user, 'attestation_signatures', :privilege)"
                ),
                {"privilege": privilege},
            )
        assert connection.scalar(
            text("SELECT has_table_privilege(current_user, 'attestation_signatures', 'INSERT')")
        )
    else:
        assert connection.scalar(
            text(
                "SELECT pg_get_userbyid(relowner) = current_user FROM pg_class "
                "WHERE oid = 'regulatory_packages'::regclass"
            )
        )
    try:
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            package = _master_data_package(db)
            ctx = TenantContext(
                organization_id=ORG_1, actor_user_id=package.generated_by, authorization_version=1
            )
            package.content_digest = None
            db.flush()
            binding = workflow.compute_binding(package)
            assert binding.binding_class == "master_data"
            _assert_master_data_sealed(db, ctx, package, runtime=runtime)
            for signing_role in ("preparer", "approver"):
                db.add(
                    AttestationSignature(
                        organization_id=ORG_1,
                        bank_id=package.bank_id,
                        package_id=package.id,
                        package_version=package.version,
                        signing_role=signing_role,
                        signer_id=f"SG-SEAL-{signing_role}",
                        signer_user_id=uuid4(),
                        binding_class=binding.binding_class,
                        certification_digest=binding.certification_digest,
                        content_digest=binding.content_digest,
                        register_state_digest=binding.register_state_digest,
                        statement="Certified profile",
                        attestation_payload={},
                        payload_digest="a" * 64,
                        signature_method="detached_ecdsa_p256_sha256",
                        signature_value=b"retained signature evidence",
                        certificate_pem="retained certificate evidence",
                        certificate_sha256="b" * 64,
                        declared_at=datetime.now(UTC),
                        prev_hash="0" * 64,
                        entry_hash="c" * 64,
                    )
                )
            db.flush()
            package.attestation_state = "fully_certified"
            package.content_digest = binding.content_digest
            package.certification_digest = binding.certification_digest
            package.fully_certified_at = datetime.now(UTC)
            package.status = "approved"
            db.flush()
            certified_export = cast(
                dict[str, object], verify.export_verification_bundle(db, ctx, package)
            )
            assert len(cast(list[object], certified_export["signatures"])) == 2
            assert (
                cast(dict[str, object], certified_export["package"])["register_state_digest"]
                == binding.register_state_digest
            )
            _assert_master_data_sealed(db, ctx, package, runtime=runtime)

            workflow.void_attestation(db, ctx, package, reason="Correction", commit=False)
            db.flush()
            assert package.attestation_state == "unsigned"
            assert package.attestation_cycle == 2
            assert package.certification_digest is None
            assert workflow.compute_binding(package) == binding
            _assert_master_data_sealed(db, ctx, package, runtime=runtime)

            _assert_signatures_and_content_sealed_under_temp_shadow(
                db, ctx, package, runtime=runtime
            )

            package.status = "superseded"
            correction = _master_data_package(db, prior=package)
            assert correction.version == 2
            assert correction.supersedes_id == package.id
            assert correction.register_state_digest != binding.register_state_digest
            assert (
                workflow.compute_binding(correction).certification_digest
                != binding.certification_digest
            )
            db.refresh(package)
            assert workflow.compute_binding(package) == binding
            retained = list(
                db.scalars(
                    select(AttestationSignature).where(
                        AttestationSignature.package_id == package.id
                    )
                )
            )
            assert len(retained) == 2
            assert all(s.register_state_digest == binding.register_state_digest for s in retained)
    finally:
        if runtime:
            connection.execute(text("RESET ROLE"))


def test_completed_run_inputs_and_history_are_read_only(connection: Connection) -> None:
    period, run = uuid4(), uuid4()
    connection.execute(
        text("""
        INSERT INTO bank_reporting_periods
          (id, organization_id, bank_id, period_start, period_end,
           label, status, created_at, updated_at)
        VALUES (:id, :org, 'BK-SEAL001', DATE '2026-09-01', DATE '2026-09-30',
                'September', 'open', now(), now())
    """),
        {"id": period, "org": ORG_1},
    )
    connection.execute(
        text("""
        INSERT INTO regulatory_runs
          (id, organization_id, bank_id, reporting_period_id, module, scenario_code, status,
           engine_version, input_schema_version, output_schema_version, input_hash, inputs,
           created_by, created_at, updated_at)
        VALUES (:id, :org, 'BK-SEAL001', :period, 'capital', 'baseline', 'running',
                'v1', 'v1', 'v1', repeat('a', 64), '{"figure": 7}', :actor, now(), now())
    """),
        {"id": run, "org": ORG_1, "period": period, "actor": uuid4()},
    )
    connection.execute(
        text(
            "UPDATE regulatory_runs SET status = 'succeeded', completed_at = now() WHERE id = :id"
        ),
        {"id": run},
    )
    _refused(connection, "UPDATE regulatory_runs SET inputs = '{}' WHERE id = :id", run)
    _refused(connection, "UPDATE regulatory_runs SET status = 'running' WHERE id = :id", run)
    _refused(connection, "DELETE FROM regulatory_runs WHERE id = :id", run)
