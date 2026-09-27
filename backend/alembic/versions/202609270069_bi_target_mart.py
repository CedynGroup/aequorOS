"""The pre-matched target mart ``bi_fact_target``, and the ``targets`` build scope.

Revision ID: 202609270069
Revises: 202609220068

``202609220068`` admitted the ``performance_targets`` reference dataset, which
is how a bank STATES a budget or reforecast. This revision creates the table BI
reads it back from, and it is deliberately not the register: a target is
declared per period against a measure id, while every catalogue variant
(``.actual`` / ``.target`` / ``.variance`` / ``.variance_pct`` /
``.attainment_pct`` — ``app/domain/bi/catalogue/targets.py``) is answered per
as-of date beside the ACTUAL it is compared with.

Why the comparison is stored and not joined (D-064)
---------------------------------------------------
The compiler admits exactly one fact table per query
(``app/services/bi/compiler.py``). A target joined at query time would either
break that rule or fan out: a bank-wide target row multiplied across the
scoped rows of the same measure double-counts, and the double count is
invisible because both operands are legitimate figures. So the mart builder
resolves the comparison once per (as-of, measure, scope, version) and stores
the target, the actual and the variance on ONE row of ONE table. Every variant
then reads this table alone, the one-fact rule holds unrelaxed, and the fan-out
is closed by construction rather than by a guard someone has to keep passing.

``scope_basis`` records WHICH target was matched — one the bank declared for
exactly this scope, or the bank-wide target applied as the fallback — so the
number is auditable from the row without re-reading the register.
``target_value`` is NOT NULL because the row exists only because a target does:
a measure with no target has NO ROW, which is what makes its variants NULL
instead of zero. ``actual_value`` and ``variance_value`` are nullable for the
opposite case — a date whose book cannot answer the base measure still records
the target the bank is being held to.

Plain, not partitioned
----------------------
Unlike the position facts this table holds one row per measure per scope per
date, not one per position, so it is sized like ``bi_fact_engine_metric`` and
gets no partitions. The primary key leads with ``(organization_id, bank_id,
as_of_date)``, which is every read path the variants use, so it needs no
secondary index either.

The ``targets`` build scope
---------------------------
``MART_BUILD_SCOPES`` gained ``targets`` and ``mart_builder`` writes one
``bi_mart_builds`` row per scope, so a production build FAILS on the old CHECK
until it is widened. Drop-and-create with the BEFORE list written out
literally, as ``202608160014`` and ``202609220068`` do: a literal keeps a fresh
upgrade and an already-migrated database on the same constraint after the next
scope is added, and lets the downgrade restore the historical shape. The parity
between the model tuple and the migrated CHECK is asserted in
``tests/db/test_bi_foundation_migration.py`` instead, so a scope added to the
tuple without a migration fails there rather than in a tenant's nightly build.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609270069"
down_revision = "202609220068"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

TABLE = "bi_fact_target"

#: Vocabularies, PINNED here rather than imported from the model so a later
#: model edit cannot change what this revision created (the same reason
#: ``202609220066`` pins its own). The Postgres suite proves the migrated
#: CHECKs still admit every value the model's tuples emit.
_PERIOD_GRAINS = "'month', 'quarter', 'half_year', 'year'"
_TARGET_VERSIONS = "'budget', 'reforecast'"
_TIME_BEHAVIOURS = "'stock', 'flow'"
_SCOPE_BASES = "'exact', 'bank_wide'"
#: A scope value is copied verbatim from whichever catalogue dimension the target
#: names, and the widest of those is 255 characters. Pinned here rather than
#: imported, like every other literal in this file. At 160 one long register row
#: raised a truncation inside the build's single nested transaction and failed
#: all six scopes nightly (audit A8-01).
_SCOPE_VALUE_WIDTH = 255

_BUILDS_TABLE = "bi_mart_builds"
_BUILDS_SCOPE_CONSTRAINT = "ck_bi_mart_builds_scope"
_NEW_SCOPE = "targets"
_SCOPES_BEFORE = "'positions', 'events', 'gl', 'engine', 'dims'"
_SCOPES_AFTER = f"{_SCOPES_BEFORE}, '{_NEW_SCOPE}'"


def _money(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Numeric(28, 6), nullable=nullable)


def _create_target_fact() -> None:
    op.create_table(
        TABLE,
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("measure_id", sa.String(length=160), nullable=False),
        sa.Column("scope_dimension", sa.String(length=80), nullable=False),
        sa.Column("scope_value", sa.String(length=_SCOPE_VALUE_WIDTH), nullable=False),
        sa.Column("target_version", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("period_grain", sa.String(length=16), nullable=False),
        sa.Column("time_behaviour", sa.String(length=8), nullable=False),
        _money("target_value", nullable=False),
        _money("actual_value"),
        _money("variance_value"),
        sa.Column("scope_basis", sa.String(length=16), nullable=False),
        sa.Column("declared_scope_dimension", sa.String(length=80), nullable=False),
        sa.Column("declared_scope_value", sa.String(length=_SCOPE_VALUE_WIDTH), nullable=False),
        sa.Column("builder_version", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "bank_id",
            "as_of_date",
            "measure_id",
            "scope_dimension",
            "scope_value",
            "target_version",
            name=f"pk_{TABLE}",
        ),
        sa.CheckConstraint(f"period_grain IN ({_PERIOD_GRAINS})", name=f"ck_{TABLE}_period_grain"),
        sa.CheckConstraint(f"target_version IN ({_TARGET_VERSIONS})", name=f"ck_{TABLE}_version"),
        sa.CheckConstraint(
            f"time_behaviour IN ({_TIME_BEHAVIOURS})", name=f"ck_{TABLE}_time_behaviour"
        ),
        sa.CheckConstraint(f"scope_basis IN ({_SCOPE_BASES})", name=f"ck_{TABLE}_scope_basis"),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name=f"fk_{TABLE}_bank",
        ),
    )


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL "
        f"USING ((organization_id)::text = {_TENANT_ID_EXPR}) "
        f"WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})"
    )


def _set_build_scopes(scopes: str) -> None:
    with op.batch_alter_table(_BUILDS_TABLE) as batch:
        batch.drop_constraint(_BUILDS_SCOPE_CONSTRAINT, type_="check")
        batch.create_check_constraint(_BUILDS_SCOPE_CONSTRAINT, f"scope IN ({scopes})")


def upgrade() -> None:
    _create_target_fact()
    _set_build_scopes(_SCOPES_AFTER)

    if op.get_bind().dialect.name != "postgresql":
        return
    _enable_rls(TABLE)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.drop_table(TABLE)

    # The narrowed CHECK applies to every row, not only to new ones, so a
    # stored `targets` build record makes it fail to apply. Those rows have to
    # go first — they are build BOOKKEEPING for a mart this downgrade is
    # deleting, so nothing a tenant supplied is lost.
    #
    # `bi_mart_builds` is FORCE-RLS and alembic runs as the tenant-scoped app
    # role, so this DELETE would otherwise match zero rows in every tenant and
    # report success — leaving exactly the rows behind that then make the
    # constraint fail. `force_rls_suspended` lifts FORCE for the owner inside
    # this transaction only, and is loud when the role cannot lift it.
    with force_rls_suspended(op.get_bind(), _BUILDS_TABLE):
        op.execute(f"DELETE FROM {_BUILDS_TABLE} WHERE scope = '{_NEW_SCOPE}'")
    _set_build_scopes(_SCOPES_BEFORE)
