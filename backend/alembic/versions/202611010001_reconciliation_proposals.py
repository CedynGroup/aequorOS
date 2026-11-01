"""Persist reconciliation proposals before separate authenticated approval.

Revision ID: 202611010001
Revises: 202610240103
"""

import sqlalchemy as sa

from alembic import op

revision = "202611010001"
down_revision = "202610240103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_exceptions",
        sa.Column("status", sa.String(16), nullable=False, server_default="approved"),
    )
    op.alter_column("reconciliation_exceptions", "approved_by", nullable=True)
    op.alter_column("reconciliation_exceptions", "approval_timestamp", nullable=True)
    op.create_check_constraint(
        "ck_reconciliation_exceptions_status",
        "reconciliation_exceptions",
        "status IN ('pending', 'approved')",
    )
    op.create_check_constraint(
        "ck_reconciliation_exceptions_approval",
        "reconciliation_exceptions",
        "(status = 'pending' AND requested_by IS NOT NULL AND approved_by IS NULL "
        "AND approved_by_user_id IS NULL AND approval_timestamp IS NULL) OR "
        "(status = 'approved' AND approved_by IS NOT NULL AND approval_timestamp IS NOT NULL)",
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER reconciliation_exceptions_governed_row ON reconciliation_exceptions"
        )
        op.execute("""
            CREATE FUNCTION aqs_reconciliation_proposal_guard() RETURNS trigger
            LANGUAGE plpgsql AS $guard$
            BEGIN
                IF (to_jsonb(OLD) - ARRAY['status', 'approved_by', 'approved_by_user_id',
                    'approval_timestamp', 'revoked_at', 'revoked_by', 'updated_at'])
                    IS DISTINCT FROM
                    (to_jsonb(NEW) - ARRAY['status', 'approved_by', 'approved_by_user_id',
                    'approval_timestamp', 'revoked_at', 'revoked_by', 'updated_at']) THEN
                    RAISE EXCEPTION 'Reconciliation proposal is governed and sealed';
                END IF;
                IF OLD.status = 'approved' OR OLD.revoked_at IS NOT NULL THEN
                    IF ROW(OLD.status, OLD.approved_by,
                           OLD.approved_by_user_id, OLD.approval_timestamp)
                        IS DISTINCT FROM
                        ROW(NEW.status, NEW.approved_by,
                            NEW.approved_by_user_id, NEW.approval_timestamp) THEN
                        RAISE EXCEPTION 'Reconciliation approval evidence is immutable';
                    END IF;
                ELSIF NEW.status = 'approved' AND
                    (NEW.approved_by_user_id IS NULL OR
                     NEW.approved_by_user_id = OLD.requested_by) THEN
                    RAISE EXCEPTION 'A different authenticated checker is required';
                END IF;
                IF OLD.revoked_at IS NOT NULL AND
                    ROW(OLD.revoked_at, OLD.revoked_by) IS DISTINCT FROM
                    ROW(NEW.revoked_at, NEW.revoked_by) THEN
                    RAISE EXCEPTION 'Reconciliation revocation is write-once';
                END IF;
                RETURN NEW;
            END $guard$
        """)
        op.execute("""
            CREATE TRIGGER reconciliation_exceptions_governed_row
            BEFORE UPDATE ON reconciliation_exceptions FOR EACH ROW
            EXECUTE FUNCTION aqs_reconciliation_proposal_guard()
        """)


def downgrade() -> None:
    # Pending requests cannot be represented by the old authoritative-only schema.
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM reconciliation_exceptions WHERE status = 'pending' LIMIT 1")
        )
        .first()
        is not None
    ):
        raise RuntimeError("Pending reconciliation proposals must be retained; downgrade refused.")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER reconciliation_exceptions_governed_row ON reconciliation_exceptions"
        )
        op.execute("DROP FUNCTION aqs_reconciliation_proposal_guard()")
        op.execute("""
            CREATE TRIGGER reconciliation_exceptions_governed_row
            BEFORE UPDATE ON reconciliation_exceptions FOR EACH ROW
            EXECUTE FUNCTION aequoros_governed_row_guard(
                'revoked_at,revoked_by,updated_at', 'revoked_at,revoked_by', '', '', '')
        """)
    op.drop_constraint("ck_reconciliation_exceptions_approval", "reconciliation_exceptions")
    op.drop_constraint("ck_reconciliation_exceptions_status", "reconciliation_exceptions")
    op.alter_column("reconciliation_exceptions", "approved_by", nullable=False)
    op.alter_column("reconciliation_exceptions", "approval_timestamp", nullable=False)
    op.drop_column("reconciliation_exceptions", "status")
