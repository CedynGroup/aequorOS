"""BI stops grading its own figures against the returns the platform files.

Revision ID: 202609290080
Revises: 202609290079

Founder's decision, 2026-09-29: *"strip reconciliation from bi entirely. we are
showing intelligence to users based on their data. Let's not complicate things.
it should be across all 5 phases."*

Why the table goes rather than merely going quiet
-------------------------------------------------
``bi_reconciliation_results`` exists for exactly one thing: one row per check
R1–R12 per (bank, as-of), which became a data-trust verdict on every BI figure.
Both halves of that are now gone from the code, and the table has no other
reader, no foreign key pointing at it, and no job type of its own — the checks
ran inside the ``bi_mart_refresh`` handler. A table that nothing writes and
nothing reads is the inert-feature defect this build has already shipped four
times (``AGENTS.md``: "a registered job with no enqueue site"), so it is dropped
here rather than left behind to be rediscovered as state.

**The mistake being corrected is a plane confusion, not a bug.** ``reconciliation.py``
opened "Reconciliation of the BI marts to the figures **the platform already
files**", and R4 summed ``pl_sign × ytd_rc`` per BSD7A line against BSD7A's own
``bsd7.pl_line`` ledger map — a Bank of Ghana return form. BI reads the bank's own
treasury and ALM book; the filing plane is a different plane with a different
authority model, and grading one against the other put a regulatory verdict on an
ALCO chart. The red "does not reconcile" chip then rendered on every dashboard
card, including packs that touch no return at all.

**Nothing on the regulatory side is touched.** The filing gates, the ICAAP freeze
gates' reconciliation checks and ``derive_facts``' refusal to derive an
unreconciled book all remain: a date that cannot produce a filable book must
still produce nothing. That refusal is in the CALCULATION plane and was never
part of BI.

What this revision deliberately does NOT narrow
-----------------------------------------------
``ck_bi_query_log_surface`` still admits ``'trust'``. ``bi_query_log`` is an
APPEND-ONLY audit tier — a row trigger blocks UPDATE and DELETE, and RESTRICTIVE
policies block them again for statements addressed to the parent — so narrowing
the vocabulary would require deleting the log rows recording trust reads that
genuinely happened. **An append-only table's vocabulary can only ever grow.**
Those reads occurred, a supervisor may ask who performed them, and the evidence
outlives the feature. This is the same reasoning ``202609280077`` used to refuse
a destructive downgrade on the same table, and the opposite of ``202609290078``,
where the rows were derived mart cells that the next build rebuilds.

What a downgrade of this revision restores, and what it cannot
--------------------------------------------------------------
``downgrade`` recreates the table with its full hardening — ENABLE + FORCE RLS
and the tenant policy, because **PostgreSQL does not inherit RLS**, and a
recreated table with no policy would be readable across tenants by any role
holding blanket SELECT — and with the R1–R12 vocabulary, which is the state
``202609280075`` (R11) and ``202609280076`` (R12) left at this point in the
chain. Restoring the narrower R1–R10 vocabulary would break the two revisions
below, whose own downgrades expect to narrow it themselves.

It does not restore the ROWS, and it cannot: they were derived from canonical
data by a service that this revision's companion commit deletes. Alembic
downgrades a schema, not a checkout — the rows come back when the code that
computes them does, on the next build. Stated plainly because a downgrade that
silently leaves a table empty is easy to read as data loss, and this is not
that: every row was reproducible and none was evidence of anything.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609290080"
down_revision = "202609290079"
branch_labels = None
depends_on = None

TABLE = "bi_reconciliation_results"

#: The vocabulary as of the revision BELOW this one: `202609220066` created
#: R1–R10, `202609280075` added R11 and `202609280076` added R12. Pinned as text
#: rather than imported from the model, which no longer has it to import — the
#: same reason `202609220066` pinned its own.
_CHECK_IDS = "'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10', 'R11', 'R12'"
_STATUSES = "'green', 'amber', 'red', 'grey'"

#: Copied from `202609220066._TENANT_ID_EXPR`. The policy must be byte-identical
#: to the one being dropped or a downgrade would restore a DIFFERENT isolation
#: rule under the same name.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"


def _money(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(28, 6), nullable=True)


def upgrade() -> None:
    # Dropping the table drops its policy, constraints and FK with it; naming
    # them separately would fail on SQLite, where the hermetic suite builds from
    # the models and never ran the policy statements at all.
    op.drop_table(TABLE)


def downgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("check_id", sa.String(length=4), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        _money("lhs"),
        _money("rhs"),
        _money("difference"),
        _money("tolerance"),
        sa.Column("detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("builder_version", sa.Integer(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
        sa.CheckConstraint(f"check_id IN ({_CHECK_IDS})", name=f"ck_{TABLE}_check_id"),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name=f"ck_{TABLE}_status"),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "as_of_date",
            "check_id",
            name=f"uq_{TABLE}_bank_as_of_check",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name=f"fk_{TABLE}_bank",
        ),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {TABLE}_tenant_isolation ON {TABLE} FOR ALL "
        f"USING ((organization_id)::text = {_TENANT_ID_EXPR}) "
        f"WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})"
    )
