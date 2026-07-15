"""api push source system

Revision ID: 202607170004
Revises: 202607170003
Create Date: 2026-07-17 18:00:00.000000

Widens every ``source_system`` CHECK so programmatic JSON pushes land as a
first-class source system (``API_PUSH``). Seven tables carry the constraint:
``mapping_configs``, ``ingestion_batches``, and the five canonical entity
tables. ``canonical_reference_rows`` has no ``source_system`` column and is
untouched. ``downgrade`` removes API_PUSH rows in FK dependency order —
clearing cross-source references (GL parents, snapshot dimension ids,
supersession pointers) that later batches may have made to API_PUSH rows —
before restoring the narrower constraints.
"""

from alembic import op

revision = "202607170004"
down_revision = "202607170003"
branch_labels = None
depends_on = None

ORIGINAL_SYSTEMS = (
    "'EXCEL_CSV', 'T24', 'FINACLE', 'FLEXCUBE', 'DB_DIRECT', 'SFTP_DROP', 'API_GENERIC', 'MANUAL'"
)
WIDENED_SYSTEMS = (
    "'EXCEL_CSV', 'T24', 'FINACLE', 'FLEXCUBE', 'DB_DIRECT', 'SFTP_DROP', 'API_GENERIC', "
    "'API_PUSH', 'MANUAL'"
)

TABLES = (
    "mapping_configs",
    "ingestion_batches",
    "canonical_gl_accounts",
    "canonical_counterparties",
    "canonical_products",
    "canonical_positions",
    "canonical_position_snapshots",
)

NEW_SYSTEM = "'API_PUSH'"


def _swap_checks(systems: str) -> None:
    for table in TABLES:
        constraint = f"ck_{table}_source_system"
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(constraint, type_="check")
            batch_op.create_check_constraint(constraint, f"source_system IN ({systems})")


def upgrade() -> None:
    _swap_checks(WIDENED_SYSTEMS)


def downgrade() -> None:
    # Snapshots first (they reference positions and the dimension tables),
    # then reference rows and entities of API_PUSH batches, then the batch
    # machinery itself. Cross-source references into API_PUSH rows (a later
    # Excel batch pointing a snapshot at an API_PUSH counterparty, GL
    # hierarchy parents, supersession chains) are nulled so no dangling ids
    # survive the deletes.
    for column, dimension in (
        ("counterparty_id", "canonical_counterparties"),
        ("product_id", "canonical_products"),
        ("gl_account_id", "canonical_gl_accounts"),
    ):
        op.execute(
            f"UPDATE canonical_position_snapshots SET {column} = NULL WHERE {column} IN "
            f"(SELECT id FROM {dimension} WHERE source_system = {NEW_SYSTEM})"
        )
    op.execute(f"DELETE FROM canonical_position_snapshots WHERE source_system = {NEW_SYSTEM}")
    op.execute(f"DELETE FROM canonical_positions WHERE source_system = {NEW_SYSTEM}")
    op.execute(
        "UPDATE canonical_gl_accounts SET parent_account_id = NULL WHERE parent_account_id IN "
        f"(SELECT id FROM canonical_gl_accounts WHERE source_system = {NEW_SYSTEM})"
    )
    for table in (
        "canonical_gl_accounts",
        "canonical_counterparties",
        "canonical_products",
    ):
        op.execute(
            f"UPDATE {table} SET superseded_by = NULL WHERE superseded_by IN "
            f"(SELECT id FROM {table} WHERE source_system = {NEW_SYSTEM})"
        )
        op.execute(f"DELETE FROM {table} WHERE source_system = {NEW_SYSTEM}")
    op.execute(
        "DELETE FROM canonical_reference_rows WHERE ingestion_batch_id IN "
        f"(SELECT id FROM ingestion_batches WHERE source_system = {NEW_SYSTEM})"
    )
    op.execute(
        "DELETE FROM translation_failures WHERE ingestion_batch_id IN "
        f"(SELECT id FROM ingestion_batches WHERE source_system = {NEW_SYSTEM})"
    )
    op.execute(
        "DELETE FROM lineage_records WHERE ingestion_batch_id IN "
        f"(SELECT id FROM ingestion_batches WHERE source_system = {NEW_SYSTEM})"
    )
    op.execute(f"DELETE FROM ingestion_batches WHERE source_system = {NEW_SYSTEM}")
    op.execute(f"DELETE FROM mapping_configs WHERE source_system = {NEW_SYSTEM}")
    _swap_checks(ORIGINAL_SYSTEMS)
