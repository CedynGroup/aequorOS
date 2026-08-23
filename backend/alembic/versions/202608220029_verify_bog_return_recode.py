"""Prove the historical BoG return-code recode actually landed.

Revision ID: 202608220029
Revises: 202608220028

Migration 202608150013 rewrote the legacy ``BSD2``/``BSD3`` return codes to
``CAR-RWA``/``LCR-NSFR``. On an RLS-forced Postgres a tenant-scoped alembic role
matched zero rows, and alembic still stamped the revision as applied — so
"migrated to head" was never evidence that the recode had happened (audit
finding P0-18). 202608150013 now lifts FORCE-RLS for its own rewrite, but that
only protects databases migrated from here on; one already stamped past it
carries the silent no-op forever.

This migration is the check for those: it looks for surviving legacy rows,
repairs them, and then ASSERTS none remain. On a healthy database it is a
no-op; on a silently-no-opped one it repairs; where it can do neither the
deploy fails instead of shipping ambiguous reporting identity.

Legacy vs official: only the PRE-TEMPLATE reconstructions are legacy, and they
are identifiable — they were registered under return families ``capital``
(BSD2) and ``liquidity`` (BSD3). The official BoG BSD2/BSD3 templates
registered afterwards carry family ``bsd`` and legitimately own those codes, so
every predicate here is family-scoped. ``return_signing_policies`` and
``return_signature_placements`` carry no family, so they are rewritten only when
the package evidence proves the original recode never ran on this database.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202608220029"
down_revision = "202608220028"
branch_labels = None
depends_on = None

RECODE: tuple[tuple[str, str], ...] = (("BSD2", "CAR-RWA"), ("BSD3", "LCR-NSFR"))
#: Rewritten only on the evidence-gated repair path; they have no family column.
CODE_ONLY_TABLES: tuple[str, ...] = (
    "return_signing_policies",
    "return_signature_placements",
)
GUARDED_TABLES: tuple[str, ...] = ("regulatory_packages", *CODE_ONLY_TABLES)
#: The families the pre-template BSD2/BSD3 reconstructions were registered under.
LEGACY_FAMILIES = "'capital', 'liquidity'"
LEGACY_PACKAGES = (
    f"return_code IN ('BSD2', 'BSD3') AND return_family IN ({LEGACY_FAMILIES})"
)


def upgrade() -> None:
    bind = op.get_bind()
    with force_rls_suspended(bind, *GUARDED_TABLES):
        legacy = bind.scalar(
            sa.text(f"SELECT count(*) FROM regulatory_packages WHERE {LEGACY_PACKAGES}")
        )
        if not legacy:
            return

        for old, new in RECODE:
            bind.execute(
                sa.text(
                    "UPDATE regulatory_packages SET return_code = :new "
                    f"WHERE return_code = :old AND return_family IN ({LEGACY_FAMILIES})"
                ),
                {"new": new, "old": old},
            )
            for table in CODE_ONLY_TABLES:
                bind.execute(
                    sa.text(f"UPDATE {table} SET return_code = :new WHERE return_code = :old"),
                    {"new": new, "old": old},
                )

        remaining = bind.scalar(
            sa.text(f"SELECT count(*) FROM regulatory_packages WHERE {LEGACY_PACKAGES}")
        )
        if remaining:
            msg = (
                f"{remaining} legacy BoG return code(s) survive in regulatory_packages "
                "after the repair, so migration 202608150013 never applied and cannot be "
                "replayed by this role. Re-run with a role that can write through "
                "row-level security (WORKER_DATABASE_URL)."
            )
            raise RuntimeError(msg)


def downgrade() -> None:
    """Deliberately irreversible.

    Restoring ``BSD2``/``BSD3`` to the pre-template reconstructions would collide
    with the official BoG templates that now legitimately own those codes.
    """
