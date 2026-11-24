"""Accept the AequorOS Reporting Gateway as a submission channel; add filing defaults.

Revision ID: 202611210120
Revises: 202611210119

Widens the two channel CHECK constraints to include ``reporting_gateway``, and adds
the bank's shared filing profile and default channel to
``regulatory_reporting_settings``. Adding a CHECK validates existing rows with a
scan that forced row-level security would filter (or refuse under the migration
role's ``row_security=off``), so each is created with FORCE suspended for the
table owner, inside this transaction. No row is written.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202611210120"
down_revision = "202611210119"
branch_labels = None
depends_on = None

_BEFORE = ("orass_api", "orass_sandbox", "email", "manual")
_AFTER = ("orass_api", "orass_sandbox", "reporting_gateway", "email", "manual")
_CONSTRAINTS = (
    ("ck_regulatory_submission_events_channel", "regulatory_submission_events"),
    ("ck_regulatory_channel_configs_channel", "regulatory_channel_configs"),
)


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


def _recreate(channels: tuple[str, ...]) -> None:
    for name, table in _CONSTRAINTS:
        with force_rls_suspended(op.get_bind(), table):
            op.drop_constraint(name, table, type_="check")
            op.create_check_constraint(name, table, f"channel IN ({_values(channels)})")


_SETTINGS = "regulatory_reporting_settings"
_DEFAULT_CHANNEL_CHECK = "ck_regulatory_reporting_settings_default_channel"
_DEFAULT_CHANNELS = ("reporting_gateway", "orass_api", "manual")


def upgrade() -> None:
    _recreate(_AFTER)
    op.add_column(
        _SETTINGS,
        sa.Column("filing_profile", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column(_SETTINGS, sa.Column("default_channel", sa.String(20), nullable=True))
    with force_rls_suspended(op.get_bind(), _SETTINGS):
        op.create_check_constraint(
            _DEFAULT_CHANNEL_CHECK,
            _SETTINGS,
            f"default_channel IS NULL OR default_channel IN ({_values(_DEFAULT_CHANNELS)})",
        )


def downgrade() -> None:
    op.drop_constraint(_DEFAULT_CHANNEL_CHECK, _SETTINGS, type_="check")
    op.drop_column(_SETTINGS, "default_channel")
    op.drop_column(_SETTINGS, "filing_profile")
    # Refuses (the CHECK fails) while any gateway row exists: filed evidence is
    # never discarded to make a downgrade possible.
    _recreate(_BEFORE)
