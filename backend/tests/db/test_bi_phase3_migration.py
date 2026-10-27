"""PostgreSQL proof of BI version immutability and dashboard deletion cascades.

Structural parity across all BI-owned tables lives in
``test_bi_owned_tables_parity.py``. This suite retains the behavioral proof that
versions cannot be rewritten but can be removed by their dashboard's cascade.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.bi.models.content import BI_CONTENT_TABLES, BI_CONTENT_UNALTERABLE_TABLES
from app.bi.models.notifications import BI_NOTIFICATION_TABLES
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


def test_the_table_list_is_not_empty() -> None:
    """A parity suite over an empty list passes while proving nothing."""
    assert len(PHASE3_TABLES) == 8, PHASE3_TABLES
    assert all(name.startswith("bi_") for name in PHASE3_TABLES)


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
            pytest.raises(DBAPIError) as refused,
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
