"""BI marts, conformed dimensions and control tables (the ``bi_*`` plane).

These tables are the DISPATCH plane's own storage: the mart builder
(``app/services/bi/mart_builder.py``, job ``bi_mart_refresh``) reads the
canonical book, ``live_metrics``, ``regulatory_runs`` and the governed
registers with ``record=False``, and writes ONLY ``bi_*`` tables. Nothing in
``app/domain/bi`` or ``app/services/bi`` may write a canonical, regulatory or
live table, and no regulatory code may import this module. The pure half of
that boundary is pinned today by ``tests/architecture/test_bi_catalogue_authority.py``
(``test_bi_domain_imports_no_service_model_or_sql``) and the generic
``test_dependency_boundaries.py::test_the_pure_domain_layer_imports_no_application_state``;
the "regulatory code never imports BI" half is the guards wave's
``test_bi_plane_boundary.py``. Every number here is a COPY of something the
platform already computed (engine metrics) or a typed projection of canonical
rows (position facts); the builder never calls ``derive_facts`` and never
recomputes an engine result.

Column contract: ``.ai/bi_contracts.md`` (binding). Naming: ``_rc`` is the
reporting currency resolved through ``jurisdictions.base_currency(bank)``,
never a currency literal; ``_native`` is the position's own currency. Money is
``Numeric(28, 6)`` like the canonical snapshot it is projected from.

House rules shared with every tenant table: ``organization_id`` and
``bank_id`` are ``String(16)`` platform ids with the composite foreign key to
``banks``; JSON columns are ``sa.JSON``, never JSONB, because the hermetic
suite and the Playwright stack build this schema with ``create_all`` on
SQLite; CHECK constraints derive from the tuples below so the model and the
database can never disagree about a vocabulary. Every table the BUILDER
writes carries ``builder_version``; the marts and dimensions also carry
``built_at``, while the two build-control tables keep their own timestamps
(a running build has no ``built_at`` yet) and ``bi_query_log`` — written by
the query path, not the builder — records the build fingerprint it ran
against instead.

**Partitioning is migration-owned (D-011).** ``bi_fact_position_daily``,
``bi_agg_position_daily``, ``bi_fact_loan_event`` and ``bi_query_log`` are
``PARTITION BY RANGE`` monthly on Postgres, ``bi_fact_position_eom`` yearly,
each with a DEFAULT partition, and every partition — parent, DEFAULT and the
children ``bi_ensure_month_partition`` / ``bi_ensure_year_partition`` create
at runtime — is ENABLE + FORCE ROW LEVEL SECURITY under the standard tenant
policy (migration ``202609220066``). The ORM deliberately declares PLAIN
tables and carries no ``postgresql_partition_by``: SQLAlchemy would emit
nothing for SQLite, but a Postgres ``create_all`` (the ``TEST_DATABASE_URL``
conftest path) would create a parent with zero partitions that rejects every
INSERT, and the fix for that is partition DDL in the model module — a second
owner of what the migration already owns. Partition routing and partition RLS
are therefore proven only in the Postgres-gated suite (``tests/db/``), never
mocked as passing on SQLite.

Row-level security lives in the migration too, like every other tenant
table; the Postgres census ``tests/db/test_tenant_rls_completeness.py``
enumerates partitioned parents and their children alike.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.authorization import PrincipalType
from app.db.base import Base, UuidV4PrimaryKeyMixin, utc_now

# --- vocabularies (the CHECK constraints derive from these) -------------------

#: How a loan event was attributed to a position (D-018). Exact natural-key
#: match with a snapshot on or before the event date; a matching position whose
#: snapshot on/before that date does not exist; or no position in that source
#: system at all. Never a cross-system guess.
LOAN_EVENT_ATTRIBUTION_BASES: tuple[str, ...] = (
    "snapshot_on_or_before",
    "no_snapshot",
    "unmatched",
)

#: ``bi_fact_engine_metric.tier``: copied from ``live_metrics`` or from the
#: latest succeeded baseline ``regulatory_runs`` per reporting period.
ENGINE_METRIC_TIERS: tuple[str, ...] = ("live", "official")

#: ``bi_fact_gl_monthly.balance_basis`` — BSD7's own vocabulary, carried
#: through ``app/domain/gl/pl_mapping.py`` (D-021).
GL_BALANCE_BASES: tuple[str, ...] = ("ytd", "period")

#: One ``bi_mart_builds`` row per (bank, as-of, scope).
MART_BUILD_SCOPES: tuple[str, ...] = ("positions", "events", "gl", "engine", "dims")
#: There is deliberately no ``skipped``: a fingerprint skip returns BEFORE the
#: build is marked running, so it leaves the previous ``succeeded`` row — whose
#: fingerprint equals the current one, which is what makes the skip idempotent
#: and preserves that row's timings and row counts as the evidence of what the
#: mart actually holds. A never-attempted slice has no row at all. The skip is
#: already recorded per attempt on ``jobs.progress``; this is a STATE table.
MART_BUILD_STATUSES: tuple[str, ...] = ("running", "succeeded", "failed")

#: Reconciliation checks R1–R10 (architecture §Reconciliation → trust) and the
#: trust colours they resolve to. ``grey`` is "not assessed", never green.
#: R10 (``dpd_completeness``, D-042) is the share of LOAN rows with no
#: ``dpd_band``, so a badge can never read green over a "we were never told"
#: figure — it must be storable or the stored badge disagrees with the live one.
RECONCILIATION_CHECK_IDS: tuple[str, ...] = tuple(f"R{number}" for number in range(1, 11))
RECONCILIATION_STATUSES: tuple[str, ...] = ("green", "amber", "red", "grey")

#: ``bi_query_log`` vocabularies. ``trust`` and ``catalogue`` are read surfaces
#: that return no mart rows, and they are named here for one reason: the read
#: budget is counted over THIS table, so a surface with no value of its own
#: either goes unmetered or gets recorded as something it is not — and putting
#: an event in an append-only audit table that did not happen is worse than the
#: missing limit. With the values admitted, the routes log what they did (A6-06).
QUERY_LOG_SURFACES: tuple[str, ...] = (
    "query",
    "grid",
    "drill",
    "explain",
    "export",
    "feed",
    "trust",
    "catalogue",
)
QUERY_LOG_DECISIONS: tuple[str, ...] = ("allowed", "denied")
QUERY_LOG_PRINCIPAL_TYPES: tuple[str, ...] = tuple(kind.value for kind in PrincipalType)

#: ``bi_dim_branch`` defaults (D-020): region is declared through the optional
#: ``business_units.region`` field and never inferred, and a branch code the
#: register does not know is still a row so the fact it labels is not lost.
UNASSIGNED_REGION = "Unassigned region"
UNMAPPED_BRANCH_NAME = "Unmapped branch"


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _bank_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
    )


class _BuilderStamp:
    """The mart builder's provenance on every row it writes."""

    #: ``mart_builder.BUILDER_VERSION`` that wrote the row; a handler older
    #: than a job payload skips the job rather than downgrade a mart (P1-B5).
    builder_version: Mapped[int] = mapped_column(Integer, nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class _TenantKeys:
    """Tenant keys for tables whose primary key is not the natural key."""

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)


class _PositionFactColumns(_TenantKeys, _BuilderStamp):
    """The typed position projection shared by the daily and month-end facts.

    One row per canonical position snapshot. ``balance_rc`` is NULL when the
    position's currency is not the reporting currency and no converted value
    was supplied (``fx_unconverted`` counts it); ``classification_exposure_rc``
    is the same value under the CLASSIFICATION rule — ``0`` when unconverted,
    NULL for non-loan types — because R1 reconciles it to the engine's NPL
    while R2/R3 reconcile ``balance_rc`` to the balance-sheet facts (D-015).
    """

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    #: ``CanonicalPositionSnapshot.id``.
    snapshot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    position_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    position_type: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_native: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    balance_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    fx_unconverted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    classification_exposure_rc: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 6), nullable=True
    )
    notional_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    rate_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rate_index: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contractual_maturity: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_repricing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Lifted ladder / repricing buckets (P0-8) — the engines' own labels.
    maturity_bucket: Mapped[str | None] = mapped_column(String(40), nullable=True)
    repricing_bucket: Mapped[str | None] = mapped_column(String(40), nullable=True)
    origination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: First day of the origination month.
    vintage_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    days_past_due: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dpd_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ifrs9_stage: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    #: From ``classified_loans(..., record=False)`` — LOAN positions only.
    grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    non_performing: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    classification_basis: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provision_required_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    provision_held_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    interest_in_suspense_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    collateral_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    collateral_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    restructured: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    deposit_account_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    behavioral_maturity_months: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    encumbered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hqla_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: ``attributes.branch_id`` verbatim; ``bi_dim_branch`` resolves it.
    branch_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    product_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    product_family: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: A mapped category, or ``unclassified_<slug of the 80-char regulatory
    #: category>`` for an unrecognised one — hence wider than the category.
    exposure_category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    counterparty_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    counterparty_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    counterparty_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True)
    employer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gl_account_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ingestion_batch_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


def _position_fact_table_args(table: str) -> tuple:
    return (
        CheckConstraint(
            "ifrs9_stage IS NULL OR ifrs9_stage IN (1, 2, 3)",
            name=f"ck_{table}_ifrs9_stage",
        ),
        _bank_fk(),
        Index(
            f"ix_{table}_org_bank_as_of_type",
            "organization_id",
            "bank_id",
            "as_of_date",
            "position_type",
        ),
        Index(
            f"ix_{table}_org_bank_as_of_branch",
            "organization_id",
            "bank_id",
            "as_of_date",
            "branch_code",
        ),
        Index(
            f"ix_{table}_org_bank_position_as_of",
            "organization_id",
            "bank_id",
            "position_id",
            "as_of_date",
        ),
    )


class BiFactPositionDaily(_PositionFactColumns, Base):
    """One typed row per canonical position snapshot per business date.

    Rebuilt as a whole ``(bank, as_of_date)`` slice in one transaction by the
    mart builder; monthly partitions on Postgres, of which only the last
    ``BI_DAILY_RETENTION_DAYS`` are kept (``bi_retention``).
    """

    __tablename__ = "bi_fact_position_daily"
    __table_args__ = _position_fact_table_args("bi_fact_position_daily")


class BiFactPositionEom(_PositionFactColumns, Base):
    """The same projection kept forever, one row per position per month-end.

    "Month-end" is the last ``as_of_date`` with data in each calendar month
    (D-014), not the calendar's last day, so a bank that feeds last-business-day
    books still gets one point per month. Yearly partitions on Postgres.
    """

    __tablename__ = "bi_fact_position_eom"
    __table_args__ = _position_fact_table_args("bi_fact_position_eom")


class BiAggPositionDaily(_TenantKeys, _BuilderStamp, Base):
    """Additive daily aggregates at a coarse grain — never distinct counts.

    The compiler selects this table when every requested member is in the
    grain; ratios are ``sum(numerator) / nullif(sum(denominator), 0)`` over it.
    Grain columns may be NULL (the fact carries NULL), so the unique index
    below coalesces them.
    """

    __tablename__ = "bi_agg_position_daily"
    __table_args__ = (
        CheckConstraint(
            "ifrs9_stage IS NULL OR ifrs9_stage IN (1, 2, 3)",
            name="ck_bi_agg_position_daily_ifrs9_stage",
        ),
        CheckConstraint("row_count >= 0", name="ck_bi_agg_position_daily_row_count"),
        CheckConstraint(
            "fx_unconverted_count >= 0", name="ck_bi_agg_position_daily_fx_unconverted_count"
        ),
        _bank_fk(),
    )

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    position_type: Mapped[str] = mapped_column(String(32), nullable=False)
    product_family: Mapped[str | None] = mapped_column(String(40), nullable=True)
    branch_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    ifrs9_stage: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    dpd_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deposit_account_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_rc_sum: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    classification_exposure_rc_sum: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    non_performing_exposure_rc_sum: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    provision_required_rc_sum: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    provision_held_rc_sum: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    collateral_rc_sum: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    #: ``sum(interest_rate * balance_rc)`` — the numerator of a weighted average.
    rate_x_balance_rc_sum: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    fx_unconverted_count: Mapped[int] = mapped_column(Integer, nullable=False)


#: The grain, with NULLs coalesced (``ifrs9_stage`` is 1..3, so ``0`` is the
#: integer stand-in for ``''``). ``as_of_date`` is in it, which is what a unique
#: index on a range-partitioned table requires.
Index(
    "uq_bi_agg_position_daily_grain",
    BiAggPositionDaily.organization_id,
    BiAggPositionDaily.bank_id,
    BiAggPositionDaily.as_of_date,
    BiAggPositionDaily.position_type,
    func.coalesce(BiAggPositionDaily.product_family, ""),
    func.coalesce(BiAggPositionDaily.branch_code, ""),
    BiAggPositionDaily.currency,
    func.coalesce(BiAggPositionDaily.ifrs9_stage, 0),
    func.coalesce(BiAggPositionDaily.dpd_band, ""),
    func.coalesce(BiAggPositionDaily.grade, ""),
    func.coalesce(BiAggPositionDaily.deposit_account_type, ""),
    unique=True,
)


class BiFactLoanEvent(_TenantKeys, _BuilderStamp, Base):
    """One row per canonical loan event, attributed to a position (D-018)."""

    __tablename__ = "bi_fact_loan_event"
    __table_args__ = (
        CheckConstraint(
            f"attribution_basis IN ({_values(LOAN_EVENT_ATTRIBUTION_BASES)})",
            name="ck_bi_fact_loan_event_attribution_basis",
        ),
        _bank_fk(),
        Index("ix_bi_fact_loan_event_org_bank_date", "organization_id", "bank_id", "event_date"),
    )

    event_date: Mapped[date] = mapped_column(Date, primary_key=True)
    #: ``CanonicalLoanEvent.id``.
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    event_subtype: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    position_source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    position_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    amount_native: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    fx_unconverted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attribution_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    branch_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    product_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    product_family: Mapped[str | None] = mapped_column(String(40), nullable=True)
    counterparty_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True)


class BiFactGlMonthly(_BuilderStamp, Base):
    """Monthly GL balances and movements mapped to neutral P&L lines.

    Movement is YTD minus the prior month's YTD through BSD7's own logic in
    ``app/domain/gl/pl_mapping.py`` (D-021); a missing prior month gives NULL
    and ``missing_prior``. ``currency`` is ``''`` for the base currency so it
    can sit in the primary key.
    """

    __tablename__ = "bi_fact_gl_monthly"
    __table_args__ = (
        CheckConstraint(
            f"balance_basis IN ({_values(GL_BALANCE_BASES)})",
            name="ck_bi_fact_gl_monthly_balance_basis",
        ),
        _bank_fk(),
    )

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    #: The last GL date with data in the month.
    month_end: Mapped[date] = mapped_column(Date, primary_key=True)
    gl_account_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    #: First day of the calendar month ``month_end`` falls in.
    calendar_month: Mapped[date] = mapped_column(Date, nullable=False)
    account_class: Mapped[str] = mapped_column(String(16), nullable=False)
    ytd_rc: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    prior_ytd_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    movement_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    missing_prior: Mapped[bool] = mapped_column(Boolean, nullable=False)
    balance_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    pl_line: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: The mapping's sign for the account (+1 / -1), NULL when unmapped.
    pl_sign: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class BiFactEngineMetric(_BuilderStamp, Base):
    """A typed COPY of an engine metric — never recomputed.

    ``live`` rows come from ``live_metrics``; ``official`` rows from the latest
    succeeded baseline ``regulatory_runs`` joined to
    ``bank_reporting_periods.period_end``. The row carries the run's own
    ``input_hash``, ``pipeline_state`` and reconciliation-blocked flag so a
    dashboard can say exactly which computation it is quoting, and
    ``advisory_designation`` from the authority registry so an advisory metric
    is never badged certified (D-022).
    """

    __tablename__ = "bi_fact_engine_metric"
    __table_args__ = (
        CheckConstraint(
            f"tier IN ({_values(ENGINE_METRIC_TIERS)})", name="ck_bi_fact_engine_metric_tier"
        ),
        _bank_fk(),
    )

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    module: Mapped[str] = mapped_column(String(16), primary_key=True)
    metric_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tier: Mapped[str] = mapped_column(String(8), primary_key=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: ``live_metrics.status`` (green/amber/red/na) or ``regulatory_runs.status``
    #: (``succeeded``…), so it is as wide as the wider source.
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    regime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    institution_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    advisory_designation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pipeline_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reconciliation_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reporting_period_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- conformed dimensions (Type 1, natural key + org + bank) -------------------


class BiDimBranch(_BuilderStamp, Base):
    """Branches: ``business_units`` matched to ``outlets``.

    ``mapped`` is False for a code the register does not know; the row then
    carries ``UNMAPPED_BRANCH_NAME`` so the facts it labels stay visible and
    R7 can count them. ``region`` defaults to ``UNASSIGNED_REGION`` until the
    bank declares one (D-020).
    """

    __tablename__ = "bi_dim_branch"
    __table_args__ = (_bank_fk(),)

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    branch_code: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(
        String(120),
        default=UNASSIGNED_REGION,
        server_default=sql_text(f"'{UNASSIGNED_REGION}'"),
        nullable=False,
    )
    outlet_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    outlet_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mapped: Mapped[bool] = mapped_column(Boolean, nullable=False)


class BiDimProduct(_BuilderStamp, Base):
    __tablename__ = "bi_dim_product"
    __table_args__ = (_bank_fk(),)

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    product_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_family: Mapped[str | None] = mapped_column(String(40), nullable=True)
    regulatory_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    risk_weight_code: Mapped[str | None] = mapped_column(String(16), nullable=True)


class BiDimCounterparty(_BuilderStamp, Base):
    """Counterparties. ``name`` is a RESTRICTED member in the catalogue (D-028)."""

    __tablename__ = "bi_dim_counterparty"
    __table_args__ = (_bank_fk(),)

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    counterparty_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    counterparty_type: Mapped[str] = mapped_column(String(32), nullable=False)
    group_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    rating: Mapped[str | None] = mapped_column(String(16), nullable=True)


class BiDimGlAccount(_BuilderStamp, Base):
    __tablename__ = "bi_dim_gl_account"
    __table_args__ = (_bank_fk(),)

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_class: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_account_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pl_line: Mapped[str | None] = mapped_column(String(80), nullable=True)


class BiDimDate(_BuilderStamp, Base):
    """Per-bank calendar with the "last date with data" flags.

    The flags fix the semi-additive month/quarter/year grain (D-014): a stock
    measure at month grain reads the row where ``is_last_in_month`` is true,
    which is the last business date the bank actually fed, not the calendar's
    last day. Fiscal periods follow BSD7's ``fiscal_year_start``.
    """

    __tablename__ = "bi_dim_date"
    __table_args__ = (_bank_fk(),)

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    has_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_last_in_month: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_last_in_quarter: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_last_in_year: Mapped[bool] = mapped_column(Boolean, nullable=False)
    calendar_month: Mapped[date] = mapped_column(Date, nullable=False)
    calendar_quarter: Mapped[date] = mapped_column(Date, nullable=False)
    calendar_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)


# --- control -----------------------------------------------------------------


class BiMartBuild(UuidV4PrimaryKeyMixin, _TenantKeys, Base):
    """One build record per (bank, as-of, scope) with its value-based fingerprint.

    ``fingerprint`` is computed over the canonical inputs, the live/official
    metric hashes, ``BUILDER_VERSION`` and ``CATALOGUE_VERSION``; an unchanged
    fingerprint skips the rebuild.
    """

    __tablename__ = "bi_mart_builds"
    __table_args__ = (
        CheckConstraint(f"scope IN ({_values(MART_BUILD_SCOPES)})", name="ck_bi_mart_builds_scope"),
        CheckConstraint(
            f"status IN ({_values(MART_BUILD_STATUSES)})", name="ck_bi_mart_builds_status"
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_bi_mart_builds_finished_after_started",
        ),
        _bank_fk(),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "as_of_date",
            "scope",
            name="uq_bi_mart_builds_bank_as_of_scope",
        ),
    )

    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    builder_version: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_counts: Mapped[dict[str, int]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BiReconciliationResult(UuidV4PrimaryKeyMixin, _TenantKeys, Base):
    """The R1–R10 outcome for a (bank, as-of) that the trust badge is read from."""

    __tablename__ = "bi_reconciliation_results"
    __table_args__ = (
        CheckConstraint(
            f"check_id IN ({_values(RECONCILIATION_CHECK_IDS)})",
            name="ck_bi_reconciliation_results_check_id",
        ),
        CheckConstraint(
            f"status IN ({_values(RECONCILIATION_STATUSES)})",
            name="ck_bi_reconciliation_results_status",
        ),
        _bank_fk(),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "as_of_date",
            "check_id",
            name="uq_bi_reconciliation_results_bank_as_of_check",
        ),
    )

    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_id: Mapped[str] = mapped_column(String(4), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    lhs: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    rhs: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    difference: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    tolerance: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    builder_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BiQueryLog(_TenantKeys, Base):
    """Append-only record of every BI read decision.

    Written by the query path (not the builder), so it carries the catalogue
    version and the mart build fingerprint the query ran against rather than
    a builder stamp. It never stores filter VALUES for restricted members —
    only member ids and the allow/deny decision. On Postgres the table is
    monthly partitioned on ``queried_at`` and append-only three ways (trigger,
    revoked UPDATE/DELETE/TRUNCATE, RESTRICTIVE policies), exactly like
    ``audit_events`` (migration ``202607250027``).
    """

    __tablename__ = "bi_query_log"
    __table_args__ = (
        CheckConstraint(
            f"principal_type IN ({_values(QUERY_LOG_PRINCIPAL_TYPES)})",
            name="ck_bi_query_log_principal_type",
        ),
        CheckConstraint(
            f"surface IN ({_values(QUERY_LOG_SURFACES)})", name="ck_bi_query_log_surface"
        ),
        CheckConstraint(
            f"decision IN ({_values(QUERY_LOG_DECISIONS)})", name="ck_bi_query_log_decision"
        ),
        CheckConstraint("row_count IS NULL OR row_count >= 0", name="ck_bi_query_log_row_count"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_bi_query_log_duration_ms"
        ),
        _bank_fk(),
        Index("ix_bi_query_log_org_bank_queried_at", "organization_id", "bank_id", "queried_at"),
        Index(
            "ix_bi_query_log_org_principal_queried_at",
            "organization_id",
            "principal_user_id",
            "queried_at",
        ),
    )

    queried_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, default=utc_now
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    principal_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    surface: Mapped[str] = mapped_column(String(16), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    member_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(8), nullable=False)
    denied_members: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    catalogue_version: Mapped[str] = mapped_column(String(40), nullable=False)
    build_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)


#: The partitioned parents (Postgres only; migration ``202609220066``) and the
#: column each ranges on. The builder calls ``bi_ensure_month_partition`` /
#: ``bi_ensure_year_partition`` for these before writing a slice, and the
#: retention job drops old monthly children of the daily fact.
MONTHLY_PARTITIONED_TABLES: dict[str, str] = {
    BiFactPositionDaily.__tablename__: "as_of_date",
    BiAggPositionDaily.__tablename__: "as_of_date",
    BiFactLoanEvent.__tablename__: "event_date",
    BiQueryLog.__tablename__: "queried_at",
}
YEARLY_PARTITIONED_TABLES: dict[str, str] = {
    BiFactPositionEom.__tablename__: "as_of_date",
}

#: Every ``bi_*`` table, in creation order. The migration and the hermetic
#: model test both read this so a table added to one cannot be forgotten by the
#: other.
BI_TABLES: tuple[str, ...] = (
    BiFactPositionDaily.__tablename__,
    BiFactPositionEom.__tablename__,
    BiAggPositionDaily.__tablename__,
    BiFactLoanEvent.__tablename__,
    BiFactGlMonthly.__tablename__,
    BiFactEngineMetric.__tablename__,
    BiDimBranch.__tablename__,
    BiDimProduct.__tablename__,
    BiDimCounterparty.__tablename__,
    BiDimGlAccount.__tablename__,
    BiDimDate.__tablename__,
    BiMartBuild.__tablename__,
    BiReconciliationResult.__tablename__,
    BiQueryLog.__tablename__,
)
