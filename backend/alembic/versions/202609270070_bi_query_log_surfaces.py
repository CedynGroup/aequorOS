"""Admit the ``packs`` and ``insights`` read surfaces to ``bi_query_log``.

Revision ID: 202609270070
Revises: 202609270069

Phase 2 adds two read surfaces the append-only log has no word for: a content
pack (the seven certified dashboards) and an insight set (the statements derived
from a fact sheet). Both are reads a reader makes and both must be recorded as
what they are.

``202609220066``'s own reasoning is why they are added rather than folded into a
neighbouring word. It admitted ``trust`` and ``catalogue`` — surfaces that
return no mart rows — because the read budget is counted over this table, so a
surface with no value of its own either goes unmetered or is recorded as
something it is not, and "putting an event in an append-only audit table that
did not happen is worse than the missing limit". The same argument decides these
two: a pack read logged as ``catalogue`` cannot be told apart from listing the
metric dictionary, and an insight set logged as ``query`` loses that the
platform made a STATEMENT about the bank rather than returning a figure. The
point of this table is knowing what a reader was shown.

The CHECK is dropped and recreated on the partitioned PARENT, which recurses to
every present partition and is inherited by every future one, so
``bi_ensure_month_partition`` needs no change: a child created by
``CREATE TABLE ... PARTITION OF`` carries the parent's constraints. Widening
validates trivially against stored rows.

Why the downgrade can FAIL, deliberately
----------------------------------------
Narrowing a CHECK applies to every row, not only to new ones, so the usual
pattern (``202609220068``, ``202609270069``) is to delete the rows the narrowed
constraint cannot admit. **This table is append-only and that is not available
here**, by design: DELETE is revoked from every non-superuser role, refused by a
RESTRICTIVE policy, and blocked row-by-row by the ``aequoros_append_only_guard``
trigger, exactly like ``audit_events`` (``202607250027``). So the downgrade
recreates the narrow constraint and FAILS, loudly, if any ``packs`` or
``insights`` row has ever been written.

That is the correct outcome rather than a defect to work around. The alternative
is a migration that deletes audit rows to make itself possible, and an
append-only log a downgrade can quietly empty is not an append-only log. A
downgrade past this revision is therefore available only before these surfaces
have been used — which is the whole window in which anyone would want it — and
after that the recovery is to fix forward.
"""

from __future__ import annotations

from alembic import op

revision = "202609270070"
down_revision = "202609270069"
branch_labels = None
depends_on = None

TABLE = "bi_query_log"
CONSTRAINT = "ck_bi_query_log_surface"

NEW_SURFACES = ("packs", "insights")
SURFACES_BEFORE = "'query', 'grid', 'drill', 'explain', 'export', 'feed', 'trust', 'catalogue'"
SURFACES_AFTER = f"{SURFACES_BEFORE}, " + ", ".join(f"'{surface}'" for surface in NEW_SURFACES)


def _set_surfaces(surfaces: str) -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, f"surface IN ({surfaces})")


def upgrade() -> None:
    _set_surfaces(SURFACES_AFTER)


def downgrade() -> None:
    # No DELETE first: this table is append-only (see the module docstring).
    # If a `packs` or `insights` row exists, this statement fails and the
    # downgrade stops — which is the intended behaviour, not an oversight.
    _set_surfaces(SURFACES_BEFORE)
