"""Seed the Notice BG/GOV/SEC/2025/23 NPL parameters into the control plane.

The credit module colours the NPL ratio against a governed ceiling, never a
literal: ``npl_limit_pct`` (10%, compliance by end-December 2026) and
``npl_dividend_restriction_pct`` (15%, immediate restrictions) per BoG Notice
BG/GOV/SEC/2025/23, plus the restructure cure counts of its paragraph 12
(6 consecutive full repayments; 4 for semi-annual schedules) consumed from
credit PR-5 onward. The notice binds banks and SDIs alike, so both classes
get the same rows.

Values come from ``app.services.regulatory_parameters.SEED_PARAMETERS`` — the
single catalogue the resolver and the hermetic seed also read. Rows already
present are skipped (idempotent against a database seeded either way).
``regulatory_parameter`` is a global, non-RLS reference table.

Revision ID: 202609010047
Revises: 202609010046
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4

revision = "202609010047"
down_revision = "202609010046"
branch_labels = None
depends_on = None

TABLE = "regulatory_parameter"
PARAM_CODES = (
    "npl_limit_pct",
    "npl_dividend_restriction_pct",
    "restructure_cure_payments",
    "restructure_cure_payments_semi_annual",
)


def _seed_table() -> sa.TableClause:
    return sa.table(
        TABLE,
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("scope_type", sa.String),
        sa.column("scope_key", sa.String),
        sa.column("param_code", sa.String),
        sa.column("jurisdiction_code", sa.String),
        sa.column("value_numeric", sa.Numeric),
        sa.column("value_json", sa.JSON),
        sa.column("unit", sa.String),
        sa.column("source_citation", sa.String),
        sa.column("confirmation_status", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("status", sa.String),
        sa.column("proposed_by", sa.String),
        sa.column("approved_by", sa.String),
        sa.column("approved_at", sa.DateTime(timezone=True)),
        sa.column("change_rationale", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    from app.services.regulatory_parameters import seed_rows  # noqa: PLC0415

    bind = op.get_bind()
    codes_sql = ", ".join(f"'{code}'" for code in PARAM_CODES)
    existing = {
        (row[0], row[1], row[2], row[3])
        for row in bind.execute(
            sa.text(
                "SELECT scope_type, scope_key, param_code, jurisdiction_code "
                f"FROM {TABLE} WHERE param_code IN ({codes_sql})"
            )
        )
    }
    now = datetime.now(UTC)
    rows = [
        {
            **row,
            "id": new_uuid4(),
            "approved_at": now,
            "change_rationale": None,
            "created_at": now,
            "updated_at": now,
        }
        for row in seed_rows()
        if row["param_code"] in PARAM_CODES
        and (row["scope_type"], row["scope_key"], row["param_code"], row["jurisdiction_code"])
        not in existing
    ]
    if rows:
        op.bulk_insert(_seed_table(), rows)


def downgrade() -> None:
    codes_sql = ", ".join(f"'{code}'" for code in PARAM_CODES)
    op.execute(sa.text(f"DELETE FROM {TABLE} WHERE param_code IN ({codes_sql})"))
