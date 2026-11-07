"""Persist immutable Behavioral model applications for independent review.

Revision ID: 202611070111
Revises: 202611040110
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202611070111"
down_revision = "202611040110"
branch_labels = None
depends_on = None

_TABLE = "behavioral_model_proposals"
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("model", sa.String(40), nullable=False),
        sa.Column("model_version", sa.String(120), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("proposed_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingestion_batch_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "model IN ('nmd-duration', 'prepayment', 'deposit-stability')",
            name="ck_behavioral_model_proposals_model",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted')",
            name="ck_behavioral_model_proposals_status",
        ),
        sa.CheckConstraint(
            "length(trim(model_version)) > 0",
            name="ck_behavioral_model_proposals_version_present",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL "
            "AND ingestion_batch_id IS NULL) OR "
            "(status = 'accepted' AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_by_user_id <> proposed_by_user_id AND reviewed_at IS NOT NULL "
            "AND ingestion_batch_id IS NOT NULL)",
            name="ck_behavioral_model_proposals_review",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name="fk_behavioral_model_proposals_bank",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_behavioral_model_proposals_proposer",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_behavioral_model_proposals_reviewer",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id", "organization_id", "bank_id"],
            [
                "ingestion_batches.id",
                "ingestion_batches.organization_id",
                "ingestion_batches.bank_id",
            ],
            name="fk_behavioral_model_proposals_batch",
        ),
        sa.UniqueConstraint(
            "ingestion_batch_id",
            name="uq_behavioral_model_proposals_ingestion_batch",
        ),
    )
    op.create_index(
        "ix_behavioral_model_proposals_scope_status",
        _TABLE,
        ["organization_id", "bank_id", "status"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
            FOR ALL
            USING ((organization_id)::text = {_TENANT_ID_EXPR})
            WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
            """
        )
        op.execute("""
            CREATE FUNCTION aqs_behavioral_model_proposal_guard() RETURNS trigger
            LANGUAGE plpgsql AS $guard$
            BEGIN
                IF TG_OP = 'DELETE' OR OLD.status = 'accepted' THEN
                    RAISE EXCEPTION 'Behavioral model proposal history is immutable';
                END IF;
                IF ROW(NEW.id, NEW.organization_id, NEW.bank_id, NEW.model, NEW.model_version,
                       NEW.as_of_date, NEW.values::jsonb, NEW.proposed_by_user_id,
                       NEW.proposed_at, NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.id, OLD.organization_id, OLD.bank_id, OLD.model, OLD.model_version,
                       OLD.as_of_date, OLD.values::jsonb, OLD.proposed_by_user_id,
                       OLD.proposed_at, OLD.created_at) THEN
                    RAISE EXCEPTION 'Behavioral model proposal values and provenance are immutable';
                END IF;
                IF NEW.status = 'accepted' AND
                   (NEW.reviewed_by_user_id IS NULL OR
                    NEW.reviewed_by_user_id = OLD.proposed_by_user_id OR
                    NEW.reviewed_at IS NULL OR NEW.ingestion_batch_id IS NULL) THEN
                    RAISE EXCEPTION
                        'Behavioral model acceptance requires a distinct reviewer and batch';
                END IF;
                RETURN NEW;
            END $guard$
            """)
        op.execute(f"""
            CREATE TRIGGER behavioral_model_proposals_governed_row
            BEFORE UPDATE OR DELETE ON {_TABLE} FOR EACH ROW
            EXECUTE FUNCTION aqs_behavioral_model_proposal_guard()
            """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER behavioral_model_proposals_governed_row ON behavioral_model_proposals"
        )
        op.execute("DROP FUNCTION aqs_behavioral_model_proposal_guard()")
        op.execute(
            "DROP POLICY behavioral_model_proposals_tenant_isolation ON behavioral_model_proposals"
        )
    op.drop_table(_TABLE)
