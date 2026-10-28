"""Remember regulatory status observations for commencement notifications."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202610230001"
down_revision = "202610260001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "regulatory_reporting_settings",
        sa.Column(
            "instrument_observations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("regulatory_reporting_settings", "instrument_observations")
