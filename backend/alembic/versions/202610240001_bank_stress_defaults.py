"""Fill missing bank FX and FTP starting stress calibrations.

Values are pinned from the canonical bank fixture's existing FX/FTP scenarios;
they are internal scenario assumptions, not regulatory requirements. Existing
keys at any effective generation remain authoritative. Parameter evidence is
retained on downgrade, matching the register's retention protection.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610240001"
down_revision = "202610120100"
branch_labels = None
depends_on = None

TABLE = "param_stress_shock"
ACTOR = "platform_seed:202610240001"
DEFAULTS = (
    ("fx", "mild_depreciation", "ghs_usd_shock_pct", "10"),
    ("fx", "severe_depreciation", "ghs_usd_shock_pct", "20"),
    ("fx", "cedi_crisis", "ghs_usd_shock_pct", "30"),
    ("fx", "cedi_crisis", "correlation_uplift", "0.2"),
    ("fx", "cedi_crisis", "crisis_window_start", "60"),
    ("fx", "cedi_crisis", "crisis_window_end", "110"),
    ("ftp", "rates_up_200", "curve_shift_bp", "200"),
    ("ftp", "funding_stress", "funding_spread_add_bps", "100"),
)


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text(f"LOCK TABLE banks, {TABLE} IN SHARE ROW EXCLUSIVE MODE"))
    with force_rls_suspended(connection, "banks", TABLE):
        scopes = cast(
            Sequence[tuple[str, str]],
            connection.execute(
                sa.text(
                    "SELECT DISTINCT b.organization_id, b.jurisdiction_code FROM banks b "
                    "JOIN institution_types t ON t.type_code = b.institution_type "
                    "WHERE t.institution_class = 'bank'"
                )
            )
            .tuples()
            .all(),
        )
        table = sa.table(
            TABLE,
            sa.column("id", sa.Uuid()),
            sa.column("organization_id", sa.String()),
            sa.column("jurisdiction_code", sa.String()),
            sa.column("module", sa.String()),
            sa.column("scenario_code", sa.String()),
            sa.column("shock_key", sa.String()),
            sa.column("shock_value", sa.Numeric()),
            sa.column("effective_from", sa.Date()),
            sa.column("approved_by", sa.String()),
            sa.column("approval_timestamp", sa.DateTime(timezone=True)),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        now = datetime.now(UTC)
        for organization_id, jurisdiction_code in scopes:
            existing = set(
                connection.execute(
                    sa.select(
                        table.c.module,
                        table.c.scenario_code,
                        table.c.shock_key,
                    ).where(
                        table.c.organization_id == organization_id,
                        table.c.jurisdiction_code == jurisdiction_code,
                    )
                ).all()
            )
            rows = [
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "jurisdiction_code": jurisdiction_code,
                    "module": module,
                    "scenario_code": scenario,
                    "shock_key": key,
                    "shock_value": Decimal(value),
                    "effective_from": date(2000, 1, 1),
                    "approved_by": ACTOR,
                    "approval_timestamp": now,
                    "created_at": now,
                    "updated_at": now,
                }
                for module, scenario, key, value in DEFAULTS
                if (module, scenario, key) not in existing
            ]
            if rows:
                connection.execute(table.insert(), rows)


def downgrade() -> None:
    """Retain approved parameter evidence; this revision changes no schema."""
