"""Persist typed source locators and job enqueue provenance (#186).

Add-only: nullable locator columns on notifications and jobs, plus enqueue
provenance on jobs, with CHECK-pinned vocabularies matching
``app/core/authorization.py``. No data migration and no backfill: existing
rows keep all-NULL locators and stay hidden at every tenant read path
(mission D9 — a missing locator is never inferred). Producers start setting
the locator in a later commit, so this migration changes no behavior.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202611070114"
down_revision = "202611070113"
branch_labels = None
depends_on = None

# Literal vocabularies (not enum imports) — migrations are frozen snapshots of
# the vocabulary at the time they run, exactly like the binding CHECKs.
_INSTITUTION_SCOPES = ("organization", "institution")
_MODULES = (
    "liq",
    "cap",
    "credit",
    "irrbb",
    "fx",
    "ftp",
    "fcst",
    "beh",
    "data",
    "reg",
    "risk",
    "markets",
    "institution",
    "account",
    "audit",
)
_SENSITIVITIES = ("published", "aggregated", "confidential", "restricted")


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


def _locator_checks(table: str) -> list[tuple[str, str]]:
    return [
        (
            f"ck_{table}_institution_scope",
            f"institution_scope IN ({_values(_INSTITUTION_SCOPES)})",
        ),
        (f"ck_{table}_module", f"module IN ({_values(_MODULES)})"),
        (f"ck_{table}_sensitivity", f"sensitivity IN ({_values(_SENSITIVITIES)})"),
    ]


def upgrade() -> None:
    # Notifications: the read filter queries org + recipient + read_at with the
    # locator dimensions, so one covering index serves list and unread-count.
    for column, width in (
        ("institution_scope", 16),
        ("bank_id", 16),
        ("module", 32),
        ("sensitivity", 16),
    ):
        op.add_column("notifications", sa.Column(column, sa.String(length=width), nullable=True))
    for constraint in _locator_checks("notifications"):
        op.create_check_constraint(constraint[0], "notifications", constraint[1])
    op.create_index(
        "ix_notifications_org_recipient_read_locator",
        "notifications",
        ["organization_id", "recipient_user_id", "read_at", "module", "sensitivity", "bank_id"],
    )

    # Jobs: bank_id already exists and is subsumed by the locator; add the
    # remaining locator dimensions plus enqueue provenance.
    for column, width in (
        ("institution_scope", 16),
        ("module", 32),
        ("sensitivity", 16),
        ("enqueued_by_principal", 160),
    ):
        op.add_column("jobs", sa.Column(column, sa.String(length=width), nullable=True))
    for column in ("enqueued_action", "enqueue_decision_reference"):
        op.add_column("jobs", sa.Column(column, sa.Text(), nullable=True))
    for constraint in _locator_checks("jobs"):
        op.create_check_constraint(constraint[0], "jobs", constraint[1])
    op.create_index(
        "ix_jobs_org_status_run_locator",
        "jobs",
        ["organization_id", "status", "run_after", "module", "sensitivity", "bank_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_org_status_run_locator", table_name="jobs")
    for constraint in _locator_checks("jobs"):
        op.drop_constraint(constraint[0], "jobs", type_="check")
    for column in (
        "institution_scope",
        "module",
        "sensitivity",
        "enqueued_by_principal",
        "enqueued_action",
        "enqueue_decision_reference",
    ):
        op.drop_column("jobs", column)

    op.drop_index("ix_notifications_org_recipient_read_locator", table_name="notifications")
    for constraint in _locator_checks("notifications"):
        op.drop_constraint(constraint[0], "notifications", type_="check")
    for column in ("institution_scope", "bank_id", "module", "sensitivity"):
        op.drop_column("notifications", column)
