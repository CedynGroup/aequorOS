"""Dual Excel export modes: admit the 'xlsx_working' artifact kind

Revision ID: 202608160015
Revises: 202608160014

Every official BoG BSD return now has three export artifacts from one sealed
run: the submission PDF (values only — the BoG filing format), the OFFICIAL
Excel (kind ``xlsx``: values-only, sheets protected — the governance twin of
the PDF) and the ALM/Finance WORKING Excel (kind ``xlsx_working``: same official
layout with the template's live formulas so reviewers can challenge inputs;
never filed, never signed). This widens ``ck_regulatory_package_artifacts_kind``
to admit the new kind and ``regulatory_artifact_versions.kind`` from
VARCHAR(8) to VARCHAR(16) so the append-only version log can record it.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608160015"
down_revision = "202608160014"
branch_labels = None
depends_on = None

KINDS_BEFORE = "'xlsx', 'csv', 'pdf'"
KINDS_AFTER = "'xlsx', 'csv', 'pdf', 'xlsx_working'"


def upgrade() -> None:
    with op.batch_alter_table("regulatory_package_artifacts") as batch:
        batch.drop_constraint("ck_regulatory_package_artifacts_kind", type_="check")
        batch.create_check_constraint(
            "ck_regulatory_package_artifacts_kind", f"kind IN ({KINDS_AFTER})"
        )
    with op.batch_alter_table("regulatory_artifact_versions") as batch:
        batch.alter_column(
            "kind",
            existing_type=sa.String(length=8),
            type_=sa.String(length=16),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute("DELETE FROM regulatory_artifact_versions WHERE kind = 'xlsx_working'")
    op.execute("DELETE FROM regulatory_package_artifacts WHERE kind = 'xlsx_working'")
    with op.batch_alter_table("regulatory_artifact_versions") as batch:
        batch.alter_column(
            "kind",
            existing_type=sa.String(length=16),
            type_=sa.String(length=8),
            existing_nullable=False,
        )
    with op.batch_alter_table("regulatory_package_artifacts") as batch:
        batch.drop_constraint("ck_regulatory_package_artifacts_kind", type_="check")
        batch.create_check_constraint(
            "ck_regulatory_package_artifacts_kind", f"kind IN ({KINDS_BEFORE})"
        )
