"""Bind the GOVERNED PARAMETER ROW IDENTITY into run provenance.

Audit 2026-08-22 D-18. A sealed ``RegulatoryRun`` records the parameter
*values* it consumed — ``inputs["parameters"]``, covered by the value-based
``input_hash`` — but never *which row* of the control plane supplied them. So
the question a supervisor actually asks — "prove this filed CAR used the
approved 13% and not a number someone edited afterwards" — had no answer on the
governance axis: you could show the value, not the authority behind it.

This adds one nullable JSON column, ``regulatory_runs.parameter_provenance``,
holding one entry per ``regulatory_parameter`` row the run resolved: the row
``id``, its scope key (``scope_type``/``scope_key``/``param_code``/
``jurisdiction_code``), its effective window, its ``confirmation_status``, the
four-eyes evidence (``proposed_by``/``approved_by``/``approved_at``), the value
as text, and ``updated_at`` — the row's version marker, since the control plane
has no version column. Together with ``202608230038``, which now forbids editing
an approved generation in place, that makes the authority behind a filed number
provable rather than assumed.

**Why a column and not the hashed snapshot.** ``input_hash`` is value-based by
contract (CLAUDE.md; ``INPUT_SCHEMA_VERSION = "bank-facts-v2"``, facts exclude
``fact.id``, canonically sorted) and parameter blocks join the snapshot only
when a value is CONSUMED. Row ids, ``approved_at`` and ``updated_at`` are
identity and timestamps, not values: putting them inside ``inputs`` would change
the hash of every future run, break reproducibility against every existing
sealed run, and make the hash depend on when a row was last touched. So this
lands beside the snapshot, never inside it. ``inputs`` is not read or rewritten
by this migration, and no existing ``input_hash`` moves —
``tests/services/test_input_hash_determinism.py`` and ``tests/equivalence``
are the executable proof.

NULL means "minted before this column existed" and is deliberately
distinguishable from ``[]``, which means "this run resolved no governed
parameter" — an empty list is a positive statement, and absence must never be
readable as one.

Revision ID: 202608230039
Revises: 202608230038
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608230039"
down_revision = "202608230038"
branch_labels = None
depends_on = None

_TABLE = "regulatory_runs"
_COLUMN = "parameter_provenance"


def upgrade() -> None:
    # Nullable with no server default: an existing run genuinely does not know
    # which rows it consumed, and backfilling a guess would be inventing
    # provenance. It reads as "unrecorded", which is the truth.
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
