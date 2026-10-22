"""Add capacity and bank-scoped blind indexes for sensitive operational fields.

Revision ID: 202610220101
Revises: 202610140100

Key access belongs to the explicit per-bank backfill, not schema migration.
Historical audit sources and their chains are deliberately unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.session import force_rls_suspended

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

PROTECTED_TEXT = (
    *((table, column) for table, column, _ in WIDENED),
    ("related_party_roles", "other_responsibilities"),
    ("ingestion_batches", "error_message"),
    ("translation_failures", "error_message"),
    ("regulatory_packages", "notes"),
)
PROTECTED_JSON = (
    (COUNTERPARTIES, "external_identifiers"),
    (COUNTERPARTIES, "attributes"),
    (PARTIES, "contact"),
    ("ingestion_batches", "validation_report"),
    ("ingestion_batches", "etl_report"),
    ("lineage_records", "details"),
    ("translation_failures", "raw_record"),
    ("bi_dashboard_versions", "spec"),
    ("bi_subscriptions", "query"),
    ("bi_alerts", "filters"),
    ("regulatory_packages", "snapshot"),
    ("regulatory_packages", "validation_report"),
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
    # Preserve existing uniqueness before any bank obtains key access for its
    # resumable backfill. Never let FORCE RLS turn this into a partial update.
    with force_rls_suspended(op.get_bind(), COUNTERPARTIES):
        op.execute(
            sa.text(
                "UPDATE canonical_counterparties "
                "SET source_reference_blind_index = source_reference, "
                "concentration_group_blind_index = coalesce(group_reference, name)"
            )
        )
    op.alter_column(COUNTERPARTIES, "source_reference_blind_index", nullable=False)
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
    # Downgrading capacity cannot decrypt data, discard indexes under ciphertext,
    # or produce a database an older binary could misinterpret as plaintext.
    tables = tuple(dict.fromkeys(table for table, _ in (*PROTECTED_TEXT, *PROTECTED_JSON)))
    with force_rls_suspended(op.get_bind(), *tables, "bi_alert_events"):
        for catalogue, predicate in (
            (PROTECTED_TEXT, "{column} LIKE 'aqs-bank-field-v1:%'"),
            (PROTECTED_JSON, "{column}::jsonb ? 'aqs-bank-field-v1'"),
            ((("bi_alert_events", "reason"),), "{column} = 'bank_key_identity_bi_unavailable'"),
        ):
            for table, column in catalogue:
                if op.get_bind().scalar(
                    sa.text(
                        f"SELECT EXISTS (SELECT 1 FROM {table} WHERE "
                        + predicate.format(column=column)
                        + ")"
                    )
                ):
                    raise RuntimeError("Bank-encrypted fields prohibit schema downgrade.")
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
