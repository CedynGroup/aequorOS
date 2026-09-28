"""Postgres parity for the Phase 3 BI tables, which nothing else covers.

``test_bi_foundation_migration.py`` iterates ``app/models/bi.py::BI_TABLES`` and
gives the marts four kinds of coverage no hermetic test can: the migrated
column's TYPE, LENGTH and nullability against the model's, the CHECK constraint
NAMES as sets, the values each migrated CHECK admits, and FORCE row-level
security. Phase 3 added eight tables in two other model modules, so none of them
was covered by any of that.

That is not a theoretical gap. It is exactly how audit finding A8-01 reached a
commit: the model and the migration AGREED on a column four characters too narrow
for the values copied into it, every parity test passed because both sides said
the same wrong thing, and SQLite ignores VARCHAR lengths entirely, so the hermetic
suite could not see it either. The defect was only findable by reading the two
against the columns the data comes FROM.

So this file covers the Phase 3 tables on the same four axes, deriving the table
list from the model modules' own tuples rather than restating it — a ninth table
added to either module is covered the moment it is declared.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect, text

from app.db.base import Base
from app.models.bi_content import BI_CONTENT_TABLES, BI_CONTENT_UNALTERABLE_TABLES
from app.models.bi_notifications import BI_NOTIFICATION_TABLES
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    migrated_postgres_schema,
    postgres_schema_url,
)

__all__ = ["migrated_postgres_schema", "postgres_schema_url"]

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration tests.",
    ),
]

#: Derived, never restated: a table added to either module joins this list by
#: being declared, so it cannot be built without Postgres coverage.
PHASE3_TABLES: tuple[str, ...] = (*BI_CONTENT_TABLES, *BI_NOTIFICATION_TABLES)

_PG = sa.dialects.postgresql.dialect()


def _rendered(type_: sa.types.TypeEngine) -> str:
    return str(type_.compile(dialect=_PG))


def test_the_table_list_is_not_empty() -> None:
    """A parity suite over an empty list passes while proving nothing."""
    assert len(PHASE3_TABLES) == 8, PHASE3_TABLES
    assert all(name.startswith("bi_") for name in PHASE3_TABLES)


def test_every_phase3_table_exists_and_forces_row_level_security(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """RLS ENABLED is not enough: the table owner bypasses it without FORCE."""
    with migrated_postgres_schema.app_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       (SELECT count(*) FROM pg_policies p
                         WHERE p.schemaname = n.nspname AND p.tablename = c.relname
                           AND p.policyname = c.relname || '_tenant_isolation') AS tenant_policy
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relname = ANY(:names)
                """
            ),
            {"schema_name": migrated_postgres_schema.schema_name, "names": list(PHASE3_TABLES)},
        ).all()
    state = {row.relname: row for row in rows}
    assert set(state) == set(PHASE3_TABLES), (
        "a Phase 3 BI table is missing from the migrated schema: "
        f"{sorted(set(PHASE3_TABLES) - set(state))}"
    )
    unprotected = [
        name
        for name, row in state.items()
        if not (row.relrowsecurity and row.relforcerowsecurity and row.tenant_policy == 1)
    ]
    assert not unprotected, (
        "these Phase 3 tables are not ENABLE + FORCE row-level security under the "
        f"standard tenant policy, so one tenant could read another's: {sorted(unprotected)}"
    )


def test_migrated_columns_match_the_model_type_length_and_nullability(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """The A8-01 axis. A model widened without its migration fails only here."""
    inspector = inspect(migrated_postgres_schema.app_engine)
    drift: list[str] = []
    for table_name in PHASE3_TABLES:
        model_table = Base.metadata.tables[table_name]
        migrated = {
            column["name"]: column
            for column in inspector.get_columns(
                table_name, schema=migrated_postgres_schema.schema_name
            )
        }
        for column in model_table.columns:
            actual = migrated.get(column.name)
            if actual is None:
                drift.append(f"{table_name}.{column.name}: missing from the migration")
                continue
            if _rendered(column.type) != _rendered(actual["type"]):
                drift.append(
                    f"{table_name}.{column.name}: model {_rendered(column.type)} "
                    f"!= migrated {_rendered(actual['type'])}"
                )
            if bool(column.nullable) != bool(actual["nullable"]):
                drift.append(
                    f"{table_name}.{column.name}: model "
                    f"{'NULL' if column.nullable else 'NOT NULL'} != migrated "
                    f"{'NULL' if actual['nullable'] else 'NOT NULL'}"
                )
        extra = sorted(set(migrated) - {column.name for column in model_table.columns})
        drift.extend(f"{table_name}.{name}: in the migration only" for name in extra)
    assert not drift, (
        "the Phase 3 BI models and migration 202609270072 disagree about these "
        "columns. The model is what the hermetic suite builds and the migration is "
        "what a real database has, so a difference is a latent Postgres-only "
        "failure — edit BOTH: " + "; ".join(drift)
    )


def test_migrated_check_constraints_are_exactly_the_models(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    inspector = inspect(migrated_postgres_schema.app_engine)
    drift: list[str] = []
    for table_name in PHASE3_TABLES:
        declared = {
            str(constraint.name)
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, sa.CheckConstraint) and constraint.name
        }
        migrated = {
            str(constraint["name"])
            for constraint in inspector.get_check_constraints(
                table_name, schema=migrated_postgres_schema.schema_name
            )
            if constraint.get("name")
        }
        drift.extend(
            f"{table_name}: {name} is in the model only" for name in sorted(declared - migrated)
        )
        drift.extend(
            f"{table_name}: {name} is in the migration only" for name in sorted(migrated - declared)
        )
    assert not drift, (
        "the Phase 3 BI models and their migration declare different CHECK "
        "constraints: " + "; ".join(drift)
    )


def test_migrated_checks_admit_every_value_the_model_admits(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """A value added to a model tuple without a constraint migration fails here.

    It passes the whole hermetic suite and then refuses a real INSERT, which is
    the hazard ``202609200065`` records and the reason every migration in this
    chain pins its CHECK literals rather than importing them.
    """
    with migrated_postgres_schema.app_engine.connect() as connection:
        migrated = {
            key: value
            for key, value in connection.execute(
                text(
                    """
                    SELECT c.conname, pg_get_constraintdef(c.oid)
                    FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = :schema_name AND c.contype = 'c'
                      AND t.relname = ANY(:names)
                    """
                ),
                {"schema_name": migrated_postgres_schema.schema_name, "names": list(PHASE3_TABLES)},
            ).tuples()
        }

    missing: list[str] = []
    for table_name in PHASE3_TABLES:
        for constraint in Base.metadata.tables[table_name].constraints:
            name = getattr(constraint, "name", None)
            sqltext = getattr(constraint, "sqltext", None)
            if not name or sqltext is None:
                continue
            assert name in migrated, f"{table_name}: CHECK {name} is not in the migrated schema"
            literal = str(sqltext)
            definition = migrated[str(name)]
            if " IN (" not in literal:
                continue
            for chunk in literal.split(" IN (")[1:]:
                values = chunk.rsplit(")", 1)[0]
                missing.extend(
                    f"{name}: {value}"
                    for value in (v.strip() for v in values.split(","))
                    if value.startswith("'") and value not in definition
                )
    assert not missing, f"migrated CHECKs reject model values: {missing}"


def test_a_version_cannot_be_rewritten_but_can_cascade_away(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """Audit A9-02. The append-only TIER matters, and this test had it backwards.

    ``202607250027`` established two tiers. The audit log blocks UPDATE **and**
    DELETE, because nothing may ever remove an audit record. The signature,
    identity and artifact-version tables block UPDATE **only**, because DELETE has
    to stay reachable through the parent's ``ON DELETE CASCADE``.

    A dashboard's version history is the second kind, and it shipped with the
    first tier: DELETE revoked from the owning role, so the parent's cascade could
    not fire and **no saved dashboard could ever be deleted**. That breaks the
    spec's "only the owner can edit or delete" completely, and rolls the audit
    event for the deletion back with it.

    This test asserted the defect. It now asserts both halves of the correct tier,
    and the delete half is exercised THROUGH THE CASCADE rather than at the
    statement level, because a statement-level check on an empty table is exactly
    what let the wrong tier pass: `DELETE FROM versions` is refused either way
    once no row matches.
    """
    assert BI_CONTENT_UNALTERABLE_TABLES, "no append-only Phase 3 table is declared"
    engine = migrated_postgres_schema.app_engine

    # Half one: a version can never be REWRITTEN. That is what makes it evidence.
    for table_name in BI_CONTENT_UNALTERABLE_TABLES:
        with (
            engine.connect() as connection,
            pytest.raises(sa.exc.DBAPIError) as refused,
            connection.begin(),
        ):
            connection.execute(text("SET LOCAL app.organization_id = 'OR-DEM00001'"))
            connection.execute(text(f"UPDATE {table_name} SET version = version + 1"))
        message = str(refused.value)
        assert "permission denied" in message or "append-only" in message, message

    # Half two: DELETE is GRANTED, so the parent's cascade can fire. Asserted from
    # the privilege itself, which is the thing the wrong tier took away, and then
    # from a real cascade below.
    with engine.connect() as connection:
        for table_name in BI_CONTENT_UNALTERABLE_TABLES:
            granted = connection.execute(
                text("SELECT has_table_privilege(:t, 'DELETE')"),
                {"t": f"{migrated_postgres_schema.schema_name}.{table_name}"},
            ).scalar()
            assert granted, (
                f"{table_name} has no DELETE privilege, so its parent's ON DELETE "
                "CASCADE cannot fire and the parent row can never be deleted"
            )


def test_deleting_a_dashboard_takes_its_versions_with_it(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """The A9-02 failure END TO END: the cascade must actually run.

    The privilege check above is necessary and not sufficient — it was the missing
    grant that broke this, but what a reader experiences is a delete that raises.
    So this seeds a real dashboard with a real version and deletes the parent.
    """
    engine = migrated_postgres_schema.app_engine
    schema = migrated_postgres_schema.schema_name
    org, bank = "OR-A902TEST", "BK-A902TEST"
    user = "44444444-4444-4444-8444-444444444444"
    dashboard = "55555555-5555-4555-8555-555555555555"

    with engine.begin() as connection:
        connection.execute(text(f"SET LOCAL search_path TO {schema}"))
        connection.execute(
            # `SET LOCAL` takes no bind parameter; `set_config` does, and the
            # third argument makes it transaction-local just the same.
            text("SELECT set_config('app.organization_id', :org, true)"),
            {"org": org},
        )
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, created_at, updated_at) "
                "VALUES (:org, 'A9-02', now(), now())"
            ),
            {"org": org},
        )
        # Named rather than derived: a generic filler put text into the timestamp
        # columns. `institution_type` is a foreign key into the seeded registry,
        # so it has to be a real type code rather than a placeholder.
        connection.execute(
            text(
                "INSERT INTO banks (id, organization_id, name, short_name, currency, "
                "jurisdiction_code, license_type, institution_type, created_at, "
                "updated_at) VALUES (:bank, :org, 'A9-02 Bank', 'A902', 'GHS', 'GH', "
                "'universal', 'universal_bank', now(), now())"
            ),
            {"bank": bank, "org": org},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, organization_id, email, role, is_active, "
                "created_at, updated_at, authorization_version) VALUES "
                "(:id, :org, 'a9-02@example.test', 'viewer', true, now(), now(), 1)"
            ),
            {"id": user, "org": org},
        )
        connection.execute(
            text(
                "INSERT INTO bi_dashboards (id, organization_id, bank_id, owner_user_id, "
                "title, description, visibility, badge, current_version, created_at, "
                "updated_at) VALUES (:id, :org, :bank, :user, 'A9-02', 'd', 'private', "
                "'personal', 1, now(), now())"
            ),
            {"id": dashboard, "org": org, "bank": bank, "user": user},
        )
        connection.execute(
            text(
                "INSERT INTO bi_dashboard_versions (id, organization_id, bank_id, "
                "dashboard_id, version, title, description, spec, spec_digest, "
                "change_note, created_by_user_id, created_at) VALUES "
                "(:vid, :org, :bank, :did, 1, 'A9-02', 'd', '{}', 'x', 'first', :user, now())"
            ),
            {
                "vid": "66666666-6666-4666-8666-666666666666",
                "org": org,
                "bank": bank,
                "did": dashboard,
                "user": user,
            },
        )

    with engine.begin() as connection:
        connection.execute(text(f"SET LOCAL search_path TO {schema}"))
        connection.execute(
            # `SET LOCAL` takes no bind parameter; `set_config` does, and the
            # third argument makes it transaction-local just the same.
            text("SELECT set_config('app.organization_id', :org, true)"),
            {"org": org},
        )
        before = connection.execute(
            text("SELECT count(*) FROM bi_dashboard_versions WHERE dashboard_id = :d"),
            {"d": dashboard},
        ).scalar()
        assert before == 1, "the fixture did not seed a version"
        # This is the statement that RAISED before the tier was corrected.
        connection.execute(text("DELETE FROM bi_dashboards WHERE id = :d"), {"d": dashboard})
        after = connection.execute(
            text("SELECT count(*) FROM bi_dashboard_versions WHERE dashboard_id = :d"),
            {"d": dashboard},
        ).scalar()
        assert after == 0, "the version survived its dashboard, so the cascade did not fire"
