"""Branch and region data scopes on ``authorization_bindings``.

Revision ID: 202609270073
Revises: 202609270072

``docs/bi.md`` §Phase 4 asks for two columns, and they are the last dimension
the indivisible binding row was missing: WHICH SLICE of an institution's book
the sentence admits. Until now a binding said *which institution, which module,
which sensitivity* and then always meant the whole book — ``BiDataScope`` in
``app/services/bi/authorization.py`` has carried the shape since Phase 1 with a
docstring saying the columns do not exist yet. They do now.

``all`` is not a value with no list — it is the ONLY kind whose list is NULL
-------------------------------------------------------------------------
The CHECK is written both ways on purpose::

    (kind = 'all'  AND values IS NULL)
 OR (kind <> 'all' AND values IS NOT NULL AND json_array_length(values) > 0)

The first half stops a row from carrying a branch list it does not apply — a
list that a future reader could start honouring, silently narrowing an
institution-wide grant nobody meant to narrow. The second half is the one that
matters for safety: without it a ``branch`` binding could store ``[]`` or NULL,
and the natural way to write the reader (``if values: inject a filter``) would
then serve that principal THE WHOLE BOOK. A scope that means "no branches"
must be storable only as a scope that returns no rows, never as an absent
filter. The database refuses the shape rather than trusting every future
reader to get the emptiness case right.

``json_array_length`` is available in both dialects (PostgreSQL built-in for
the ``json`` type; SQLite's JSON1, compiled in since 3.38), so the hermetic
``create_all`` suite enforces the identical invariant the migration does.

Why a DEFAULT, and why it stays
-------------------------------
Every existing row means the whole institution, so ``'all'`` backfills them
with no data step (and is therefore safe under the app role's FORCE-RLS
session, which cannot UPDATE tenant rows). The server default is KEPT rather
than dropped after backfill: a binding written by any path that has not been
taught about data scopes must mean the whole institution, because the
alternative — NULL — is a kind no evaluator recognises, and an unrecognised
kind is exactly the ambiguity ``ResourceLocator`` already refuses elsewhere.
A caller that wants a narrower scope must say so.

``values`` is untyped text, deliberately
----------------------------------------
Branch codes are an OPEN vocabulary (``bi_dim_branch.branch_code`` is
``VARCHAR(120)``, sourced from whatever the core banking system calls its
company/branch code) and regions likewise (``business_units.region``, declared
and never inferred). There is no enum to constrain against and no FK to hang
this on: a binding may legitimately name a branch that has not been ingested
yet, which must scope the reader to nothing rather than fail their sign-in.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609270073"
down_revision = "202609270072"
branch_labels = None
depends_on = None

_TABLE = "authorization_bindings"
_KINDS = ("all", "branch", "region")


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "data_scope_kind",
            sa.String(length=16),
            nullable=False,
            server_default="all",
        ),
    )
    op.add_column(_TABLE, sa.Column("data_scope_values", sa.JSON(), nullable=True))
    values = ", ".join(f"'{kind}'" for kind in _KINDS)
    op.create_check_constraint(
        "ck_authorization_bindings_data_scope_kind",
        _TABLE,
        f"data_scope_kind IN ({values})",
    )
    op.create_check_constraint(
        "ck_authorization_bindings_data_scope_values",
        _TABLE,
        "(data_scope_kind = 'all' AND data_scope_values IS NULL) OR "
        "(data_scope_kind <> 'all' AND data_scope_values IS NOT NULL AND "
        "json_array_length(data_scope_values) > 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_authorization_bindings_data_scope_values", _TABLE, type_="check")
    op.drop_constraint("ck_authorization_bindings_data_scope_kind", _TABLE, type_="check")
    op.drop_column(_TABLE, "data_scope_values")
    op.drop_column(_TABLE, "data_scope_kind")
