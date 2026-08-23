"""The governance registers are sealed by the DATABASE, not by convention.

Audit 2026-08-22 D-17. ``202608230038`` installs the SEALED tier — the
governed-lifecycle sibling of ``202607250027``'s UNALTERABLE tier — on the four
tables this programme added: the regulatory-parameter control plane, the
reconciliation escape valve, the canonical-withdrawal ledger and the
system-of-record register.

Two things have to be true and neither is provable from the migration text:

* the seal **holds** — the value, the ceiling, the scope and the approval
  evidence cannot be rewritten in place once the row is authoritative;
* the seal **does not break the product** — the governed lifecycle each service
  actually performs (``approve``, ``apply``, ``reverse``, ``revoke``,
  supersession) still runs.

A trigger satisfying only the first is a broken deploy, so every table below
pins both halves.

Postgres-gated: SQLite has no triggers of this shape, and the hermetic suite
builds its schema with ``Base.metadata.create_all`` and runs no migration at all.
The migration chain is expensive, so this module builds ONE schema and shares it;
every case runs inside a transaction it rolls back.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError

from alembic import command
from tests.api.helpers import ORG_1
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)

postgres_only = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the governance append-only checks.",
)

_BANK_ID = "BK-SEAL001"
_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _load_migration() -> ModuleType:
    """The migration module, by path: ``alembic/versions`` is not a package and
    the file name starts with a digit, so it cannot be imported by name."""
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "202608230038_governance_append_only.py"
    )
    spec = importlib.util.spec_from_file_location("governance_append_only_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GOVERNANCE_MIGRATION = _load_migration()


@pytest.fixture(scope="module")
def governance_schema() -> Iterator[MigratedPostgresSchema]:
    """One migrated schema for the whole module, plus the org + bank to hang rows on."""
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_sealed_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    app_engine = create_engine(database_url)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        command.upgrade(alembic_config_for_app(), "head")
        with app_engine.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, false)"),
                {"org": str(ORG_1)},
            )
            connection.execute(
                text(
                    "INSERT INTO organizations (id, name, created_at, updated_at) "
                    "VALUES (:org, 'Sealed tenant', now(), now())"
                ),
                {"org": str(ORG_1)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO banks
                      (id, organization_id, name, short_name, currency, jurisdiction_code,
                       license_type, institution_type, created_at, updated_at)
                    VALUES
                      (:bank, :org, 'Sealed Bank', 'SEAL', 'GHS', 'GH',
                       'universal', 'universal_bank', now(), now())
                    """
                ),
                {"bank": _BANK_ID, "org": str(ORG_1)},
            )
            connection.commit()
        yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
    finally:
        monkeypatch.undo()
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def connection(governance_schema: MigratedPostgresSchema) -> Iterator[Connection]:
    """A tenant-scoped connection whose whole transaction is rolled back."""
    with governance_schema.app_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('app.organization_id', :org, false)"),
            {"org": str(ORG_1)},
        )
        # ``false`` makes the GUC session-scoped, so it survives the commit that
        # closes the autobegun transaction and lets the case own an explicit one.
        conn.commit()
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def _refused(connection: Connection, statement: str, params: dict[str, Any], match: str) -> None:
    """Assert one statement is refused, without losing the enclosing transaction."""
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match=match):
        connection.execute(text(statement), params)
    savepoint.rollback()


# -- row builders ---------------------------------------------------------


def _insert_exception(connection: Connection, row_id: UUID) -> None:
    connection.execute(
        text(
            """
            INSERT INTO reconciliation_exceptions
              (id, organization_id, bank_id, control, max_gap_fraction, effective_from,
               reason, requested_at, approved_by, approval_timestamp, created_at, updated_at)
            VALUES
              (:id, :org, :bank, 'balance_sheet_identity', 0.01, DATE '2026-01-01',
               'Migration cutover residue, board approved', :now, 'Second Officer',
               :now, :now, :now)
            """
        ),
        {"id": str(row_id), "org": str(ORG_1), "bank": _BANK_ID, "now": _NOW},
    )


def _insert_withdrawal(connection: Connection, row_id: UUID, *, withdrawal_status: str) -> None:
    approved = withdrawal_status != "pending"
    connection.execute(
        text(
            """
            INSERT INTO canonical_withdrawals
              (id, organization_id, bank_id, source_system, as_of_date, entity,
               position_type, reason, status, requested_by, requested_at,
               approved_by, approved_at, withdrawal_batch_id, rows_withdrawn,
               created_at, updated_at)
            VALUES
              (:id, :org, :bank, 'T24', DATE '2026-06-30', 'position',
               'LOAN', 'Duplicated book from the retired core', :status,
               'Maker Officer', :now, :approved_by, :approved_at, :batch, :rows, :now, :now)
            """
        ),
        {
            "id": str(row_id),
            "org": str(ORG_1),
            "bank": _BANK_ID,
            "status": withdrawal_status,
            "now": _NOW,
            "approved_by": "Checker Officer" if approved else None,
            "approved_at": _NOW if approved else None,
            "batch": str(uuid4()) if withdrawal_status == "applied" else None,
            "rows": 42 if approved else 0,
        },
    )


def _insert_declaration(connection: Connection, row_id: UUID, *, declaration_status: str) -> None:
    approved = declaration_status == "approved"
    connection.execute(
        text(
            """
            INSERT INTO system_of_record_declarations
              (id, organization_id, bank_id, position_type, source_system, effective_from,
               source_citation, rationale, confirmation_status, status, proposed_by,
               proposed_at, approved_by, approved_at, created_at, updated_at)
            VALUES
              (:id, :org, :bank, 'LOAN', 'T24', DATE '2026-01-01',
               'Board minute 2026-01, item 4', 'Core banking is the book of record',
               'confirmed', :status, 'Maker Officer', :now,
               :approved_by, :approved_at, :now, :now)
            """
        ),
        {
            "id": str(row_id),
            "org": str(ORG_1),
            "bank": _BANK_ID,
            "status": declaration_status,
            "now": _NOW,
            "approved_by": "Checker Officer" if approved else None,
            "approved_at": _NOW if approved else None,
        },
    )


def _approved_parameter_id(connection: Connection) -> UUID:
    """One of the seeded, approved control-plane generations."""
    param_id = connection.scalar(
        text(
            "SELECT id FROM regulatory_parameter WHERE status = 'approved' "
            "ORDER BY param_code, scope_type, scope_key LIMIT 1"
        )
    )
    assert param_id is not None, "the seed migration inserted no approved generation"
    return param_id


# -- structure ------------------------------------------------------------


@postgres_only
def test_every_sealed_table_carries_its_trigger_and_cannot_be_truncated(
    governance_schema: MigratedPostgresSchema,
) -> None:
    """The rule the migration declares is the rule the database installed — and
    TRUNCATE, which bypasses row triggers entirely, is revoked alongside it."""
    expected = {rule.trigger for rule in GOVERNANCE_MIGRATION.SEALED_TABLES}
    with governance_schema.app_engine.connect() as conn:
        installed = set(
            conn.scalars(
                text(
                    """
                    SELECT t.tgname
                    FROM pg_trigger t
                    JOIN pg_class c ON c.oid = t.tgrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE NOT t.tgisinternal
                      AND n.nspname = :schema_name
                      AND t.tgname = ANY(:names)
                    """
                ),
                {"schema_name": governance_schema.schema_name, "names": sorted(expected)},
            )
        )
        assert installed == expected

        # A table's OWNER keeps TRUNCATE by ownership, which REVOKE cannot remove
        # (the same limitation 202607250027 lives with); the revoke protects every
        # role that holds it by GRANT. Assert the grant is gone for non-owners.
        leaked = [
            rule.table
            for rule in GOVERNANCE_MIGRATION.SEALED_TABLES
            if conn.scalar(
                text(
                    "SELECT pg_get_userbyid(relowner) <> current_user "
                    "FROM pg_class WHERE oid = to_regclass(:table)"
                ),
                {"table": rule.table},
            )
            and conn.scalar(
                text("SELECT has_table_privilege(current_user, :table, 'TRUNCATE')"),
                {"table": rule.table},
            )
        ]
        assert not leaked, f"TRUNCATE is still granted on {leaked}"


# -- regulatory_parameter -------------------------------------------------


@postgres_only
def test_an_approved_regulatory_parameter_is_sealed_against_in_place_edits(
    connection: Connection,
) -> None:
    """The finding's second example: a superseded parameter's value edited after
    the runs that consumed it were sealed."""
    param_id = _approved_parameter_id(connection)
    _refused(
        connection,
        "UPDATE regulatory_parameter SET value_numeric = value_numeric + 1, "
        "updated_at = now() WHERE id = :id",
        {"id": str(param_id)},
        "governed and sealed",
    )
    _refused(
        connection,
        "UPDATE regulatory_parameter SET source_citation = 'Rewritten', "
        "updated_at = now() WHERE id = :id",
        {"id": str(param_id)},
        "governed and sealed",
    )
    _refused(
        connection,
        "UPDATE regulatory_parameter SET status = 'draft', updated_at = now() WHERE id = :id",
        {"id": str(param_id)},
        "may not move from approved",
    )


@postgres_only
def test_the_parameter_control_plane_still_approves_and_supersedes(
    connection: Connection,
) -> None:
    """``operator/services/regulatory_parameters.approve`` writes the approval
    columns on a draft and closes the prior generation's ``effective_to``. Both
    must survive the seal."""
    draft_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO regulatory_parameter
              (id, scope_type, scope_key, param_code, jurisdiction_code, value_numeric,
               unit, source_citation, confirmation_status, effective_from, status,
               proposed_by, created_at, updated_at)
            VALUES
              (:id, 'institution_class', 'bank', 'seal_probe_pct', 'GH', 10,
               'pct', 'Probe', 'confirmed', DATE '2026-09-01', 'draft',
               'Maker Officer', now(), now())
            """
        ),
        {"id": str(draft_id)},
    )
    connection.execute(
        text(
            "UPDATE regulatory_parameter SET status = 'approved', "
            "approved_by = 'Checker Officer', approved_at = now(), "
            "change_rationale = 'BoG notice', updated_at = now() WHERE id = :id"
        ),
        {"id": str(draft_id)},
    )
    assert (
        connection.scalar(
            text("SELECT status FROM regulatory_parameter WHERE id = :id"), {"id": str(draft_id)}
        )
        == "approved"
    )

    prior_id = _approved_parameter_id(connection)
    connection.execute(
        text(
            "UPDATE regulatory_parameter SET effective_to = DATE '2027-01-01', "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": str(prior_id)},
    )
    assert connection.scalar(
        text("SELECT effective_to FROM regulatory_parameter WHERE id = :id"),
        {"id": str(prior_id)},
    ) == date(2027, 1, 1)


# -- reconciliation_exceptions -------------------------------------------


@postgres_only
def test_a_granted_reconciliation_ceiling_cannot_be_widened_in_place(
    connection: Connection,
) -> None:
    """The finding's first example, verbatim: the ceiling and the window are
    frozen, and revocation is write-once."""
    row_id = uuid4()
    _insert_exception(connection, row_id)
    _refused(
        connection,
        "UPDATE reconciliation_exceptions SET max_gap_fraction = 0.5, "
        "updated_at = now() WHERE id = :id",
        {"id": str(row_id)},
        "governed and sealed",
    )
    _refused(
        connection,
        "UPDATE reconciliation_exceptions SET effective_to = DATE '2030-01-01', "
        "updated_at = now() WHERE id = :id",
        {"id": str(row_id)},
        "governed and sealed",
    )

    connection.execute(
        text(
            "UPDATE reconciliation_exceptions SET revoked_at = now(), "
            "revoked_by = 'Second Officer', updated_at = now() WHERE id = :id"
        ),
        {"id": str(row_id)},
    )
    _refused(
        connection,
        "UPDATE reconciliation_exceptions SET revoked_at = NULL, revoked_by = NULL, "
        "updated_at = now() WHERE id = :id",
        {"id": str(row_id)},
        "write-once",
    )


# -- canonical_withdrawals ------------------------------------------------


@postgres_only
def test_an_applied_withdrawal_keeps_its_scope_and_evidence(connection: Connection) -> None:
    row_id = uuid4()
    _insert_withdrawal(connection, row_id, withdrawal_status="applied")
    for column, value in (
        ("reason", "'A different reason'"),
        ("as_of_date", "DATE '2026-03-31'"),
        ("rows_withdrawn", "0"),
        ("approved_by", "'Someone Else'"),
    ):
        _refused(
            connection,
            f"UPDATE canonical_withdrawals SET {column} = {value}, "  # noqa: S608
            "updated_at = now() WHERE id = :id",
            {"id": str(row_id)},
            "governed and sealed",
        )


@postgres_only
def test_the_withdrawal_lifecycle_still_runs_forward_and_never_back(
    connection: Connection,
) -> None:
    """``canonical_withdrawal.apply`` then ``reverse`` — the product path — and
    then the reversal cannot be undone or rewritten."""
    row_id = uuid4()
    _insert_withdrawal(connection, row_id, withdrawal_status="pending")
    connection.execute(
        text(
            "UPDATE canonical_withdrawals SET status = 'applied', "
            "approved_by = 'Checker Officer', approved_at = now(), "
            "withdrawal_batch_id = :batch, rows_withdrawn = 42, updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": str(row_id), "batch": str(uuid4())},
    )
    reversal_batch = uuid4()
    connection.execute(
        text(
            "UPDATE canonical_withdrawals SET status = 'reversed', reversed_at = now(), "
            "reversed_by = 'Checker Officer', reversal_reason = 'Withdrawn in error', "
            "reversal_batch_id = :batch, rows_restored = 42, updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": str(row_id), "batch": str(reversal_batch)},
    )
    _refused(
        connection,
        "UPDATE canonical_withdrawals SET status = 'applied', updated_at = now() WHERE id = :id",
        {"id": str(row_id)},
        "may not move from reversed",
    )
    _refused(
        connection,
        "UPDATE canonical_withdrawals SET reversal_reason = 'Something else', "
        "updated_at = now() WHERE id = :id",
        {"id": str(row_id)},
        "write-once",
    )


# -- system_of_record_declarations ---------------------------------------


@postgres_only
def test_an_approved_declaration_is_sealed_but_still_revocable(
    connection: Connection,
) -> None:
    row_id = uuid4()
    _insert_declaration(connection, row_id, declaration_status="approved")
    _refused(
        connection,
        "UPDATE system_of_record_declarations SET source_system = 'FINACLE', "
        "updated_at = now() WHERE id = :id",
        {"id": str(row_id)},
        "governed and sealed",
    )
    connection.execute(
        text(
            "UPDATE system_of_record_declarations SET effective_to = DATE '2026-12-31', "
            "revoked_at = now(), revoked_by = 'Checker Officer', "
            "revocation_reason = 'Core migration completed', updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": str(row_id)},
    )
    assert (
        connection.scalar(
            text("SELECT revoked_by FROM system_of_record_declarations WHERE id = :id"),
            {"id": str(row_id)},
        )
        == "Checker Officer"
    )
    _refused(
        connection,
        "UPDATE system_of_record_declarations SET revoked_by = 'Someone Else', "
        "updated_at = now() WHERE id = :id",
        {"id": str(row_id)},
        "write-once",
    )
