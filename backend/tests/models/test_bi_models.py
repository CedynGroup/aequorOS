"""Hermetic shape checks for the BI models (``app/models/bi.py``).

What the ORM declares is what ``Base.metadata.create_all`` builds for the
hermetic suite and the Playwright stack, and what the RLS static guard and the
current-generation guard read. These tests pin that declaration against the
binding column contract (``.ai/bi_contracts.md``) — keys, unique indexes, the
CHECK vocabularies, the tenant columns, ``sa.JSON`` (never JSONB) — and the
D-011 rule that the models are PLAIN tables: no ``postgresql_partition_by``, so
``create_all`` works on SQLite and on Postgres alike, and partitioning stays
the migration's business.

The BI session factory lives here too: with no ``BI_DATABASE_URL`` it yields
``None`` (the request session is used), and any session made from the BI pool
is an ordinary :class:`Session`, so the global ``after_begin`` tenant-GUC
listener applies to it unchanged.
"""

from __future__ import annotations

from typing import cast

import pytest
import sqlalchemy as sa
from sqlalchemy import Table, event, inspect
from sqlalchemy.dialects import sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_bi_engine, get_bi_sessionmaker, set_tenant_rls_context
from app.domain.authority.registry import REGISTRY, AdvisoryDesignation, MetricFamily, Regime
from app.domain.bi import extract
from app.domain.bi.authority import UNREGISTERED
from app.domain.bi.catalogue.version import CATALOGUE_VERSION
from app.domain.capital.loan_classification import (
    BANK_GRADE_ORDER,
    BASIS_DAYS_PAST_DUE,
    BASIS_RESTRUCTURE_HOLD,
    BASIS_STAGE_PROXY,
    BASIS_UNCLASSIFIED,
    SDI_GRADE_ORDER,
)
from app.domain.credit.dpd_bands import DPD_BAND_CODES
from app.domain.ingestion.reference_schemas import performance_targets
from app.domain.irr.engine import IRR_BUCKETS
from app.domain.liquidity.engine import HQLA_LEVEL_1, HQLA_LEVEL_2A, HQLA_LEVEL_2B
from app.domain.positions.families import (
    LOAN_CATEGORY_MAP,
    PAST_DUE_CATEGORY,
    UNCLASSIFIED_FAMILY,
    unclassified_category,
)
from app.models import bi
from app.models.bi import (
    BI_TABLES,
    MONTHLY_PARTITIONED_TABLES,
    YEARLY_PARTITIONED_TABLES,
    BiAggPositionDaily,
    BiDimBranch,
    BiDimCounterparty,
    BiDimDate,
    BiDimGlAccount,
    BiDimProduct,
    BiFactEngineMetric,
    BiFactGlMonthly,
    BiFactLoanEvent,
    BiFactPositionDaily,
    BiFactPositionEom,
    BiFactTarget,
    BiMartBuild,
    BiQueryLog,
    BiReconciliationResult,
)
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalLoanEvent,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)
from app.models.institution_profile import OUTLET_STATUSES, OUTLET_TYPES, Outlet
from app.models.institution_type import InstitutionType
from app.models.live import LIVE_MODULES, LiveMetric
from app.models.regulatory_run import RegulatoryRun

MODELS: tuple[type, ...] = (
    BiFactPositionDaily,
    BiFactPositionEom,
    BiAggPositionDaily,
    BiFactLoanEvent,
    BiFactGlMonthly,
    BiFactEngineMetric,
    BiFactTarget,
    BiDimBranch,
    BiDimProduct,
    BiDimCounterparty,
    BiDimGlAccount,
    BiDimDate,
    BiMartBuild,
    BiReconciliationResult,
    BiQueryLog,
)

#: Primary keys per the contract — partition key first where partitioned.
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "bi_fact_position_daily": ("as_of_date", "snapshot_id"),
    "bi_fact_position_eom": ("as_of_date", "snapshot_id"),
    "bi_agg_position_daily": ("as_of_date", "id"),
    "bi_fact_loan_event": ("event_date", "event_id"),
    "bi_fact_gl_monthly": (
        "organization_id",
        "bank_id",
        "month_end",
        "gl_account_code",
        "currency",
    ),
    "bi_fact_engine_metric": (
        "organization_id",
        "bank_id",
        "as_of_date",
        "module",
        "metric_id",
        "tier",
    ),
    "bi_fact_target": (
        "organization_id",
        "bank_id",
        "as_of_date",
        "measure_id",
        "scope_dimension",
        "scope_value",
        "target_version",
    ),
    "bi_dim_branch": ("organization_id", "bank_id", "branch_code"),
    "bi_dim_product": ("organization_id", "bank_id", "product_code"),
    "bi_dim_counterparty": ("organization_id", "bank_id", "counterparty_id"),
    "bi_dim_gl_account": ("organization_id", "bank_id", "account_code"),
    "bi_dim_date": ("organization_id", "bank_id", "date"),
    "bi_mart_builds": ("id",),
    "bi_reconciliation_results": ("id",),
    "bi_query_log": ("queried_at", "id"),
}

#: The contract's named vocabularies and the CHECK that must carry each.
VOCABULARIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "bi_fact_loan_event",
        "ck_bi_fact_loan_event_attribution_basis",
        bi.LOAN_EVENT_ATTRIBUTION_BASES,
    ),
    ("bi_fact_engine_metric", "ck_bi_fact_engine_metric_tier", bi.ENGINE_METRIC_TIERS),
    ("bi_fact_gl_monthly", "ck_bi_fact_gl_monthly_balance_basis", bi.GL_BALANCE_BASES),
    ("bi_fact_target", "ck_bi_fact_target_period_grain", bi.TARGET_PERIOD_GRAINS),
    ("bi_fact_target", "ck_bi_fact_target_version", bi.TARGET_VERSIONS),
    ("bi_fact_target", "ck_bi_fact_target_time_behaviour", bi.TARGET_TIME_BEHAVIOURS),
    ("bi_fact_target", "ck_bi_fact_target_scope_basis", bi.TARGET_SCOPE_BASES),
    ("bi_mart_builds", "ck_bi_mart_builds_scope", bi.MART_BUILD_SCOPES),
    ("bi_mart_builds", "ck_bi_mart_builds_status", bi.MART_BUILD_STATUSES),
    (
        "bi_reconciliation_results",
        "ck_bi_reconciliation_results_check_id",
        bi.RECONCILIATION_CHECK_IDS,
    ),
    (
        "bi_reconciliation_results",
        "ck_bi_reconciliation_results_status",
        bi.RECONCILIATION_STATUSES,
    ),
    ("bi_query_log", "ck_bi_query_log_surface", bi.QUERY_LOG_SURFACES),
    ("bi_query_log", "ck_bi_query_log_decision", bi.QUERY_LOG_DECISIONS),
    ("bi_query_log", "ck_bi_query_log_principal_type", bi.QUERY_LOG_PRINCIPAL_TYPES),
)


def _table(model: type) -> Table:
    return cast(Table, model.__table__)


def _check_definitions(table: Table) -> dict[str, str]:
    return {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name
    }


#: The prefix that makes a table part of the BI plane. The plane-boundary guard
#: (``tests/architecture/test_bi_plane_boundary.py``) derives the WRITABLE set
#: the same way, so the two cannot disagree about what a BI table is.
BI_TABLE_PREFIX = "bi_"


def _bi_tables_in(metadata: sa.MetaData) -> frozenset[str]:
    """Every ``bi_*`` table SQLAlchemy knows about in ``metadata``.

    This is the set the four Postgres suites MUST iterate — partition/FORCE-RLS
    state, CHECK-vocabulary parity, column type/nullability parity and
    CHECK-name parity all loop over :data:`BI_TABLES`. Deriving the same set
    from the metadata is what makes the hand-written tuple checkable.
    """
    return frozenset(name for name in metadata.tables if name.startswith(BI_TABLE_PREFIX))


def test_every_contract_table_is_declared_and_exported() -> None:
    assert tuple(_table(model).name for model in MODELS) == BI_TABLES
    # BOTH directions. Containment alone (``BI_TABLES <= metadata``) let a new
    # mart model pass every test while silently escaping the four Postgres
    # suites that iterate this tuple — the same blind spot that let a
    # model-only ``String(8)`` reach a migration (audit A6-04, A5-07).
    assert _bi_tables_in(Base.metadata) == frozenset(BI_TABLES), (
        "app/models/bi.py declares a bi_* table that BI_TABLES does not name (or "
        "the reverse), so the Postgres parity/partition/RLS suites would skip it: "
        f"{sorted(_bi_tables_in(Base.metadata) ^ frozenset(BI_TABLES))}"
    )
    assert all(name.startswith(BI_TABLE_PREFIX) for name in BI_TABLES)
    assert set(MONTHLY_PARTITIONED_TABLES) | set(YEARLY_PARTITIONED_TABLES) <= set(BI_TABLES)


def test_an_unregistered_bi_table_is_caught_rather_than_silently_skipped() -> None:
    """Proof the rule above bites, on a SYNTHETIC metadata.

    A real model added to ``Base`` would join ``create_all`` for the whole
    session, so the future mart is declared on a throwaway
    :class:`~sqlalchemy.MetaData` instead: the derivation must report it as the
    difference that fails :func:`test_every_contract_table_is_declared_and_exported`.
    """
    synthetic = sa.MetaData()
    for name in BI_TABLES:
        sa.Table(name, synthetic, sa.Column("organization_id", sa.String(16)))
    sa.Table(
        "bi_fact_hypothetical_future_mart",
        synthetic,
        sa.Column("organization_id", sa.String(16)),
    )
    # A non-BI table must NOT be dragged in by the prefix rule.
    sa.Table("regulatory_runs", synthetic, sa.Column("organization_id", sa.String(16)))

    derived = _bi_tables_in(synthetic)

    assert derived != frozenset(BI_TABLES), "the derivation is blind to a new bi_* table"
    assert derived - frozenset(BI_TABLES) == {"bi_fact_hypothetical_future_mart"}
    assert "regulatory_runs" not in derived


def test_create_all_on_sqlite_builds_every_bi_table_as_a_plain_table() -> None:
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    present = set(inspect(engine).get_table_names())
    assert set(BI_TABLES) <= present
    # D-011: partitioning is migration-owned. A ``postgresql_partition_by`` here
    # would make a Postgres ``create_all`` produce a childless parent that
    # rejects every INSERT on the TEST_DATABASE_URL conftest path.
    for model in MODELS:
        assert _table(model).dialect_options["postgresql"]["partition_by"] is None, _table(
            model
        ).name


@pytest.mark.parametrize("model", MODELS, ids=lambda model: _table(model).name)
def test_primary_keys_follow_the_contract(model: type) -> None:
    table = _table(model)
    assert tuple(column.name for column in table.primary_key.columns) == PRIMARY_KEYS[table.name]


@pytest.mark.parametrize("model", MODELS, ids=lambda model: _table(model).name)
def test_every_table_is_tenant_scoped_with_the_bank_foreign_key(model: type) -> None:
    table = _table(model)
    for column_name in ("organization_id", "bank_id"):
        column = table.c[column_name]
        assert isinstance(column.type, sa.String) and column.type.length == 16
        assert not column.nullable
    bank_fks = [
        fk
        for fk in table.foreign_key_constraints
        if fk.referred_table.name == "banks"
        and [column.name for column in fk.columns] == ["bank_id", "organization_id"]
    ]
    assert len(bank_fks) == 1, f"{table.name}: composite FK to banks missing"


@pytest.mark.parametrize("model", MODELS, ids=lambda model: _table(model).name)
def test_json_columns_are_plain_json_never_jsonb(model: type) -> None:
    for column in _table(model).columns:
        assert not isinstance(column.type, JSONB), f"{_table(model).name}.{column.name}"


def test_builder_provenance_is_stamped_where_the_builder_writes() -> None:
    built_by_builder = tuple(table for table in BI_TABLES if table != "bi_query_log")
    for name in built_by_builder:
        columns = Base.metadata.tables[name].c
        assert "builder_version" in columns and not columns["builder_version"].nullable, name
    for name in built_by_builder:
        if name in {"bi_mart_builds", "bi_reconciliation_results"}:
            # The control tables keep their own timestamps: a running build has
            # no ``built_at`` yet, and a reconciliation is ``evaluated_at``.
            assert "built_at" not in Base.metadata.tables[name].c, name
        else:
            assert not Base.metadata.tables[name].c["built_at"].nullable, name
    log = Base.metadata.tables["bi_query_log"].c
    assert "builder_version" not in log and "built_at" not in log
    assert {"build_fingerprint", "catalogue_version", "query_hash", "decision"} <= set(log.keys())


def test_daily_and_eom_facts_share_one_projection_and_the_three_indexes() -> None:
    daily = _table(BiFactPositionDaily)
    eom = _table(BiFactPositionEom)
    assert [c.name for c in daily.columns] == [c.name for c in eom.columns]
    assert [str(c.type) for c in daily.columns] == [str(c.type) for c in eom.columns]
    for table in (daily, eom):
        indexed = {
            index.name: tuple(column.name for column in index.columns) for index in table.indexes
        }
        assert indexed == {
            f"ix_{table.name}_org_bank_as_of_type": (
                "organization_id",
                "bank_id",
                "as_of_date",
                "position_type",
            ),
            f"ix_{table.name}_org_bank_as_of_branch": (
                "organization_id",
                "bank_id",
                "as_of_date",
                "branch_code",
            ),
            f"ix_{table.name}_org_bank_position_as_of": (
                "organization_id",
                "bank_id",
                "position_id",
                "as_of_date",
            ),
        }
    money = daily.c["balance_native"].type
    assert isinstance(money, sa.Numeric) and (money.precision, money.scale) == (28, 6)
    assert daily.c["balance_rc"].nullable and not daily.c["fx_unconverted"].nullable
    assert daily.c["classification_exposure_rc"].nullable


def test_aggregate_grain_is_unique_with_nulls_coalesced() -> None:
    table = _table(BiAggPositionDaily)
    grain = next(index for index in table.indexes if index.name == "uq_bi_agg_position_daily_grain")
    assert grain.unique
    rendered = str(sa.schema.CreateIndex(grain).compile(dialect=sqlite.dialect()))
    for expression in (
        "organization_id",
        "bank_id",
        "as_of_date",
        "position_type",
        "coalesce(product_family, '')",
        "coalesce(branch_code, '')",
        "currency",
        "coalesce(ifrs9_stage, 0)",
        "coalesce(dpd_band, '')",
        "coalesce(grade, '')",
        "coalesce(deposit_account_type, '')",
    ):
        assert expression in rendered, expression
    for measure in (
        "row_count",
        "balance_rc_sum",
        "classification_exposure_rc_sum",
        "non_performing_exposure_rc_sum",
        "provision_required_rc_sum",
        "provision_held_rc_sum",
        "collateral_rc_sum",
        "rate_x_balance_rc_sum",
        "fx_unconverted_count",
    ):
        assert not table.c[measure].nullable, measure


def test_control_tables_carry_the_contract_unique_keys() -> None:
    builds = {
        (uc.name, tuple(c.name for c in uc.columns))
        for uc in _table(BiMartBuild).constraints
        if isinstance(uc, sa.UniqueConstraint)
    }
    results = {
        (uc.name, tuple(c.name for c in uc.columns))
        for uc in _table(BiReconciliationResult).constraints
        if isinstance(uc, sa.UniqueConstraint)
    }
    assert builds == {
        (
            "uq_bi_mart_builds_bank_as_of_scope",
            ("organization_id", "bank_id", "as_of_date", "scope"),
        )
    }
    assert results == {
        (
            "uq_bi_reconciliation_results_bank_as_of_check",
            ("organization_id", "bank_id", "as_of_date", "check_id"),
        )
    }


@pytest.mark.parametrize(("table_name", "constraint", "values"), VOCABULARIES)
def test_check_constraints_derive_from_the_vocabulary_tuples(
    table_name: str, constraint: str, values: tuple[str, ...]
) -> None:
    definitions = _check_definitions(Base.metadata.tables[table_name])
    assert constraint in definitions, f"{table_name} has no CHECK {constraint}"
    for value in values:
        assert f"'{value}'" in definitions[constraint], f"{constraint} omits {value!r}"


def test_vocabularies_are_exactly_the_contract() -> None:
    assert bi.LOAN_EVENT_ATTRIBUTION_BASES == ("snapshot_on_or_before", "no_snapshot", "unmatched")
    assert bi.ENGINE_METRIC_TIERS == ("live", "official")
    assert bi.MART_BUILD_SCOPES == ("positions", "events", "gl", "engine", "dims", "targets")
    assert bi.MART_BUILD_STATUSES == ("running", "succeeded", "failed")
    assert bi.RECONCILIATION_CHECK_IDS == (
        "R1",
        "R2",
        "R3",
        "R4",
        "R5",
        "R6",
        "R7",
        "R8",
        "R9",
        "R10",
    )
    assert bi.RECONCILIATION_STATUSES == ("green", "amber", "red", "grey")
    assert bi.QUERY_LOG_SURFACES == (
        "query",
        "grid",
        "drill",
        "explain",
        "export",
        "feed",
        "trust",
        "catalogue",
    )
    assert bi.QUERY_LOG_DECISIONS == ("allowed", "denied")
    assert bi.TARGET_PERIOD_GRAINS == ("month", "quarter", "half_year", "year")
    assert bi.TARGET_VERSIONS == ("budget", "reforecast")
    assert bi.TARGET_TIME_BEHAVIOURS == ("stock", "flow")
    assert bi.TARGET_SCOPE_BASES == ("exact", "bank_wide")
    assert bi.TARGET_BANK_WIDE_SCOPE == ""
    assert bi.UNASSIGNED_REGION == "Unassigned region"
    assert bi.UNMAPPED_BRANCH_NAME == "Unmapped branch"
    region = _table(BiDimBranch).c["region"]
    assert isinstance(region.server_default, sa.DefaultClause)
    assert "Unassigned region" in str(region.server_default.arg)


def test_target_vocabularies_match_the_register_that_supplies_them() -> None:
    """The mart CHECKs and the register's enums are ONE vocabulary, both ways.

    ``app/models/bi.py`` mirrors the ``performance_targets`` register's words
    rather than importing them, because ``app.models`` stays free of
    ``app.domain.ingestion``. A mirror with nothing holding the two sides
    together is the defect it looks like a fix for: a grain added to the
    register would be accepted on ingestion and then refused by
    ``ck_bi_fact_target_period_grain`` when the builder tried to store its
    comparison, so the bank's declared target would silently never appear
    beside an actual. Asserted in BOTH directions — a value dropped from
    either side is as wrong as one added to only one.
    """
    pairs = (
        ("grain", bi.TARGET_PERIOD_GRAINS, performance_targets.GRAINS),
        ("time_behaviour", bi.TARGET_TIME_BEHAVIOURS, performance_targets.TIME_BEHAVIOURS),
        ("version", bi.TARGET_VERSIONS, performance_targets.VERSIONS),
    )
    for field, mart, register in pairs:
        assert mart == register, (
            f"bi_fact_target and the performance_targets register disagree about "
            f"{field!r}: the mart admits {mart} and the register admits {register}. "
            f"Edit BOTH, and the migration's pinned literal with them."
        )
    #: The register's enums are what ingestion validates against, so the field
    #: names have to be the ones it actually declares.
    assert set(performance_targets.SCHEMA.enums) == {"grain", "time_behaviour", "version"}


def test_partition_keys_are_named_for_the_builder() -> None:
    assert MONTHLY_PARTITIONED_TABLES == {
        "bi_fact_position_daily": "as_of_date",
        "bi_agg_position_daily": "as_of_date",
        "bi_fact_loan_event": "event_date",
        "bi_query_log": "queried_at",
    }
    assert YEARLY_PARTITIONED_TABLES == {"bi_fact_position_eom": "as_of_date"}
    for name, key in {**MONTHLY_PARTITIONED_TABLES, **YEARLY_PARTITIONED_TABLES}.items():
        # The range column leads the primary key, as a partitioned PK requires.
        assert PRIMARY_KEYS[name][0] == key, name


# --- column widths ---------------------------------------------------------------

#: Every vocabulary a ``String(n)`` column in the marts is written from, read
#: from the module that OWNS it, so a value added there is measured here. The
#: defect this pins: ``bi_fact_engine_metric.status`` was ``String(8)`` while
#: the official tier copies ``regulatory_runs.status = 'succeeded'`` (nine
#: characters) — a ``StringDataRightTruncation`` on Postgres that SQLite,
#: which ignores declared lengths, never showed.
VOCABULARY_WIDTHS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "bi_fact_position_daily",
        "maturity_bucket",
        tuple(bucket.code for bucket in extract.MATURITY_BUCKETS),
    ),
    ("bi_fact_position_daily", "repricing_bucket", tuple(name for name, _ in IRR_BUCKETS)),
    ("bi_fact_position_daily", "dpd_band", DPD_BAND_CODES),
    ("bi_fact_position_daily", "grade", (*BANK_GRADE_ORDER, *SDI_GRADE_ORDER)),
    (
        "bi_fact_position_daily",
        "classification_basis",
        (BASIS_DAYS_PAST_DUE, BASIS_STAGE_PROXY, BASIS_UNCLASSIFIED, BASIS_RESTRUCTURE_HOLD),
    ),
    (
        "bi_fact_position_daily",
        "product_family",
        (*extract.PRODUCT_FAMILY_LABELS, UNCLASSIFIED_FAMILY),
    ),
    (
        "bi_fact_position_daily",
        "exposure_category",
        (
            *(category for category, _weight in LOAN_CATEGORY_MAP.values()),
            PAST_DUE_CATEGORY[0],
            # The longest an unrecognised regulatory category can produce: the
            # prefix plus a slug of the whole 80-character canonical column.
            unclassified_category("x" * 80),
        ),
    ),
    ("bi_fact_position_daily", "hqla_level", (HQLA_LEVEL_1, HQLA_LEVEL_2A, HQLA_LEVEL_2B)),
    ("bi_fact_loan_event", "attribution_basis", bi.LOAN_EVENT_ATTRIBUTION_BASES),
    ("bi_fact_gl_monthly", "balance_basis", bi.GL_BALANCE_BASES),
    ("bi_fact_engine_metric", "tier", bi.ENGINE_METRIC_TIERS),
    (
        "bi_fact_engine_metric",
        "status",
        ("green", "amber", "red", "na", "queued", "running", "succeeded", "failed"),
    ),
    (
        "bi_fact_engine_metric",
        "module",
        (*LIVE_MODULES, *(family.value for family in MetricFamily)),
    ),
    ("bi_fact_engine_metric", "metric_id", tuple(entry.metric_id for entry in REGISTRY)),
    ("bi_fact_engine_metric", "unit", ("text", "count", "pct", "ccy", "ratio")),
    ("bi_fact_engine_metric", "regime", tuple(regime.value for regime in Regime)),
    (
        "bi_fact_engine_metric",
        "advisory_designation",
        (*(designation.value for designation in AdvisoryDesignation), UNREGISTERED),
    ),
    ("bi_dim_branch", "outlet_type", OUTLET_TYPES),
    ("bi_dim_branch", "status", OUTLET_STATUSES),
    ("bi_dim_branch", "region", (bi.UNASSIGNED_REGION,)),
    ("bi_dim_branch", "name", (bi.UNMAPPED_BRANCH_NAME,)),
    ("bi_mart_builds", "scope", bi.MART_BUILD_SCOPES),
    ("bi_mart_builds", "status", bi.MART_BUILD_STATUSES),
    ("bi_reconciliation_results", "check_id", bi.RECONCILIATION_CHECK_IDS),
    ("bi_reconciliation_results", "status", bi.RECONCILIATION_STATUSES),
    ("bi_query_log", "principal_type", bi.QUERY_LOG_PRINCIPAL_TYPES),
    ("bi_query_log", "surface", bi.QUERY_LOG_SURFACES),
    ("bi_query_log", "decision", bi.QUERY_LOG_DECISIONS),
    ("bi_query_log", "catalogue_version", (CATALOGUE_VERSION,)),
)

#: Columns COPIED from another table's column must be at least as wide as it.
COPIED_WIDTHS: tuple[tuple[str, str, type, str], ...] = (
    ("bi_fact_position_daily", "source_system", CanonicalPositionSnapshot, "source_system"),
    ("bi_fact_position_daily", "source_reference", CanonicalPositionSnapshot, "source_reference"),
    ("bi_fact_position_daily", "position_type", CanonicalPosition, "position_type"),
    ("bi_fact_position_daily", "currency", CanonicalPosition, "currency"),
    ("bi_fact_position_daily", "rate_type", CanonicalPositionSnapshot, "rate_type"),
    ("bi_fact_position_daily", "rate_index", CanonicalPositionSnapshot, "rate_index"),
    (
        "bi_fact_position_daily",
        "deposit_account_type",
        CanonicalPositionSnapshot,
        "deposit_account_type",
    ),
    ("bi_fact_position_daily", "product_code", CanonicalProduct, "product_code"),
    ("bi_fact_position_daily", "counterparty_type", CanonicalCounterparty, "counterparty_type"),
    ("bi_fact_position_daily", "counterparty_group", CanonicalCounterparty, "group_reference"),
    ("bi_fact_position_daily", "gl_account_code", CanonicalGlAccount, "account_code"),
    ("bi_fact_loan_event", "event_type", CanonicalLoanEvent, "event_type"),
    ("bi_fact_loan_event", "event_subtype", CanonicalLoanEvent, "event_subtype"),
    ("bi_fact_loan_event", "source_system", CanonicalLoanEvent, "source_system"),
    ("bi_fact_loan_event", "source_reference", CanonicalLoanEvent, "source_reference"),
    (
        "bi_fact_loan_event",
        "position_source_reference",
        CanonicalLoanEvent,
        "position_source_reference",
    ),
    ("bi_fact_loan_event", "currency", CanonicalLoanEvent, "currency"),
    ("bi_fact_gl_monthly", "gl_account_code", CanonicalGlAccount, "account_code"),
    ("bi_fact_gl_monthly", "account_class", CanonicalGlAccount, "account_class"),
    ("bi_fact_engine_metric", "module", LiveMetric, "module"),
    ("bi_fact_engine_metric", "status", LiveMetric, "status"),
    ("bi_fact_engine_metric", "status", RegulatoryRun, "status"),
    ("bi_fact_engine_metric", "engine_version", LiveMetric, "engine_version"),
    ("bi_fact_engine_metric", "engine_version", RegulatoryRun, "engine_version"),
    ("bi_fact_engine_metric", "pipeline_state", LiveMetric, "pipeline_state"),
    ("bi_fact_engine_metric", "input_hash", LiveMetric, "computed_from_input_hash"),
    ("bi_fact_engine_metric", "institution_class", InstitutionType, "type_code"),
    ("bi_dim_branch", "name", Outlet, "name"),
    ("bi_dim_branch", "outlet_type", Outlet, "outlet_type"),
    ("bi_dim_branch", "status", Outlet, "status"),
    ("bi_dim_product", "product_code", CanonicalProduct, "product_code"),
    ("bi_dim_product", "name", CanonicalProduct, "name"),
    ("bi_dim_product", "regulatory_category", CanonicalProduct, "regulatory_category"),
    ("bi_dim_product", "risk_weight_code", CanonicalProduct, "risk_weight_code"),
    ("bi_dim_counterparty", "source_reference", CanonicalCounterparty, "source_reference"),
    ("bi_dim_counterparty", "name", CanonicalCounterparty, "name"),
    ("bi_dim_counterparty", "counterparty_type", CanonicalCounterparty, "counterparty_type"),
    ("bi_dim_counterparty", "group_reference", CanonicalCounterparty, "group_reference"),
    ("bi_dim_counterparty", "country_code", CanonicalCounterparty, "country_code"),
    ("bi_dim_counterparty", "rating", CanonicalCounterparty, "rating"),
    ("bi_dim_gl_account", "account_code", CanonicalGlAccount, "account_code"),
    ("bi_dim_gl_account", "name", CanonicalGlAccount, "name"),
    ("bi_dim_gl_account", "account_class", CanonicalGlAccount, "account_class"),
    ("bi_dim_gl_account", "parent_account_code", CanonicalGlAccount, "account_code"),
)


def _width(table_name: str, column_name: str) -> int:
    column = Base.metadata.tables[table_name].c[column_name]
    assert isinstance(column.type, sa.String), f"{table_name}.{column_name} is not a String"
    assert column.type.length is not None, f"{table_name}.{column_name} has no length"
    return column.type.length


@pytest.mark.parametrize(
    ("table_name", "column_name", "values"),
    VOCABULARY_WIDTHS,
    ids=[f"{table}.{column}" for table, column, _ in VOCABULARY_WIDTHS],
)
def test_string_columns_are_at_least_as_wide_as_their_vocabulary(
    table_name: str, column_name: str, values: tuple[str, ...]
) -> None:
    assert values, "an empty vocabulary proves nothing"
    width = _width(table_name, column_name)
    too_long = sorted((value for value in values if len(value) > width), key=len)
    assert not too_long, (
        f"{table_name}.{column_name} is String({width}) but must hold "
        f"{too_long[-1]!r} ({len(too_long[-1])} chars); widen the model AND migration 202609220066"
    )


@pytest.mark.parametrize(
    ("table_name", "column_name", "source", "source_column"),
    COPIED_WIDTHS,
    ids=[
        f"{table}.{column}<-{src.__tablename__}.{col}" for table, column, src, col in COPIED_WIDTHS
    ],
)
def test_copied_columns_are_at_least_as_wide_as_their_source(
    table_name: str, column_name: str, source: type, source_column: str
) -> None:
    source_width = _width(_table(source).name, source_column)
    assert _width(table_name, column_name) >= source_width, (
        f"{table_name}.{column_name} is narrower than {_table(source).name}.{source_column} "
        f"({source_width}); a copied value would be truncated on Postgres"
    )


# --- session factory -----------------------------------------------------------


def test_bi_sessionmaker_is_none_without_a_bi_database_url() -> None:
    """The hermetic and single-database default: the request session is used."""
    assert get_bi_sessionmaker() is None


def test_bi_sessionmaker_binds_the_bi_pool_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql+psycopg://bi:bi@localhost:1/never_connected"
    monkeypatch.setenv("BI_DATABASE_URL", url)
    get_settings.cache_clear()
    get_bi_engine.cache_clear()
    try:
        factory = get_bi_sessionmaker()
        assert factory is not None
        assert factory.kw["bind"] is get_bi_engine(url)
    finally:
        get_bi_engine.cache_clear()
        get_settings.cache_clear()


def test_bi_engine_is_a_small_named_pool_on_postgres() -> None:
    get_bi_engine.cache_clear()
    engine = get_bi_engine("postgresql+psycopg://bi:bi@localhost:1/never_connected")
    seen: dict[str, object] = {}

    class _Abort(Exception):
        pass

    # ``do_connect`` sees the merged connect arguments just before the DBAPI
    # call; raising there inspects them without ever reaching a server.
    @event.listens_for(engine, "do_connect")
    def _capture(_dialect, _record, _cargs, cparams) -> None:
        seen.update(cparams)
        raise _Abort

    try:
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == 3
        assert engine.pool._max_overflow == 2  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(_Abort):
            engine.connect()
        assert seen["application_name"] == "aequoros-bi"
        assert seen["keepalives"] == 1
        assert get_bi_engine("postgresql+psycopg://bi:bi@localhost:1/never_connected") is engine
    finally:
        engine.dispose()
        get_bi_engine.cache_clear()


def test_bi_sessions_get_the_tenant_guc_listener_for_free(tmp_path) -> None:
    """The ``after_begin`` listener is registered on :class:`Session` itself,
    so a session from the BI pool is scoped exactly like a request session."""
    assert event.contains(Session, "after_begin", set_tenant_rls_context)
    engine = get_bi_engine(f"sqlite+pysqlite:///{tmp_path / 'bi.db'}")
    try:
        with sessionmaker(bind=engine)() as session:
            assert isinstance(session, Session)
            session.info["organization_id"] = "OR-DEM00001"
            session.execute(sa.text("SELECT 1"))  # begins a transaction; the hook runs
    finally:
        engine.dispose()
        get_bi_engine.cache_clear()
