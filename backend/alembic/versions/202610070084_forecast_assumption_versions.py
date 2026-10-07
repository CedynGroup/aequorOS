"""The governed forecast assumption register, and each run's record of what it used.

Revision ID: 202610070084
Revises: 202610040083

Forecast, what-if and optimizer runs resolved their base, adverse and severely
adverse presets from ``param_stress_shock`` rows with ``module = 'forecast'``. No
product path ever wrote those rows, so for every provisioned bank forecasting
refused with ``missing_parameter`` and there was no governed way out.

``forecast_assumption_versions`` replaces them as the only source the engine
reads: one row per version of a bank's COMPLETE assumption set, drafted by a
maker, decided by a different checker, and effective from a book date. It is
bank-scoped (each bank's board sets its own drivers; two banks of one
organization share an RLS tenant), with at most one draft or submission in
flight per bank (``uq_fav_one_open_per_bank``, partial on both dialects).

``regulatory_runs.assumption_provenance`` records which approved version a
forecasting run consumed, beside the value-based snapshot and never inside it.

Data step
---------
Any existing register presets are carried over, so a bank that could forecast
before this revision still can, on the same values: for each organization and
jurisdiction, every effective date at which the register resolved a COMPLETE set
(every preset scenario with every driver) becomes an approved version for each
bank of that organization in that jurisdiction, approved by the label the rows
named, at their approval time. An incomplete set is not carried over: it never
produced a run either, and inventing the missing values is exactly what the
register refuses. The ``param_stress_shock`` rows are left in place as the
historical record; nothing reads ``module = 'forecast'`` any more.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610070084"
down_revision = "202610040083"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

TABLE = "forecast_assumption_versions"

#: Vocabularies PINNED here rather than imported, like every other revision in
#: this chain, so a later model edit cannot change what this revision created.
_STATUSES = "'draft', 'submitted', 'approved', 'rejected'"
_OPEN_STATUSES = "'draft', 'submitted'"
_ORIGINS = "'tenant', 'register'"
_PRESET_CODES = ("base", "adverse", "severely_adverse")
_ASSUMPTION_KEYS = (
    "loan_growth_pct",
    "deposit_growth_pct",
    "nim_pct",
    "cost_to_income_pct",
    "credit_loss_rate_pct",
    "fx_depreciation_pct",
    "dividend_payout_pct",
)
_CARRIED_OVER_NOTE = (
    "Carried over from the board register's approved forecast presets when the "
    "governed assumption register replaced them (migration 202610070084)."
)

_versions = sa.table(
    TABLE,
    sa.column("id", sa.Uuid()),
    sa.column("organization_id", sa.String()),
    sa.column("bank_id", sa.String()),
    sa.column("version_number", sa.Integer()),
    sa.column("status", sa.String()),
    sa.column("origin", sa.String()),
    sa.column("effective_from", sa.Date()),
    sa.column("presets", sa.JSON()),
    sa.column("change_note", sa.Text()),
    sa.column("reviewed_at", sa.DateTime(timezone=True)),
    sa.column("approver_label", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=24), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("presets", sa.JSON(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("submitted_by", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("approver_label", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name="ck_fav_status"),
        sa.CheckConstraint(f"origin IN ({_ORIGINS})", name="ck_fav_origin"),
        sa.CheckConstraint("version_number >= 1", name="ck_fav_version_number"),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR reviewed_at IS NOT NULL",
            name="ck_fav_reviewed_at",
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR reviewed_by IS NOT NULL OR approver_label IS NOT NULL",
            name="ck_fav_approver",
        ),
        sa.CheckConstraint(
            "reviewed_by IS NULL OR submitted_by IS NULL OR reviewed_by <> submitted_by",
            name="ck_fav_checker_not_submitter",
        ),
        sa.CheckConstraint(
            "reviewed_by IS NULL OR created_by IS NULL OR reviewed_by <> created_by",
            name="ck_fav_checker_not_author",
        ),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "version_number", name="uq_fav_version_number"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=f"fk_{TABLE}_organization"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name="fk_fav_bank",
        ),
    )
    op.create_index(
        "ix_fav_bank_status_effective",
        TABLE,
        ["organization_id", "bank_id", "status", "effective_from"],
    )
    op.create_index(
        "uq_fav_one_open_per_bank",
        TABLE,
        ["organization_id", "bank_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_OPEN_STATUSES})"),
        sqlite_where=sa.text(f"status IN ({_OPEN_STATUSES})"),
    )
    with op.batch_alter_table("regulatory_runs") as batch:
        batch.add_column(sa.Column("assumption_provenance", sa.JSON(), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {TABLE}_tenant_isolation ON {TABLE} FOR ALL "
            f"USING ((organization_id)::text = {_TENANT_ID_EXPR}) "
            f"WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})"
        )

    with force_rls_suspended(bind, "param_stress_shock", "banks", TABLE):
        versions = _carried_over_versions(bind)
        if versions:
            bind.execute(sa.insert(_versions), versions)


def downgrade() -> None:
    with op.batch_alter_table("regulatory_runs") as batch:
        batch.drop_column("assumption_provenance")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.drop_table(TABLE)


def _carried_over_versions(bind: sa.Connection) -> list[dict[str, Any]]:
    """One approved version per bank for every date the register resolved a complete set."""
    rows = bind.execute(
        sa.text(
            "SELECT organization_id, jurisdiction_code, scenario_code, shock_key, "
            "shock_value, effective_from, effective_to, approved_by, approval_timestamp "
            "FROM param_stress_shock WHERE module = 'forecast' "
            "ORDER BY effective_from, approval_timestamp"
        )
    ).mappings()
    register: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in rows:
        register[(row["organization_id"], row["jurisdiction_code"])].append(row)
    if not register:
        return []

    banks: dict[tuple[str, str], list[str]] = defaultdict(list)
    for bank in bind.execute(
        sa.text("SELECT id, organization_id, jurisdiction_code FROM banks ORDER BY id")
    ).mappings():
        banks[(bank["organization_id"], bank["jurisdiction_code"])].append(bank["id"])

    now = datetime.now(UTC)
    versions: list[dict[str, Any]] = []
    for scope, scope_rows in register.items():
        generations = _generations(scope_rows)
        for bank_id in banks.get(scope, []):
            for number, (effective_from, presets, label, approved_at) in enumerate(
                generations, start=1
            ):
                versions.append(
                    {
                        "id": uuid4(),
                        "organization_id": scope[0],
                        "bank_id": bank_id,
                        "version_number": number,
                        "status": "approved",
                        "origin": "register",
                        "effective_from": effective_from,
                        "presets": presets,
                        "change_note": _CARRIED_OVER_NOTE,
                        "reviewed_at": approved_at,
                        "approver_label": label,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
    return versions


def _generations(rows: list[Any]) -> list[tuple[date, dict[str, dict[str, str]], str, Any]]:
    """The complete sets the register resolved, at each date a row took effect.

    Resolution is the register's own: rows active on the date, later
    ``effective_from`` overwriting earlier, only the projection drivers kept.
    """
    generations: list[tuple[date, dict[str, dict[str, str]], str, Any]] = []
    for effective_from in sorted({_as_date(row["effective_from"]) for row in rows}):
        active = [
            row
            for row in rows
            if _as_date(row["effective_from"]) <= effective_from
            and (row["effective_to"] is None or _as_date(row["effective_to"]) > effective_from)
        ]
        presets: dict[str, dict[str, str]] = {}
        for row in active:
            if row["shock_key"] in _ASSUMPTION_KEYS:
                value = format(Decimal(str(row["shock_value"])).normalize(), "f")
                presets.setdefault(row["scenario_code"], {})[row["shock_key"]] = value
        complete = all(
            set(presets.get(code, {})) == set(_ASSUMPTION_KEYS) for code in _PRESET_CODES
        )
        if not complete:
            continue
        presets = {code: presets[code] for code in _PRESET_CODES}
        if generations and generations[-1][1] == presets:
            continue
        approval = max(active, key=lambda row: str(row["approval_timestamp"]))
        generations.append(
            (effective_from, presets, approval["approved_by"], approval["approval_timestamp"])
        )
    return generations


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
