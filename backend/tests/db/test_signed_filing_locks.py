"""Filed content stays sealed; corrections append a linked version."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError

from app.db.session import force_rls_suspended
from tests.db import test_governance_append_only as governance_fixtures
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
