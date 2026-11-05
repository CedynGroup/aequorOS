"""Bind an inspector session to the ONE bank it examines.

Revision ID: 202611030002
Revises: 202611030001

The operator console opens an inspection session against an organization; the
act-as-examiner token it mints is accepted by the tenant API as a read-only
``examiner`` principal. That token proved which TENANT the operator was looking
at, never which INSTITUTION: ``organization_id`` was the only tenant dimension
the session carried, so a session opened for one bank could reach a sibling
bank of the same organization.

``bank_id`` records the institution the session examines. It is NULLABLE because
sessions already open when the column lands cannot be back-filled — several
banks can share one organization, so an organization id names no institution.
A session with no bank binds nothing and reads no filed register: the tenant API
requires the token's bank claim to equal the bank in the path, and a missing
claim is a refusal, not an organization-wide fallback.
"""

import sqlalchemy as sa

from alembic import op

revision = "202611030002"
down_revision = "202611030001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operator_inspector_sessions",
        sa.Column("bank_id", sa.String(length=16), nullable=True),
    )
    op.create_foreign_key(
        "fk_operator_inspector_sessions_bank_id",
        "operator_inspector_sessions",
        "banks",
        ["bank_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_operator_inspector_sessions_bank_id",
        "operator_inspector_sessions",
        type_="foreignkey",
    )
    op.drop_column("operator_inspector_sessions", "bank_id")
