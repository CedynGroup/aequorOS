"""Copy every shared LIQ generation to its existing bank owners; freeze archives.

No financial data, grants, identities or sessions are created. Source rows are
retained unchanged. Runtime code must cut over to the new tables without a
shared-register fallback. Downgrade is deliberately unavailable after local
divergence: silently collapsing bank-local values would lose approved evidence.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "202611040109"
down_revision = "202611040108"
branch_labels = None
depends_on = None

TABLES = ("bank_liquidity_threshold", "bank_liquidity_haircut")
ARCHIVES = ("param_liquidity_threshold", "param_liquidity_haircut")
COPY_TABLES = ("banks", *ARCHIVES, *TABLES)
COMMON_COLUMNS = (
    "organization_id",
    "jurisdiction_code",
    "effective_from",
    "effective_to",
    "approved_by",
    "approval_timestamp",
)
TAIL_COLUMNS = ("notes", "created_at", "updated_at")
TENANT = "NULLIF(current_setting('app.organization_id', true), '')"


@contextmanager
def force_rls_suspended(connection: Connection, *tables: str) -> Generator[None]:
    """Frozen owner-only migration pattern; RLS remains enabled for other roles."""
    # Only lift FORCE on tables which had it; preserve the preexisting flags.
    # SQLAlchemy cannot infer raw catalog scalar types at this boundary.
    forced = cast(
        list[str],
        connection.execute(
            sa.text(
                "SELECT relname FROM pg_class WHERE relnamespace = current_schema()::regnamespace "
                "AND relname = ANY(:tables) AND relforcerowsecurity ORDER BY relname"
            ),
            {"tables": list(tables)},
        )
        .scalars()
        .all(),
    )
    for table in forced:
        connection.exec_driver_sql(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    # Any error rolls back the DDL together with the entire Alembic transaction.
    yield
    for table in forced:
        connection.exec_driver_sql(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def _revoke(table: str, privileges: str) -> None:
    # Same retention ACL sweep as 202610110095, including owner-restored rights.
    op.execute(f"""
        REVOKE {privileges} ON {table} FROM PUBLIC;
        DO $$ DECLARE r record; BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                     WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper LOOP
                EXECUTE format('REVOKE {privileges} ON {table} FROM %I', r.rolname);
            END LOOP;
        END $$;
    """)


def _columns(table: str) -> tuple[str, ...]:
    specific = (
        ("institution_class", "threshold_code", "threshold_pct")
        if table == TABLES[0]
        else ("asset_class", "haircut_pct")
    )
    return (*COMMON_COLUMNS, *specific, *TAIL_COLUMNS)


def _matched_banks(table: str, archive: str) -> str:
    class_match = " AND p.institution_class = t.institution_class" if table == TABLES[0] else ""
    return (
        f"FROM {archive} p JOIN banks b ON b.organization_id = p.organization_id "
        "AND b.jurisdiction_code = p.jurisdiction_code "
        "JOIN institution_types t ON t.type_code = b.institution_type" + class_match
    )


def _create_table(table: str) -> None:
    specific: tuple[SchemaItem, ...] = (
        (
            sa.Column("institution_class", sa.String(8), nullable=False),
            sa.Column("threshold_code", sa.String(60), nullable=False),
            sa.Column("threshold_pct", sa.Numeric(12, 6), nullable=False),
            sa.CheckConstraint(
                "institution_class IN ('bank', 'sdi')",
                name=f"ck_{table}_institution_class",
            ),
        )
        if table == TABLES[0]
        else (
            sa.Column("asset_class", sa.String(80), nullable=False),
            sa.Column("haircut_pct", sa.Numeric(9, 6), nullable=False),
        )
    )
    key = ("institution_class", "threshold_code") if table == TABLES[0] else ("asset_class",)
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("jurisdiction_code", sa.String(8), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("approved_by", sa.String(120), nullable=False),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=False),
        *specific,
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=f"fk_{table}_organization_id"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name=f"fk_{table}_org_bank",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "jurisdiction_code",
            *key,
            "effective_from",
            name=f"uq_{table}_scope",
        ),
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL "
        f"USING (organization_id = {TENANT}) WITH CHECK (organization_id = {TENANT})"
    )


def upgrade() -> None:
    connection = op.get_bind()
    for table in TABLES:
        _create_table(table)
    # Block concurrent shared-register writes and bank/classification changes
    # throughout the snapshot. The owner handoff follows the migration runbook.
    op.execute(f"LOCK TABLE {', '.join(COPY_TABLES)}, institution_types IN ACCESS EXCLUSIVE MODE")
    with force_rls_suspended(connection, *COPY_TABLES):
        for table, archive in zip(TABLES, ARCHIVES, strict=True):
            columns = _columns(table)
            op.execute(
                f"INSERT INTO {table} (id, bank_id, {', '.join(columns)}) "
                f"SELECT gen_random_uuid(), b.id, {', '.join('p.' + c for c in columns)} "
                + _matched_banks(table, archive)
            )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_sealed BEFORE UPDATE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION aequoros_governed_row_guard("
            "'effective_to,updated_at', '', '', '', '')"
        )
        op.execute(
            f"CREATE TRIGGER {table}_retained BEFORE DELETE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION aequoros_retained_register_guard()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT "
            "EXECUTE FUNCTION aequoros_append_only_guard()"
        )
        _revoke(table, "DELETE, TRUNCATE")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO aqs_api, aqs_worker")
    for archive in ARCHIVES:
        # A statement guard also rejects attempted writes when no rows match.
        op.execute(
            f"CREATE TRIGGER {archive}_archive_read_only BEFORE INSERT OR UPDATE ON {archive} "
            "FOR EACH STATEMENT EXECUTE FUNCTION aequoros_append_only_guard()"
        )
        _revoke(archive, "INSERT, UPDATE")


def _register_diverged(connection: Connection, table: str, archive: str) -> bool:
    # Raw SQL scalar typing is unavailable at this data-query boundary.
    divergent = cast(
        object,
        connection.scalar(
            sa.text(
                "WITH expected AS (SELECT (to_jsonb(p) - 'id') || "
                "jsonb_build_object('bank_id', b.id) AS value "
                + _matched_banks(table, archive)
                + f"), actual AS (SELECT to_jsonb(p) - 'id' AS value FROM {table} p) "
                "SELECT EXISTS ((SELECT value FROM expected EXCEPT ALL "
                "SELECT value FROM actual) UNION ALL (SELECT value FROM actual "
                "EXCEPT ALL SELECT value FROM expected))"
            )
        ),
    )
    if not isinstance(divergent, bool):
        raise RuntimeError("Expected a complete liquidity divergence check")
    return divergent


def downgrade() -> None:
    connection = op.get_bind()
    # The freeze revoked even owner ACLs. Restore only transaction-local lock
    # eligibility first; a refused rollback rolls this grant back as well.
    op.execute(f"GRANT UPDATE ON {', '.join(ARCHIVES)} TO CURRENT_USER")
    op.execute(f"LOCK TABLE {', '.join(COPY_TABLES)}, institution_types IN ACCESS EXCLUSIVE MODE")
    with force_rls_suspended(connection, *COPY_TABLES):
        for table, archive in zip(TABLES, ARCHIVES, strict=True):
            # Compare every stored field except the deliberately fresh UUID.
            # EXCEPT ALL detects both missing and added generations. Changed
            # bank classification/jurisdiction also fails closed rather than
            # guessing how to merge a bank's approved register back.
            if _register_diverged(connection, table, archive):
                raise RuntimeError(
                    f"bank-local liquidity register divergence in {table}; refusing lossy downgrade"
                )
    for archive in ARCHIVES:
        op.execute(f"DROP TRIGGER {archive}_archive_read_only ON {archive}")
        # Restore the historical deployment contract, not blanket custom grants.
        op.execute(f"GRANT INSERT, UPDATE ON {archive} TO CURRENT_USER, aqs_api, aqs_worker")
    for table in reversed(TABLES):
        op.drop_table(table)
