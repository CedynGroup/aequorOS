# pyright: standard
"""documents filed with a return (Excel, CSV, PDF), bound into the signed manifest

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submission_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_submission_documents_submission_id_submissions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_documents")),
        sa.UniqueConstraint(
            "submission_id", "name", name=op.f("uq_submission_documents_submission_id")
        ),
    )
    op.create_index(
        op.f("ix_submission_documents_submission_id"),
        "submission_documents",
        ["submission_id"],
        unique=False,
    )
    if op.get_bind().dialect.name == "postgresql":
        # A filed document is evidence: never changed, never removed.
        op.execute(
            """
            CREATE OR REPLACE FUNCTION srg_document_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'submission_documents is immutable: % refused', TG_OP
                    USING ERRCODE = 'insufficient_privilege';
            END
            $$
            """
        )
        op.execute(
            "CREATE TRIGGER submission_documents_immutable BEFORE UPDATE OR DELETE "
            "ON submission_documents FOR EACH ROW EXECUTE FUNCTION srg_document_immutable()"
        )
        op.execute(
            "CREATE TRIGGER submission_documents_no_truncate BEFORE TRUNCATE "
            "ON submission_documents FOR EACH STATEMENT EXECUTE FUNCTION srg_document_immutable()"
        )
        role = _app_role()
        if role:
            # Evidence: the API files and reads documents, and can do nothing else to them.
            op.execute(f"GRANT SELECT, INSERT ON submission_documents TO {role}")


def _app_role() -> str | None:
    """The API's database role, as 0001 reads it: ``-x app_role=`` or ``GATEWAY_DB_APP_ROLE``."""
    import os
    import re

    from alembic import context

    role = context.get_x_argument(as_dictionary=True).get("app_role") or os.environ.get(
        "GATEWAY_DB_APP_ROLE"
    )
    if role and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("app_role must be a plain lower-case identifier")
    return role or None


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS submission_documents_no_truncate ON submission_documents"
        )
        op.execute("DROP TRIGGER IF EXISTS submission_documents_immutable ON submission_documents")
        op.execute("DROP FUNCTION IF EXISTS srg_document_immutable()")
    op.drop_index(op.f("ix_submission_documents_submission_id"), table_name="submission_documents")
    op.drop_table("submission_documents")
