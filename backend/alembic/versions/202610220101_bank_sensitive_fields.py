"""Add capacity and bank-scoped blind indexes for sensitive operational fields.

Revision ID: 202610220101
Revises: 202610140100
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202610220101"
down_revision = "202610140100"
branch_labels = None
depends_on = None

COUNTERPARTIES = "canonical_counterparties"
PARTIES = "related_parties"
CURRENT = "superseded_by IS NULL AND withdrawn_at IS NULL"
WIDENED = (
    (COUNTERPARTIES, "name", 255),
    (COUNTERPARTIES, "source_reference", 255),
    (COUNTERPARTIES, "group_reference", 255),
    (PARTIES, "full_name", 200),
    ("related_party_roles", "term_of_appointment", 80),
    ("related_party_roles", "icag_registration", 60),
)

ALERT_REASONS = (
    "authorization_revoked",
    "no_governed_limit",
    "no_figure",
    "unknown_member",
    "query_refused",
    "data_scope_unsupported",
)
IDENTITY_REASON = "bank_key_identity_bi_unavailable"


def _alert_reasons(reasons: tuple[str, ...]) -> None:
    op.drop_constraint("ck_bi_alert_events_reason", "bi_alert_events", type_="check")
    values = ", ".join(f"'{reason}'" for reason in reasons)
    op.create_check_constraint(
        "ck_bi_alert_events_reason", "bi_alert_events", f"reason IS NULL OR reason IN ({values})"
    )


def _current_index(column: str) -> None:
    op.create_index(
        "uq_canonical_counterparties_current",
        COUNTERPARTIES,
        ["organization_id", "bank_id", "source_system", column, "as_of_date"],
        unique=True,
        postgresql_where=sa.text(CURRENT),
        sqlite_where=sa.text(CURRENT),
    )


def upgrade() -> None:
    # Field reads and writes resolve bank key configuration in the runtime
    # session. Key registration and rotation remain control-plane mutations.
    op.execute("GRANT SELECT ON bank_encryption_keys TO aqs_api, aqs_worker")
    _alert_reasons((*ALERT_REASONS, IDENTITY_REASON))
    for table, column, width in WIDENED:
        op.alter_column(table, column, existing_type=sa.String(width), type_=sa.Text())
    for column in (
        "source_reference_blind_index",
        "name_blind_index",
        "concentration_group_blind_index",
    ):
        op.add_column(COUNTERPARTIES, sa.Column(column, sa.String(255), nullable=True))
    op.add_column(
        COUNTERPARTIES,
        sa.Column(
            "external_identifiers_blind_indexes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(PARTIES, sa.Column("full_name_blind_index", sa.String(255), nullable=True))
    op.drop_index("uq_canonical_counterparties_current", table_name=COUNTERPARTIES)
    _current_index("source_reference_blind_index")
    op.create_index(
        "ix_counterparties_name_blind",
        COUNTERPARTIES,
        ["organization_id", "bank_id", "name_blind_index"],
    )
    op.create_index(
        "ix_related_parties_name_blind",
        PARTIES,
        ["organization_id", "bank_id", "full_name_blind_index"],
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT ON bank_encryption_keys FROM aqs_api, aqs_worker")
    _alert_reasons(ALERT_REASONS)
    op.drop_index("ix_related_parties_name_blind", table_name=PARTIES)
    op.drop_index("ix_counterparties_name_blind", table_name=COUNTERPARTIES)
    op.drop_index("uq_canonical_counterparties_current", table_name=COUNTERPARTIES)
    _current_index("source_reference")
    op.drop_column(PARTIES, "full_name_blind_index")
    for column in (
        "source_reference_blind_index",
        "name_blind_index",
        "concentration_group_blind_index",
        "external_identifiers_blind_indexes",
    ):
        op.drop_column(COUNTERPARTIES, column)
    for table, column, width in reversed(WIDENED):
        op.alter_column(table, column, existing_type=sa.Text(), type_=sa.String(width))
