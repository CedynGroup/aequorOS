"""Protect regulatory approval and submission evidence from mutation.

Revision ID: 202608220031
Revises: 202608220030

Approvals and submission events are evidential records. Their models already
describe them as append-only, but before this migration Postgres accepted
updates and deletes. The test fixture resets its hermetic sample bank between
cases, so a transaction-local, explicitly named test-reset GUC permits only
those fixture deletes; it never permits updates and application code never sets
it.
"""

from alembic import op

revision = "202608220031"
down_revision = "202608220030"
branch_labels = None
depends_on = None

EVIDENCE_TABLES = (
    "regulatory_package_approvals",
    "regulatory_submission_events",
)
GUARD_FUNCTION = "aequoros_regulatory_event_append_only_guard"
TEST_RESET_GUC = "app.aequoros_regulatory_event_test_reset"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        CREATE FUNCTION {GUARD_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('{TEST_RESET_GUC}', true) = '1' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION '% is append-only; % is prohibited', TG_TABLE_NAME, TG_OP;
        END;
        $$
        """
    )
    for table in EVIDENCE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in EVIDENCE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {GUARD_FUNCTION}()")