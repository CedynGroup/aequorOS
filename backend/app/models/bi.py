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

#: ``bi_fact_target`` vocabularies, mirroring
#: ``app/domain/ingestion/reference_schemas/performance_targets.py`` without
#: importing it (``app.models`` stays free of ``app.domain.ingestion``);
#: ``tests/models/test_bi_models.py`` asserts the parity in both
#: directions, so a grain or version added to the register cannot be stored
#: under a CHECK that does not know it.
TARGET_PERIOD_GRAINS: tuple[str, ...] = ("month", "quarter", "half_year", "year")
TARGET_VERSIONS: tuple[str, ...] = ("budget", "reforecast")
TARGET_TIME_BEHAVIOURS: tuple[str, ...] = ("stock", "flow")
#: Which target the builder matched to the row's scope (D-064). ``exact`` is a
#: target the bank declared for exactly this scope; ``bank_wide`` is the
#: bank-wide target applied because no scoped one was declared. The two are
#: never summed, and the row says which was used so the number is auditable
#: without re-reading the register.
TARGET_SCOPE_BASES: tuple[str, ...] = ("exact", "bank_wide")
#: How wide a target's scope value may be, and therefore what the register
#: accepts. It equals the WIDEST column a scope value can be copied from
#: (``counterparty.group`` and ``loan.employer``, both ``String(255)``); a
#: narrower mart column turns one long register row into a nightly total build
#: failure for that tenant (audit A8-01). Asserted against the catalogue's
#: scopeable dimensions in ``tests/models/test_bi_models.py``, so a new wider
#: dimension fails there rather than in a tenant's build.
TARGET_SCOPE_VALUE_WIDTH = 255
#: ``bi_fact_target.scope_dimension`` / ``scope_value`` for a bank-wide target.
#: The register names NO scope for one (never a sentinel); the mart needs a
#: value it can put in a primary key, so the empty string is that key — the
#: same convention ``bi_fact_gl_monthly.currency`` already uses.
TARGET_BANK_WIDE_SCOPE = ""

#: One ``bi_mart_builds`` row per (bank, as-of, scope).
MART_BUILD_SCOPES: tuple[str, ...] = ("positions", "events", "gl", "engine", "dims", "targets")
#: There is deliberately no ``skipped``: a fingerprint skip returns BEFORE the
#: build is marked running, so it leaves the previous ``succeeded`` row — whose
#: fingerprint equals the current one, which is what makes the skip idempotent
#: and preserves that row's timings and row counts as the evidence of what the
#: mart actually holds. A never-attempted slice has no row at all. The skip is
#: already recorded per attempt on ``jobs.progress``; this is a STATE table.
MART_BUILD_STATUSES: tuple[str, ...] = ("running", "succeeded", "failed")

#: Reconciliation checks R1–R12 (architecture §Reconciliation → trust) and the
#: trust colours they resolve to. ``grey`` is "not assessed", never green.
#: R10 (``dpd_completeness``, D-042) is the share of LOAN rows with no
#: ``dpd_band``, so a badge can never read green over a "we were never told"
#: figure — it must be storable or the stored badge disagrees with the live one.
#: R11 (P5-B) is the general-ledger-by-branch identity: the branch breakdown
#: must sum to the institution ledger it breaks down; admitted to the stored
#: vocabulary by migration ``202609280075``. R12 (P5-A) is the arrears
#: completeness share, R10's twin for ``arrears_amount_rc``: a bank that states
#: arrears for half its book would otherwise show a sum that reads as the whole
#: book's; admitted by ``202609280076``. Until a migration lands, a Postgres
#: ``ck_bi_reconciliation_results_check_id`` built by an earlier one refuses the
#: row and ``reconciliation.persist`` logs the skip.
RECONCILIATION_CHECK_IDS: tuple[str, ...] = tuple(f"R{number}" for number in range(1, 13))
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
    "packs",
    "insights",
    #: A question a reader typed, translated into a ``BiQuery`` by a model and
    #: confirmed by them before it ran. Its own value rather than ``catalogue``,
    #: which is what the read technically is, because the one question an auditor
    #: asks first about a model-assisted surface is which reads came from a model
    #: proposing a query rather than a person composing one. Admitted to the
    #: database CHECK by ``202609280077``.
    "nlq",
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
    #: ``attributes.officer_id`` verbatim — the bank's own code for the officer
    #: who owns the account. No name is resolved: the platform holds no officer
    #: register, and inventing one would be seeded data. Width is
    #: ``optional_position_fields.OFFICER_ID_MAX_LENGTH``, itself ``branch_code``'s,
    #: and ingestion refuses anything longer, so the two cannot disagree (A8-01).
    officer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: ``attributes.channel`` / ``attributes.account_status``, already resolved to
    #: one spelling at ingestion (``domain.ingestion.optional_position_fields``),
    #: so the mart never holds two spellings of one channel. Widths exceed the
    #: longest value of each vocabulary; a parity test asserts that relationship
    #: rather than restating the numbers.
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    account_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: ``attributes.arrears_amount`` in the REPORTING currency, under the
    #: DERIVATION rule (D-015): NULL when the position's currency is not the
    #: reporting currency, exactly like ``balance_rc``, and NULL when the bank
    #: stated no arrears. Never 0 for either reason — a zero would assert a
    #: performing facility. R12 is what stops a partial book reading as a whole one.
    arrears_amount_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
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
        CheckConstraint(
            "arrears_amount_rc IS NULL OR arrears_amount_rc >= 0",
            name=f"ck_{table}_arrears_amount_rc",
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

    **A per-cell sum is NULL when no row in the cell carries the value** (audit
    A360 H1). Each ``*_sum`` is ``SUM(column)`` over the cell's fact rows, and SQL's
    ``SUM`` of nothing but NULLs is NULL — so a cell whose whole book is
    unconverted foreign currency has ``balance_rc_sum IS NULL``, exactly as
    ``SUM(balance_rc)`` over the same rows would, and a bank that states no
    collateral has ``collateral_rc_sum IS NULL`` rather than a collateral of 0.
    Storing 0 there made the aggregate path answer a no-dimension KPI with a
    measured zero while the fact path answered NULL for the same rows, and every
    downstream consumer (alerts, target attainment, insights, the dashboard's
    needs-data check) then treated the zero as a figure. Only the two COUNTS
    (``row_count``, ``fx_unconverted_count``) are NOT NULL: a cell exists only
    because rows do, so its row count is never absent.
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
    balance_rc_sum: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    classification_exposure_rc_sum: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 6), nullable=True
    )
    #: ``sum(CASE WHEN non_performing THEN classification_exposure_rc ELSE 0 END)``:
    #: a performing row contributes 0 (the selection did not hold), so the sum is
    #: NULL only when every row is non-performing with no exposure — the same
    #: rows-to-value rule the compiler's ``_summed`` applies on the fact path.
    non_performing_exposure_rc_sum: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 6), nullable=True
    )
    provision_required_rc_sum: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    provision_held_rc_sum: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    collateral_rc_sum: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    #: ``sum(interest_rate * balance_rc)`` — the numerator of a weighted average;
    #: NULL when no row carries both a rate and a reporting-currency balance.
    rate_x_balance_rc_sum: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
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


class BiFactGlBranchMonthly(_BuilderStamp, Base):
    """The same monthly P&L figures as :class:`BiFactGlMonthly`, by BRANCH.

    Fed by the bank's own ``gl_segment_balances`` register
    (``app/domain/ingestion/reference_schemas/gl_segment_balances.py``), which is
    a dataset rather than a wider ``gl_account`` record because GL-by-branch is a
    different grain from the chart of accounts and
    ``uq_canonical_gl_accounts_current`` forbids the second key there.

    Three properties this table exists to hold, each of which is a way the
    feature goes wrong if it is dropped:

    **It sums to the institution's ledger by construction.** Every (account,
    month, currency) block carries one row per allocated branch PLUS one row on
    ``gl_segment_balances.RESIDUAL_BRANCH_ID`` holding ``institution_ytd − Σ
    reported_ytd``. So Σ over ``branch_code`` is
    :class:`BiFactGlMonthly`'s ``ytd_rc`` exactly, for a complete allocation and
    a partial one alike, and a bank that allocated four fifths of its interest
    income still sees its whole interest income — with the rest on a line that
    says nobody allocated it. Summing only what was allocated would be a board
    figure that silently under-reports by whatever the bank forgot. R11 proves
    the identity; the residual is a computed, labelled figure, the same device
    ``services/reconciliation.py`` uses for a retained balance-sheet plug.

    **It is a SEPARATE table, not a ``branch_code`` column on
    ``bi_fact_gl_monthly``.** ``services/bi/authorization.branch_attributable``
    reads the branch key off the mapped table, so a branch key on the
    institution ledger would make the institution's own P&L readable by a
    branch-scoped principal. Apart is what makes branch P&L visible to a scoped
    reader and the institution's invisible, at no cost. It also keeps the
    FILED-reconciled figure (R4 against BSD7A's own resolver) out of reach of how
    completely a bank allocated its branches.

    **``pl_line`` / ``pl_sign`` / ``balance_basis`` are the INSTITUTION row's.**
    A branch does not get its own mapping: the account's BSD7 line and sign are
    the bank's single statement about that account, so Σ ``pl_sign × ytd_rc`` per
    line over branches is the same line the return files.
    """

    __tablename__ = "bi_fact_gl_branch_monthly"
    __table_args__ = (
        CheckConstraint(
            f"balance_basis IN ({_values(GL_BALANCE_BASES)})",
            name="ck_bi_fact_gl_branch_monthly_balance_basis",
        ),
        Index(
            "ix_bi_fact_gl_branch_monthly_org_bank_month_branch",
            "organization_id",
            "bank_id",
            "month_end",
            "branch_code",
        ),
        _bank_fk(),
    )

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    #: The institution row's ``month_end`` — the last GL date with data in the
    #: month. The branch register batch must fall in the SAME calendar month or
    #: no rows are built at all: pairing March's breakdown with April's ledger
    #: would push a whole month of unattributed movement into the residual and
    #: call it unallocated. ``register_as_of`` records which day inside the month.
    month_end: Mapped[date] = mapped_column(Date, primary_key=True)
    gl_account_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    #: ``gl_segment_balances.branch_id`` verbatim, or
    #: ``gl_segment_balances.RESIDUAL_BRANCH_ID`` on the computed remainder.
    #: ``bi_dim_branch`` resolves both, so the remainder renders with a name.
    #: Width matches ``bi_dim_branch.branch_code`` and the position facts.
    branch_code: Mapped[str] = mapped_column(String(120), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    calendar_month: Mapped[date] = mapped_column(Date, nullable=False)
    account_class: Mapped[str] = mapped_column(String(16), nullable=False)
    ytd_rc: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    #: The branch's own prior-month fiscal-year-to-date balance, NULL when the
    #: prior month's register does not carry this exact (account, branch).
    prior_ytd_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    #: ``ytd_rc − prior_ytd_rc``, NULL whenever ``missing_prior``. Never the YTD
    #: figure passed off as a month, and never 0 for "unknown".
    movement_rc: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    missing_prior: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: The institution row's basis, line and sign — see the class docstring.
    balance_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    pl_line: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pl_sign: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    #: The ``as_of_date`` of the branch register batch these rows came from, so a
    #: reader can see that the breakdown is (say) a month older than the ledger.
    register_as_of: Mapped[date] = mapped_column(Date, nullable=False)


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


class BiFactTarget(_BuilderStamp, Base):
    """The bank's own budget / reforecast figure, PRE-MATCHED to its actual (D-064).

    One row per (as-of, measure, scope, version): the target the bank declared
    through the ``performance_targets`` register, the ACTUAL the BI compiler
    reads for the same measure and scope on the same date, and the variance
    between them. Nothing is joined at query time — the four catalogue
    variants (``.actual`` / ``.target`` / ``.variance`` / ``.variance_pct`` /
    ``.attainment_pct``, ``app/domain/bi/catalogue/targets.py``) all read THIS
    table, so the compiler's one-fact-table rule holds without being relaxed
    and a bank-wide target can never fan out across a scoped one.

    ``scope_basis`` records WHICH target the builder matched — a target the
    bank declared for exactly this scope, or the bank-wide target applied as
    the fallback — so the answer is auditable from the row. ``target_value``
    is NOT NULL because the row exists only because a target does; a measure
    with no target has no row at all, which is what makes the variants NULL
    rather than zero (D-015). ``actual_value`` and ``variance_value`` are
    nullable: a date whose book cannot answer the base measure still records
    the target the bank is being held to.
    """

    __tablename__ = "bi_fact_target"
    __table_args__ = (
        CheckConstraint(
            f"period_grain IN ({_values(TARGET_PERIOD_GRAINS)})",
            name="ck_bi_fact_target_period_grain",
        ),
        CheckConstraint(
            f"target_version IN ({_values(TARGET_VERSIONS)})",
            name="ck_bi_fact_target_version",
        ),
        CheckConstraint(
            f"time_behaviour IN ({_values(TARGET_TIME_BEHAVIOURS)})",
            name="ck_bi_fact_target_time_behaviour",
        ),
        CheckConstraint(
            f"scope_basis IN ({_values(TARGET_SCOPE_BASES)})",
            name="ck_bi_fact_target_scope_basis",
        ),
        _bank_fk(),
    )

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    #: The BASE catalogue measure id the target was declared against.
    measure_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    #: The catalogue dimension this row is scoped to; ``''`` is bank-wide.
    scope_dimension: Mapped[str] = mapped_column(String(80), primary_key=True)
    #: A scope value is COPIED VERBATIM from whichever dimension the target names,
    #: and the widest of those (``counterparty.group``, ``loan.employer``) is
    #: ``String(255)``, so this must be too. At 160 a target scoped to a long
    #: counterparty group raised ``StringDataRightTruncation`` inside the build's
    #: single nested transaction, which fails ALL SIX scopes — positions, events,
    #: GL, engine, dims and targets — and re-fails every night, from one register
    #: row (audit A8-01). ``TARGET_SCOPE_VALUE_WIDTH`` is the one number, and
    #: the register refuses a longer value at ingestion so the door and the mart
    #: cannot disagree.
    scope_value: Mapped[str] = mapped_column(String(TARGET_SCOPE_VALUE_WIDTH), primary_key=True)
    target_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    #: The window the declared target covers: first and last day of its grain.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_grain: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The bank's declared basis; it must equal the measure's own (a stock
    #: level compared against a flow accumulation is silently a whole period
    #: wrong), so a row that disagrees is refused rather than compared.
    time_behaviour: Mapped[str] = mapped_column(String(8), nullable=False)
    target_value: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    actual_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    #: ``actual − target`` in the measure's own unit; NULL when the actual is.
    variance_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    scope_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The scope the matched target was DECLARED for — equal to this row's
    #: scope when ``scope_basis`` is ``exact``, and the bank-wide scope when it
    #: is the fallback.
    declared_scope_dimension: Mapped[str] = mapped_column(String(80), nullable=False)
    declared_scope_value: Mapped[str] = mapped_column(
        String(TARGET_SCOPE_VALUE_WIDTH), nullable=False
    )


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
    """The R1–R12 outcome for a (bank, as-of) that the trust badge is read from."""

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
    BiFactGlBranchMonthly.__tablename__,
    BiFactEngineMetric.__tablename__,
    BiFactTarget.__tablename__,
    BiDimBranch.__tablename__,
    BiDimProduct.__tablename__,
    BiDimCounterparty.__tablename__,
    BiDimGlAccount.__tablename__,
    BiDimDate.__tablename__,
    BiMartBuild.__tablename__,
    BiReconciliationResult.__tablename__,
    BiQueryLog.__tablename__,
)
