"""BoG official prudential returns: 'bsd' family, 'weekly' frequency, legacy recode

Revision ID: 202608150013
Revises: 202608150012

The official Bank of Ghana BSD templates (docs/reporting/) are now generated
template-faithfully by ``bog_forms`` (docs/bog_returns/00_full_return_registry.md):

- widens ``ck_regulatory_packages_return_family`` to admit ``bsd`` (the family of
  BSD1 … BSD17) and ``ck_regulatory_packages_frequency`` to admit ``weekly``
  (BSD1/1A/1B/14/15A/15B — Guide: weekly, 9 days);
- right-reporting reconciliation (registry doc §3): the pre-template
  reconstructions registered as ``BSD2`` (Capital Adequacy) and ``BSD3``
  (LCR/NSFR) never were those BoG forms — official BSD2 is the Statement of
  Assets & Liabilities, BSD3 is Large Exposures. Their stored ``return_code``
  values are recoded to ``CAR-RWA`` / ``LCR-NSFR`` in every table that carries
  a return code (packages, signing policies, signature placements) so the
  official codes are free for the real templates. Idempotent (a re-run finds
  nothing to rename); the downgrade restores the legacy codes.

  RLS HAZARD (learned 2026-08-16 on the primary; CLOSED 2026-08-21): these
  tables are FORCE-RLS, so when alembic runs as the tenant-scoped app role the
  UPDATEs saw ZERO rows and silently no-opped. The original remediation was a
  human remembering to re-run under a BYPASSRLS role, which audit finding P0-18
  correctly called a defect rather than a fix. ``_recode_visible`` now lifts
  FORCE for the duration of the rewrite (``app.db.session.force_rls_suspended``)
  and raises when it cannot, so the recode either happens or fails loudly under
  every role. Still idempotent and safe to repeat.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202608150013"
down_revision = "202608150012"
branch_labels = None
depends_on = None

FREQUENCIES_BEFORE = "'monthly', 'quarterly', 'semiannual', 'annual', 'daily'"
FREQUENCIES_AFTER = "'weekly', 'monthly', 'quarterly', 'semiannual', 'annual', 'daily'"
FAMILIES_BEFORE = (
    "'liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress', 'corporate', "
    "'large_exposures', 'dbk', 'stress'"
)
FAMILIES_AFTER = (
    "'liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress', 'corporate', "
    "'large_exposures', 'dbk', 'stress', 'bsd'"
)

RECODE: tuple[tuple[str, str], ...] = (("BSD2", "CAR-RWA"), ("BSD3", "LCR-NSFR"))
RETURN_CODE_TABLES: tuple[str, ...] = (
    "regulatory_packages",
    "return_signing_policies",
    "return_signature_placements",
)


def _swap_check(name: str, table: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def _recode(pairs: tuple[tuple[str, str], ...]) -> None:
    for table in RETURN_CODE_TABLES:
        for old, new in pairs:
            op.execute(
                sa.text(
                    f"UPDATE {table} SET return_code = :new WHERE return_code = :old"
                ).bindparams(new=new, old=old)
            )


def _recode_visible(pairs: tuple[tuple[str, str], ...]) -> None:
    """Recode with the tables' FORCE-RLS lifted for this transaction only.

    Without this the ``UPDATE``s below match zero rows whenever alembic runs as
    the tenant-scoped app role — the hazard this migration's docstring records,
    left to a human to remember. ``force_rls_suspended`` performs the rewrite
    correctly under any role that owns the tables and raises loudly when it
    cannot, so "silently did nothing" is no longer a reachable outcome.
    """
    with force_rls_suspended(op.get_bind(), *RETURN_CODE_TABLES):
        _recode(pairs)


def upgrade() -> None:
    _swap_check(
        "ck_regulatory_packages_frequency",
        "regulatory_packages",
        f"frequency IN ({FREQUENCIES_AFTER})",
    )
    _swap_check(
        "ck_regulatory_packages_return_family",
        "regulatory_packages",
        f"return_family IN ({FAMILIES_AFTER})",
    )
    _recode_visible(RECODE)


def downgrade() -> None:
    _recode_visible(tuple((new, old) for old, new in RECODE))
    _swap_check(
        "ck_regulatory_packages_return_family",
        "regulatory_packages",
        f"return_family IN ({FAMILIES_BEFORE})",
    )
    _swap_check(
        "ck_regulatory_packages_frequency",
        "regulatory_packages",
        f"frequency IN ({FREQUENCIES_BEFORE})",
    )
