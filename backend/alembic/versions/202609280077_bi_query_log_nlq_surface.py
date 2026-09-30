"""``nlq`` joins the ``bi_query_log`` surface vocabulary.

Revision ID: 202609280077
Revises: 202609280076

``docs/bi.md`` §Phase 5's natural-language clause ends "and logged", and the
surface is how a log row says WHICH question was asked. Without this value the
ask route logs under ``catalogue`` — defensible, because the read it performs
genuinely IS a catalogue read and the action is in the query digest, and it is the
same accommodation ``manage_bi_commentary`` makes by logging under ``insights``.
But it makes one query impossible to answer: *which reads came from a model
proposing a query rather than a person composing one.* For a surface where the
platform hands a question to an external model, that is the query an auditor will
ask first, so it gets its own value.

The surface is metadata about the REQUEST and never about the tenant's data, so
adding a value discloses nothing: it names the door, not what came through it.

Downgrade fails by design, exactly as ``202609270070`` documented for ``packs``
and ``insights``
--------------------------------------------------------------------------------
``bi_query_log`` is append-only at the AUDIT tier — migration ``202607250027``
installs a trigger blocking UPDATE and DELETE, and revokes both — so this
revision cannot delete the rows a narrowed CHECK would reject. It does not try.
If an ``nlq`` row exists, ``downgrade`` fails and stops, and that is the intended
behaviour rather than an oversight: silently discarding audit rows to make a
schema change fit is the opposite of what an append-only audit table is for. A
downgrade past this revision is available only before the surface has been used.
"""

from __future__ import annotations

from alembic import op

revision = "202609280077"
down_revision = "202609280076"
branch_labels = None
depends_on = None

TABLE = "bi_query_log"
CONSTRAINT = "ck_bi_query_log_surface"

NEW_SURFACES = ("nlq",)
#: Written out rather than derived from ``models.bi.QUERY_LOG_SURFACES``, as
#: ``202609220068`` established: a literal keeps a fresh upgrade and an
#: already-migrated database on the same constraint after the NEXT surface is
#: added, and lets the downgrade restore the historical shape. Parity between the
#: constant and the migrated CHECK is asserted instead, by
#: ``tests/db/test_bi_migration_structural_parity.py``.
SURFACES_BEFORE = (
    "'query', 'grid', 'drill', 'explain', 'export', 'feed', 'trust', 'catalogue', "
    "'packs', 'insights'"
)
SURFACES_AFTER = f"{SURFACES_BEFORE}, " + ", ".join(f"'{surface}'" for surface in NEW_SURFACES)


def _set_surfaces(surfaces: str) -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, f"surface IN ({surfaces})")


def upgrade() -> None:
    _set_surfaces(SURFACES_AFTER)


def downgrade() -> None:
    # No DELETE first: the table is append-only at the audit tier. If an `nlq`
    # row exists this statement fails and the downgrade stops. See the docstring.
    _set_surfaces(SURFACES_BEFORE)
