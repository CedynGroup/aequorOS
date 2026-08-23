"""Durable login lockout for the staff control plane (``operator_users``).

The operator plane guards cross-tenant **BYPASSRLS** access — the
highest-privilege surface in the platform — and until now its login throttle
was an in-process dict keyed on ``(email, client-ip)``
(``app/operator/services/operator_auth.py``): 5 failures per pair, 5 minutes.
That is the exact control the TENANT plane rejected as inadequate. The tenant
design note is explicit about why (``app/services/auth_throttle.py``): a
per-process counter yields ``max_failed × workers × replicas`` attempts and
resets on every deploy, and an IP dimension hands the attacker a fresh budget
for every source address they rotate through. Success on the operator endpoint
yields a ``super_admin`` principal on a cross-tenant session over every bank,
so the higher-privilege plane was holding the weaker control (audit finding
D-25).

This migration gives ``operator_users`` the SAME two durable columns tenant
``users`` has carried since ``202605250001`` — ``failed_login_attempts`` and
``locked_until`` — so the staff login path can run on the shared
``auth_throttle`` primitive rather than a second, weaker implementation.
Existing rows start at zero attempts and no lock, which is the state a
never-failed account would have had anyway.

No calculation input, hash or output is touched: ``operator_users`` is a staff
identity table with no bearing on any regulatory computation.

Revision ID: 202608230041
Revises: 202608230036
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608230041"
down_revision = "202608230036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operator_users",
        sa.Column(
            "failed_login_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "operator_users",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("operator_users", "locked_until")
    op.drop_column("operator_users", "failed_login_attempts")
