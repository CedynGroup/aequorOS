"""Add ``credit`` to every institution type's default module set.

The Credit / Loan Book module applies to every deposit-taking lender: a bank
files the 5-grade classification, an SDI the NBFI 4-grade, and both are bound
by the NPL prudential limit of Notice BG/GOV/SEC/2025/23 — so ``credit`` joins
``BANK_MODULES`` in the live catalogue (``app/services/institution_types.py``)
and, through the class derivation, the SDI set too. This migration reconciles
the already-seeded ``institution_types`` rows to that catalogue end state.

Idempotent per row: appends ``credit`` only where absent, preserving each
row's existing order (per-licence customisations survive). Downgrade removes
it wherever present.

Revision ID: 202609010046
Revises: 202609010045
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "202609010046"
down_revision = "202609010045"
branch_labels = None
depends_on = None

MODULE = "credit"


def _rows(bind: sa.engine.Connection) -> list[tuple[str, list[str]]]:
    result = bind.execute(sa.text("SELECT type_code, default_modules FROM institution_types"))
    rows: list[tuple[str, list[str]]] = []
    for type_code, raw in result:
        modules = raw if isinstance(raw, list) else json.loads(raw)
        rows.append((type_code, list(modules)))
    return rows


def _write(bind: sa.engine.Connection, type_code: str, modules: list[str]) -> None:
    bind.execute(
        sa.text(
            "UPDATE institution_types SET default_modules = :mods WHERE type_code = :code"
        ).bindparams(
            sa.bindparam("mods", value=modules, type_=sa.JSON()),
            sa.bindparam("code", value=type_code),
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    for type_code, modules in _rows(bind):
        if MODULE not in modules:
            # After "capital" where present, so the set reads in nav order.
            anchor = modules.index("capital") + 1 if "capital" in modules else len(modules)
            modules.insert(anchor, MODULE)
            _write(bind, type_code, modules)


def downgrade() -> None:
    bind = op.get_bind()
    for type_code, modules in _rows(bind):
        if MODULE in modules:
            _write(bind, type_code, [m for m in modules if m != MODULE])
