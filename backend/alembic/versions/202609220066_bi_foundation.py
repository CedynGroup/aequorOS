"""BI foundation: marts, dimensions, control tables, partitions and their RLS.

Revision ID: 202609220066
Revises: 202609200065

The BI plane gets its own storage (``bi_*``, models in ``app/models/bi.py``)
so that dashboards read typed, pre-joined projections instead of running the
engines or scanning the canonical book per request. Every number in these
tables is a COPY of something the platform already computed or a typed
projection of canonical rows; nothing here is an input to a filing.

Why partitions, and why the RLS is unusual
------------------------------------------
The daily position fact grows by one full book per business date and is only
kept for ``BI_DAILY_RETENTION_DAYS``, so it is ``PARTITION BY RANGE`` monthly
and retention is a partition drop, not a row delete. The month-end fact is
kept forever (yearly partitions); the aggregate, the loan-event fact and the
query log are monthly.

Postgres does NOT copy row-level security onto partitions: a policy on the
parent filters queries THROUGH the parent, but a partition addressed by name
has only its own policies. Every partition therefore needs ENABLE + FORCE +
the tenant policy of its own, and a child created at runtime by the builder
would ship without one unless creation itself installs it. That is what the
``SECURITY DEFINER`` functions ``bi_ensure_month_partition`` /
``bi_ensure_year_partition`` are for: they are the ONLY sanctioned way to
create a ``bi_*`` child (the app role never runs raw ``CREATE TABLE``), they
create the child with the standard tenant policy, and they are idempotent.
Their DROP siblings ``bi_drop_month_partition`` / ``bi_drop_year_partition``
are the only sanctioned way to remove one (retention), and are no-ops when
the child is absent. Each function accepts ONLY the parents of its own cadence
(the monthly set for the month functions, the yearly set for the year
functions — audit A4-02: a yearly child created on the monthly daily fact
would block every monthly child of that year and escape retention forever),
and every partition bound is computed with ``timezone`` pinned to UTC (audit
A4-01: the query log's ``timestamptz`` key would otherwise take the CALLING
session's offset, so a child created from a New York session both overlapped
the next UTC month and left rows to fall into DEFAULT). The DEFAULT partitions
are created here with the same policy and can never be dropped through the
functions, whose child names are derived from a date. A child must exist
before rows for its range are written: once rows for a month have landed in
the DEFAULT partition, creating that month's child fails on the conflicting
rows.

The functions run as their OWNER (the role that ran this migration and owns
the tables), so a distinct cross-tenant worker role can create and drop
partitions without owning anything — which is what lets retention run as the
BYPASSRLS worker at all (audit A4-03: a non-owner cannot ``DROP`` or ``DETACH``
a partition directly). EXECUTE is revoked from PUBLIC and granted to the
migrating role plus every BYPASSRLS login role present at migration time — no
migration here knows a role by name; the ``_revoke`` loops enumerate
``pg_roles`` for the same reason. A worker role created AFTER this migration
needs ``GRANT EXECUTE`` alongside the table grants it needs anyway.

``bi_query_log`` is append-only exactly like ``audit_events``
(``202607250027``): the existing ``aequoros_append_only_guard`` trigger (row
triggers on a partitioned parent are cloned onto every present and future
child), UPDATE/DELETE/TRUNCATE revoked, and RESTRICTIVE policies. The
ensure-partition functions mirror the parent's RESTRICTIVE policies onto each
new child, so a child addressed by name carries the same denial.

The ORM declares these as plain tables (D-011): the hermetic suite builds its
schema with ``create_all`` on SQLite, which has no partitions, and a Postgres
``create_all`` with ``PARTITION BY`` would create a parent with no partition
that rejects every insert. Partition routing and partition RLS are proven only
by the Postgres-gated suite: ``tests/db/test_bi_foundation_migration.py`` and
``tests/db/test_bi_partition_rls.py``.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609220066"
down_revision = "202609200065"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

#: Parent → range column. Monthly children are ``<parent>_y2026m09``, yearly
#: ``<parent>_y2026``; every parent also has ``<parent>_default``.
_MONTHLY: dict[str, str] = {
    "bi_fact_position_daily": "as_of_date",
    "bi_agg_position_daily": "as_of_date",
    "bi_fact_loan_event": "event_date",
    "bi_query_log": "queried_at",
}
_YEARLY: dict[str, str] = {"bi_fact_position_eom": "as_of_date"}
_PARTITIONED: tuple[str, ...] = (*_MONTHLY, *_YEARLY)
_PLAIN: tuple[str, ...] = (
    "bi_fact_gl_monthly",
    "bi_fact_engine_metric",
    "bi_dim_branch",
    "bi_dim_product",
    "bi_dim_counterparty",
    "bi_dim_gl_account",
    "bi_dim_date",
    "bi_mart_builds",
    "bi_reconciliation_results",
)
_TABLES: tuple[str, ...] = (*_PARTITIONED, *_PLAIN)
_QUERY_LOG = "bi_query_log"
_GUARD_FUNCTION = "aequoros_append_only_guard"
_PARTITION_FUNCTIONS: tuple[str, ...] = (
    "bi_ensure_month_partition(regclass, date)",
    "bi_ensure_year_partition(regclass, date)",
    "bi_drop_month_partition(regclass, date)",
    "bi_drop_year_partition(regclass, date)",
)

#: Vocabularies, PINNED here rather than imported from the model so a later
#: model edit cannot change what this revision created. The Postgres suite
#: proves the migrated CHECKs still agree with the model's.
_ATTRIBUTION_BASES = "'snapshot_on_or_before', 'no_snapshot', 'unmatched'"
_TIERS = "'live', 'official'"
_BALANCE_BASES = "'ytd', 'period'"
_BUILD_SCOPES = "'positions', 'events', 'gl', 'engine', 'dims'"
_BUILD_STATUSES = "'running', 'succeeded', 'failed', 'skipped'"
_CHECK_IDS = "'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9'"
_RECONCILIATION_STATUSES = "'green', 'amber', 'red', 'grey'"
_PRINCIPAL_TYPES = "'human', 'machine'"
_SURFACES = "'query', 'grid', 'drill', 'explain', 'export', 'feed'"
_DECISIONS = "'allowed', 'denied'"
_UNASSIGNED_REGION = "Unassigned region"


# --- column groups ------------------------------------------------------------


def _tenant_columns() -> list[sa.Column]:
    return [
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
    ]


def _builder_columns() -> list[sa.Column]:
    return [
        sa.Column("builder_version", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _bank_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["bank_id", "organization_id"],
        ["banks.id", "banks.organization_id"],
        name=f"fk_{table}_bank",
    )


def _money(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Numeric(28, 6), nullable=nullable)


def _position_fact_columns() -> list[sa.Column]:
    return [
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        *_tenant_columns(),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("position_type", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        _money("balance_native", nullable=False),
        _money("balance_rc"),
        sa.Column("fx_unconverted", sa.Boolean(), nullable=False),
        _money("classification_exposure_rc"),
        _money("notional_rc"),
        sa.Column("interest_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("rate_type", sa.String(length=16), nullable=True),
        sa.Column("rate_index", sa.String(length=40), nullable=True),
        sa.Column("contractual_maturity", sa.Date(), nullable=True),
        sa.Column("next_repricing_date", sa.Date(), nullable=True),
        sa.Column("maturity_bucket", sa.String(length=40), nullable=True),
        sa.Column("repricing_bucket", sa.String(length=40), nullable=True),
        sa.Column("origination_date", sa.Date(), nullable=True),
        sa.Column("vintage_month", sa.Date(), nullable=True),
        sa.Column("days_past_due", sa.Integer(), nullable=True),
        sa.Column("dpd_band", sa.String(length=32), nullable=True),
        sa.Column("ifrs9_stage", sa.SmallInteger(), nullable=True),
        sa.Column("grade", sa.String(length=32), nullable=True),
        sa.Column("non_performing", sa.Boolean(), nullable=True),
        sa.Column("classification_basis", sa.String(length=40), nullable=True),
        _money("provision_required_rc"),
        _money("provision_held_rc"),
        _money("interest_in_suspense_rc"),
        _money("collateral_rc"),
        sa.Column("collateral_type", sa.String(length=80), nullable=True),
        sa.Column("restructured", sa.Boolean(), nullable=True),
        sa.Column("deposit_account_type", sa.String(length=16), nullable=True),
        sa.Column("behavioral_maturity_months", sa.Numeric(12, 4), nullable=True),
        sa.Column("encumbered", sa.Boolean(), nullable=True),
        sa.Column("hqla_level", sa.String(length=8), nullable=True),
        sa.Column("branch_code", sa.String(length=120), nullable=True),
        sa.Column("product_code", sa.String(length=80), nullable=True),
        sa.Column("product_family", sa.String(length=40), nullable=True),
        sa.Column("exposure_category", sa.String(length=80), nullable=True),
        sa.Column("counterparty_id", sa.Uuid(), nullable=True),
        sa.Column("counterparty_type", sa.String(length=32), nullable=True),
        sa.Column("counterparty_group", sa.String(length=255), nullable=True),
        sa.Column("sector", sa.String(length=120), nullable=True),
        sa.Column("employer", sa.String(length=255), nullable=True),
        sa.Column("gl_account_code", sa.String(length=80), nullable=True),
        sa.Column("ingestion_batch_id", sa.Uuid(), nullable=True),
        *_builder_columns(),
    ]


# --- tables -------------------------------------------------------------------


def _create_position_fact(table: str, *, partition_by: str) -> None:
    op.create_table(
        table,
        *_position_fact_columns(),
        sa.PrimaryKeyConstraint("as_of_date", "snapshot_id", name=f"pk_{table}"),
        sa.CheckConstraint(
            "ifrs9_stage IS NULL OR ifrs9_stage IN (1, 2, 3)", name=f"ck_{table}_ifrs9_stage"
        ),
        _bank_fk(table),
        postgresql_partition_by=partition_by,
    )
    op.create_index(
        f"ix_{table}_org_bank_as_of_type",
        table,
        ["organization_id", "bank_id", "as_of_date", "position_type"],
    )
    op.create_index(
        f"ix_{table}_org_bank_as_of_branch",
        table,
        ["organization_id", "bank_id", "as_of_date", "branch_code"],
    )
    op.create_index(
        f"ix_{table}_org_bank_position_as_of",
        table,
        ["organization_id", "bank_id", "position_id", "as_of_date"],
    )


def _create_agg_position_daily() -> None:
    table = "bi_agg_position_daily"
    op.create_table(
        table,
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_tenant_columns(),
        sa.Column("position_type", sa.String(length=32), nullable=False),
        sa.Column("product_family", sa.String(length=40), nullable=True),
        sa.Column("branch_code", sa.String(length=120), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("ifrs9_stage", sa.SmallInteger(), nullable=True),
        sa.Column("dpd_band", sa.String(length=32), nullable=True),
        sa.Column("grade", sa.String(length=32), nullable=True),
        sa.Column("deposit_account_type", sa.String(length=16), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        _money("balance_rc_sum", nullable=False),
        _money("classification_exposure_rc_sum", nullable=False),
        _money("non_performing_exposure_rc_sum", nullable=False),
        _money("provision_required_rc_sum", nullable=False),
        _money("provision_held_rc_sum", nullable=False),
        _money("collateral_rc_sum", nullable=False),
        _money("rate_x_balance_rc_sum", nullable=False),
        sa.Column("fx_unconverted_count", sa.Integer(), nullable=False),
        *_builder_columns(),
        sa.PrimaryKeyConstraint("as_of_date", "id", name=f"pk_{table}"),
        sa.CheckConstraint(
            "ifrs9_stage IS NULL OR ifrs9_stage IN (1, 2, 3)", name=f"ck_{table}_ifrs9_stage"
        ),
        sa.CheckConstraint("row_count >= 0", name=f"ck_{table}_row_count"),
        sa.CheckConstraint("fx_unconverted_count >= 0", name=f"ck_{table}_fx_unconverted_count"),
        _bank_fk(table),
        postgresql_partition_by="RANGE (as_of_date)",
    )
    # The grain with NULLs coalesced (``ifrs9_stage`` is 1..3, so 0 stands in
    # for ''); ``as_of_date`` is present, as a unique index on a range-
    # partitioned table requires.
    op.create_index(
        f"uq_{table}_grain",
        table,
        [
            "organization_id",
            "bank_id",
            "as_of_date",
            "position_type",
            sa.text("coalesce(product_family, '')"),
            sa.text("coalesce(branch_code, '')"),
            "currency",
            sa.text("coalesce(ifrs9_stage, 0)"),
            sa.text("coalesce(dpd_band, '')"),
            sa.text("coalesce(grade, '')"),
            sa.text("coalesce(deposit_account_type, '')"),
        ],
        unique=True,
    )


def _create_loan_event() -> None:
    table = "bi_fact_loan_event"
    op.create_table(
        table,
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        *_tenant_columns(),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("event_subtype", sa.String(length=40), nullable=True),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("position_source_reference", sa.String(length=255), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        _money("amount_native", nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        _money("amount_rc"),
        sa.Column("fx_unconverted", sa.Boolean(), nullable=False),
        sa.Column("attribution_basis", sa.String(length=32), nullable=False),
        sa.Column("branch_code", sa.String(length=120), nullable=True),
        sa.Column("product_code", sa.String(length=80), nullable=True),
        sa.Column("product_family", sa.String(length=40), nullable=True),
        sa.Column("counterparty_id", sa.Uuid(), nullable=True),
        sa.Column("sector", sa.String(length=120), nullable=True),
        *_builder_columns(),
        sa.PrimaryKeyConstraint("event_date", "event_id", name=f"pk_{table}"),
        sa.CheckConstraint(
            f"attribution_basis IN ({_ATTRIBUTION_BASES})", name=f"ck_{table}_attribution_basis"
        ),
        _bank_fk(table),
        postgresql_partition_by="RANGE (event_date)",
    )
    op.create_index(
        f"ix_{table}_org_bank_date", table, ["organization_id", "bank_id", "event_date"]
    )


def _create_gl_monthly() -> None:
    table = "bi_fact_gl_monthly"
    op.create_table(
        table,
        *_tenant_columns(),
        sa.Column("month_end", sa.Date(), nullable=False),
        sa.Column("gl_account_code", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("calendar_month", sa.Date(), nullable=False),
        sa.Column("account_class", sa.String(length=16), nullable=False),
        _money("ytd_rc", nullable=False),
        _money("prior_ytd_rc"),
        _money("movement_rc"),
        sa.Column("missing_prior", sa.Boolean(), nullable=False),
        sa.Column("balance_basis", sa.String(length=16), nullable=False),
        sa.Column("pl_line", sa.String(length=80), nullable=True),
        sa.Column("pl_sign", sa.SmallInteger(), nullable=True),
        *_builder_columns(),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "bank_id",
            "month_end",
            "gl_account_code",
            "currency",
            name=f"pk_{table}",
        ),
        sa.CheckConstraint(
            f"balance_basis IN ({_BALANCE_BASES})", name=f"ck_{table}_balance_basis"
        ),
        _bank_fk(table),
    )


def _create_engine_metric() -> None:
    table = "bi_fact_engine_metric"
    op.create_table(
        table,
        *_tenant_columns(),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("module", sa.String(length=16), nullable=False),
        sa.Column("metric_id", sa.String(length=120), nullable=False),
        sa.Column("tier", sa.String(length=8), nullable=False),
        _money("value"),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=8), nullable=True),
        sa.Column("regime", sa.String(length=40), nullable=True),
        sa.Column("institution_class", sa.String(length=40), nullable=True),
        sa.Column("advisory_designation", sa.String(length=40), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("engine_version", sa.String(length=80), nullable=True),
        sa.Column("pipeline_state", sa.String(length=16), nullable=True),
        sa.Column("reconciliation_blocked", sa.Boolean(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        *_builder_columns(),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "bank_id",
            "as_of_date",
            "module",
            "metric_id",
            "tier",
            name=f"pk_{table}",
        ),
        sa.CheckConstraint(f"tier IN ({_TIERS})", name=f"ck_{table}_tier"),
        _bank_fk(table),
    )


def _create_dimensions() -> None:
    op.create_table(
        "bi_dim_branch",
        *_tenant_columns(),
        sa.Column("branch_code", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "region",
            sa.String(length=120),
            nullable=False,
            server_default=sa.text(f"'{_UNASSIGNED_REGION}'"),
        ),
        sa.Column("outlet_id", sa.Uuid(), nullable=True),
        sa.Column("outlet_type", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("mapped", sa.Boolean(), nullable=False),
        *_builder_columns(),
        sa.PrimaryKeyConstraint(
            "organization_id", "bank_id", "branch_code", name="pk_bi_dim_branch"
        ),
        _bank_fk("bi_dim_branch"),
    )
    op.create_table(
        "bi_dim_product",
        *_tenant_columns(),
        sa.Column("product_code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("product_family", sa.String(length=40), nullable=True),
        sa.Column("regulatory_category", sa.String(length=80), nullable=True),
        sa.Column("risk_weight_code", sa.String(length=16), nullable=True),
        *_builder_columns(),
        sa.PrimaryKeyConstraint(
            "organization_id", "bank_id", "product_code", name="pk_bi_dim_product"
        ),
        _bank_fk("bi_dim_product"),
    )
    op.create_table(
        "bi_dim_counterparty",
        *_tenant_columns(),
        sa.Column("counterparty_id", sa.Uuid(), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("counterparty_type", sa.String(length=32), nullable=False),
        sa.Column("group_reference", sa.String(length=255), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("rating", sa.String(length=16), nullable=True),
        *_builder_columns(),
        sa.PrimaryKeyConstraint(
            "organization_id", "bank_id", "counterparty_id", name="pk_bi_dim_counterparty"
        ),
        _bank_fk("bi_dim_counterparty"),
    )
    op.create_table(
        "bi_dim_gl_account",
        *_tenant_columns(),
        sa.Column("account_code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_class", sa.String(length=16), nullable=False),
        sa.Column("parent_account_code", sa.String(length=80), nullable=True),
        sa.Column("pl_line", sa.String(length=80), nullable=True),
        *_builder_columns(),
        sa.PrimaryKeyConstraint(
            "organization_id", "bank_id", "account_code", name="pk_bi_dim_gl_account"
        ),
        _bank_fk("bi_dim_gl_account"),
    )
    op.create_table(
        "bi_dim_date",
        *_tenant_columns(),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("has_data", sa.Boolean(), nullable=False),
        sa.Column("is_last_in_month", sa.Boolean(), nullable=False),
        sa.Column("is_last_in_quarter", sa.Boolean(), nullable=False),
        sa.Column("is_last_in_year", sa.Boolean(), nullable=False),
        sa.Column("calendar_month", sa.Date(), nullable=False),
        sa.Column("calendar_quarter", sa.Date(), nullable=False),
        sa.Column("calendar_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_quarter", sa.SmallInteger(), nullable=False),
        *_builder_columns(),
        sa.PrimaryKeyConstraint("organization_id", "bank_id", "date", name="pk_bi_dim_date"),
        _bank_fk("bi_dim_date"),
    )


def _create_control() -> None:
    op.create_table(
        "bi_mart_builds",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_tenant_columns(),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("builder_version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_counts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_bi_mart_builds"),
        sa.CheckConstraint(f"scope IN ({_BUILD_SCOPES})", name="ck_bi_mart_builds_scope"),
        sa.CheckConstraint(f"status IN ({_BUILD_STATUSES})", name="ck_bi_mart_builds_status"),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_bi_mart_builds_finished_after_started",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "as_of_date",
            "scope",
            name="uq_bi_mart_builds_bank_as_of_scope",
        ),
        _bank_fk("bi_mart_builds"),
    )
    op.create_table(
        "bi_reconciliation_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_tenant_columns(),
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
        sa.PrimaryKeyConstraint("id", name="pk_bi_reconciliation_results"),
        sa.CheckConstraint(
            f"check_id IN ({_CHECK_IDS})", name="ck_bi_reconciliation_results_check_id"
        ),
        sa.CheckConstraint(
            f"status IN ({_RECONCILIATION_STATUSES})", name="ck_bi_reconciliation_results_status"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "as_of_date",
            "check_id",
            name="uq_bi_reconciliation_results_bank_as_of_check",
        ),
        _bank_fk("bi_reconciliation_results"),
    )
    op.create_table(
        _QUERY_LOG,
        sa.Column("queried_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_tenant_columns(),
        sa.Column("principal_user_id", sa.Uuid(), nullable=False),
        sa.Column("principal_type", sa.String(length=16), nullable=False),
        sa.Column("surface", sa.String(length=16), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("member_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("decision", sa.String(length=8), nullable=False),
        sa.Column("denied_members", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("catalogue_version", sa.String(length=40), nullable=False),
        sa.Column("build_fingerprint", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("queried_at", "id", name=f"pk_{_QUERY_LOG}"),
        sa.CheckConstraint(
            f"principal_type IN ({_PRINCIPAL_TYPES})", name=f"ck_{_QUERY_LOG}_principal_type"
        ),
        sa.CheckConstraint(f"surface IN ({_SURFACES})", name=f"ck_{_QUERY_LOG}_surface"),
        sa.CheckConstraint(f"decision IN ({_DECISIONS})", name=f"ck_{_QUERY_LOG}_decision"),
        sa.CheckConstraint(
            "row_count IS NULL OR row_count >= 0", name=f"ck_{_QUERY_LOG}_row_count"
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name=f"ck_{_QUERY_LOG}_duration_ms"
        ),
        _bank_fk(_QUERY_LOG),
        postgresql_partition_by="RANGE (queried_at)",
    )
    op.create_index(
        f"ix_{_QUERY_LOG}_org_bank_queried_at",
        _QUERY_LOG,
        ["organization_id", "bank_id", "queried_at"],
    )
    op.create_index(
        f"ix_{_QUERY_LOG}_org_principal_queried_at",
        _QUERY_LOG,
        ["organization_id", "principal_user_id", "queried_at"],
    )


# --- Postgres: RLS, partitions, append-only ----------------------------------


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL "
        f"USING ((organization_id)::text = {_TENANT_ID_EXPR}) "
        f"WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})"
    )


def _revoke(table: str, privileges: str) -> None:
    op.execute(f"REVOKE {privileges} ON {table} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                     WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
            LOOP
                EXECUTE format('REVOKE {privileges} ON {table} FROM %I', r.rolname);
            END LOOP;
        END $$
        """
    )


def _restrictive_append_only_policies(table: str) -> None:
    op.execute(
        f"CREATE POLICY {table}_no_update ON {table} AS RESTRICTIVE FOR UPDATE TO PUBLIC "
        f"USING (false) WITH CHECK (false)"
    )
    op.execute(
        f"CREATE POLICY {table}_no_delete ON {table} AS RESTRICTIVE FOR DELETE TO PUBLIC "
        f"USING (false)"
    )


def _install_append_only(table: str) -> None:
    """``audit_events``' three guards (``202607250027``), on a partitioned parent.

    The row trigger is cloned by Postgres onto every partition, present and
    future; the revoked privileges and RESTRICTIVE policies bind statements
    addressed to the parent. The ensure-partition functions mirror the
    RESTRICTIVE policies onto each child they create, and ``_harden_child``
    does the same for the DEFAULT partition created here.
    """
    op.execute(
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}()"
    )
    _revoke(table, "UPDATE, DELETE, TRUNCATE")
    _restrictive_append_only_policies(table)
    # The database stamps arrival time, not an application host's wall clock.
    op.execute(f"ALTER TABLE {table} ALTER COLUMN queried_at SET DEFAULT now()")


class _Cadence:
    """What distinguishes the month functions from the year functions."""

    def __init__(self, *, label: str, parents: tuple[str, ...], argument: str) -> None:
        self.label = label
        self.parents = parents
        self.argument = argument
        unit = "month" if label == "monthly" else "year"
        self.lower = f"date_trunc('{unit}', {argument})::date"
        self.upper = f"(date_trunc('{unit}', {argument}) + interval '1 {unit}')::date"
        self.suffix = (
            'to_char(lower_bound, \'"y"YYYY"m"MM\')'
            if unit == "month"
            else "to_char(lower_bound, '\"y\"YYYY')"
        )


_MONTH = _Cadence(label="monthly", parents=tuple(_MONTHLY), argument="month_start")
_YEAR = _Cadence(label="yearly", parents=tuple(_YEARLY), argument="year_start")


def _partition_preamble(name: str, cadence: _Cadence) -> str:
    """The guards every partition function runs before touching anything.

    Resolves the parent (must be a partitioned table AND one of this cadence's
    parents — never the other cadence's, so a yearly child can never land on
    the monthly daily fact), then derives the child name from the date, which
    is why a DEFAULT partition can never be addressed through these functions.
    """
    parents = ", ".join(f"'{parent}'" for parent in cadence.parents)
    return f"""
            SELECT n.nspname, c.relname, pg_catalog.pg_get_userbyid(c.relowner)
              INTO parent_schema, parent_name, parent_owner
              FROM pg_catalog.pg_class AS c
              JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
             WHERE c.oid = parent AND c.relkind = 'p';
            IF parent_name IS NULL THEN
                RAISE EXCEPTION '{name}: % is not a partitioned table', parent
                    USING ERRCODE = 'wrong_object_type';
            END IF;
            IF parent_name <> ALL (ARRAY[{parents}]) THEN
                RAISE EXCEPTION '{name}: % is not a {cadence.label}-partitioned BI table', parent
                    USING ERRCODE = 'wrong_object_type';
            END IF;
            child_name := parent_name || '_' || {cadence.suffix};
            child := to_regclass(format('%I.%I', parent_schema, child_name));
    """


def _function_header(name: str, cadence: _Cadence, *, returns: str) -> str:
    """``SECURITY DEFINER`` with ``search_path`` pinned to ``pg_catalog`` (every
    object is schema-qualified) and ``timezone`` pinned to UTC, so a
    ``timestamptz`` partition bound never takes the caller's offset."""
    return f"""
        CREATE OR REPLACE FUNCTION {name}(parent regclass, {cadence.argument} date)
        RETURNS {returns}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET timezone = 'UTC'
        AS $$
        DECLARE
            lower_bound date := {cadence.lower};
            upper_bound date := {cadence.upper};
            parent_schema text;
            parent_name text;
            parent_owner text;
            child_name text;
            child regclass;
    """


def _ensure_partition_function_ddl(name: str, cadence: _Cadence) -> str:
    """Create the cadence's child for a date, with its RLS, idempotently."""
    tenant_expr = _TENANT_ID_EXPR.replace("'", "''")
    return f"""
        {_function_header(name, cadence, returns="regclass")}
            policy record;
            privilege text;
            role_name text;
        BEGIN
            {_partition_preamble(name, cadence)}
            IF child IS NOT NULL THEN
                RETURN child;
            END IF;
            EXECUTE format(
                'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                parent_schema, child_name, parent_schema, parent_name, lower_bound, upper_bound
            );
            EXECUTE format(
                'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', parent_schema, child_name
            );
            EXECUTE format(
                'ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', parent_schema, child_name
            );
            EXECUTE format(
                'CREATE POLICY %I ON %I.%I FOR ALL '
                'USING ((organization_id)::text = {tenant_expr}) '
                'WITH CHECK ((organization_id)::text = {tenant_expr})',
                child_name || '_tenant_isolation', parent_schema, child_name
            );
            -- A parent's RESTRICTIVE policies (append-only) travel with it.
            FOR policy IN
                SELECT p.polname, p.polcmd,
                       pg_catalog.pg_get_expr(p.polqual, p.polrelid) AS qual,
                       pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid) AS with_check
                  FROM pg_catalog.pg_policy AS p
                 WHERE p.polrelid = parent AND NOT p.polpermissive AND p.polroles = '{{0}}'
            LOOP
                EXECUTE format(
                    'CREATE POLICY %I ON %I.%I AS RESTRICTIVE FOR %s TO PUBLIC USING (%s)%s',
                    child_name || substr(policy.polname, length(parent_name) + 1),
                    parent_schema, child_name,
                    CASE policy.polcmd
                        WHEN 'r' THEN 'SELECT' WHEN 'a' THEN 'INSERT'
                        WHEN 'w' THEN 'UPDATE' WHEN 'd' THEN 'DELETE' ELSE 'ALL'
                    END,
                    coalesce(policy.qual, 'true'),
                    CASE WHEN policy.with_check IS NULL THEN ''
                         ELSE ' WITH CHECK (' || policy.with_check || ')' END
                );
            END LOOP;
            -- So do the parent's revoked privileges: a privilege its owner no
            -- longer holds on the parent is revoked on the child from every
            -- non-superuser role, so a statement addressed to the child is
            -- refused exactly as one addressed to the parent is.
            FOREACH privilege IN ARRAY ARRAY['UPDATE', 'DELETE', 'TRUNCATE'] LOOP
                IF NOT pg_catalog.has_table_privilege(parent_owner, parent, privilege) THEN
                    EXECUTE format(
                        'REVOKE %s ON %I.%I FROM PUBLIC', privilege, parent_schema, child_name
                    );
                    FOR role_name IN
                        SELECT r.rolname FROM pg_catalog.pg_roles AS r
                         WHERE r.rolname NOT LIKE 'pg\\_%' AND NOT r.rolsuper
                    LOOP
                        EXECUTE format(
                            'REVOKE %s ON %I.%I FROM %I',
                            privilege, parent_schema, child_name, role_name
                        );
                    END LOOP;
                END IF;
            END LOOP;
            RETURN to_regclass(format('%I.%I', parent_schema, child_name));
        END
        $$
    """


def _drop_partition_function_ddl(name: str, cadence: _Cadence) -> str:
    """Drop the cadence's child for a date (retention); ``false`` when absent.

    The child is dropped only if it really is a partition of that parent —
    a same-named table that is not attached is left alone — and the DEFAULT
    partition is unreachable because the name is derived from the date.
    """
    return f"""
        {_function_header(name, cadence, returns="boolean")}
        BEGIN
            {_partition_preamble(name, cadence)}
            IF child IS NULL THEN
                RETURN false;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_inherits AS i
                 WHERE i.inhrelid = child AND i.inhparent = parent
            ) THEN
                RAISE EXCEPTION '{name}: % is not a partition of %', child, parent
                    USING ERRCODE = 'wrong_object_type';
            END IF;
            EXECUTE format('DROP TABLE %I.%I', parent_schema, child_name);
            RETURN true;
        END
        $$
    """


def _create_partition_functions() -> None:
    op.execute(_ensure_partition_function_ddl("bi_ensure_month_partition", _MONTH))
    op.execute(_ensure_partition_function_ddl("bi_ensure_year_partition", _YEAR))
    op.execute(_drop_partition_function_ddl("bi_drop_month_partition", _MONTH))
    op.execute(_drop_partition_function_ddl("bi_drop_year_partition", _YEAR))
    for signature in _PARTITION_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO CURRENT_USER")
        # The cross-tenant worker role is the caller in production. No
        # migration knows it by name; BYPASSRLS is what defines it.
        op.execute(
            f"""
            DO $$
            DECLARE r record;
            BEGIN
                FOR r IN SELECT rolname FROM pg_roles
                         WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
                           AND rolbypassrls AND rolcanlogin
                LOOP
                    EXECUTE format('GRANT EXECUTE ON FUNCTION {signature} TO %I', r.rolname);
                END LOOP;
            END $$
            """
        )


def _harden_child(child: str) -> None:
    """The DEFAULT partition of the query log carries the parent's denial too."""
    _revoke(child, "UPDATE, DELETE, TRUNCATE")
    _restrictive_append_only_policies(child)


def _create_default_partitions() -> None:
    for table in _PARTITIONED:
        child = f"{table}_default"
        op.execute(f"CREATE TABLE {child} PARTITION OF {table} DEFAULT")
        _enable_rls(child)
    _harden_child(f"{_QUERY_LOG}_default")


def upgrade() -> None:
    _create_position_fact("bi_fact_position_daily", partition_by="RANGE (as_of_date)")
    _create_position_fact("bi_fact_position_eom", partition_by="RANGE (as_of_date)")
    _create_agg_position_daily()
    _create_loan_event()
    _create_gl_monthly()
    _create_engine_metric()
    _create_dimensions()
    _create_control()

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        _enable_rls(table)
    _install_append_only(_QUERY_LOG)
    _create_partition_functions()
    _create_default_partitions()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for signature in _PARTITION_FUNCTIONS:
            op.execute(f"DROP FUNCTION IF EXISTS {signature}")
        op.execute(f"DROP TRIGGER IF EXISTS {_QUERY_LOG}_append_only ON {_QUERY_LOG}")
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    # Dropping a partitioned parent drops every partition with it, DEFAULT and
    # runtime-created children included.
    for table in reversed(_TABLES):
        op.drop_table(table)
