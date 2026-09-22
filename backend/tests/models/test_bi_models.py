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
    BiMartBuild,
    BiQueryLog,
    BiReconciliationResult,
)

MODELS: tuple[type, ...] = (
    BiFactPositionDaily,
    BiFactPositionEom,
    BiAggPositionDaily,
    BiFactLoanEvent,
    BiFactGlMonthly,
    BiFactEngineMetric,
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


def test_every_contract_table_is_declared_and_exported() -> None:
    assert tuple(_table(model).name for model in MODELS) == BI_TABLES
    assert set(BI_TABLES) <= set(Base.metadata.tables)
    assert all(name.startswith("bi_") for name in BI_TABLES)
    assert set(MONTHLY_PARTITIONED_TABLES) | set(YEARLY_PARTITIONED_TABLES) <= set(BI_TABLES)


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
    assert bi.MART_BUILD_SCOPES == ("positions", "events", "gl", "engine", "dims")
    assert bi.MART_BUILD_STATUSES == ("running", "succeeded", "failed", "skipped")
    assert bi.RECONCILIATION_CHECK_IDS == ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9")
    assert bi.RECONCILIATION_STATUSES == ("green", "amber", "red", "grey")
    assert bi.QUERY_LOG_SURFACES == ("query", "grid", "drill", "explain", "export", "feed")
    assert bi.QUERY_LOG_DECISIONS == ("allowed", "denied")
    assert bi.UNASSIGNED_REGION == "Unassigned region"
    assert bi.UNMAPPED_BRANCH_NAME == "Unmapped branch"
    region = _table(BiDimBranch).c["region"]
    assert isinstance(region.server_default, sa.DefaultClause)
    assert "Unassigned region" in str(region.server_default.arg)


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
