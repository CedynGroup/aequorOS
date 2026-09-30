"""The BI mart builder on real Postgres partitions (migration ``202609220066``).

What SQLite cannot prove (D-011): that a build creates the month / year
children it writes into THROUGH the migration's definer functions before the
first row lands (so nothing ever sits in DEFAULT), that the ``(bank, as_of)``
slice replace happens inside a real partition, that a second run with an
unchanged fingerprint skips, and that retention drops the daily children
through ``bi_drop_month_partition`` while the month-end parent is never
touched (D-039). Runs under a NOBYPASSRLS role with the tenant GUC set by the
ORM session hook, exactly as the ``bi`` lane would under its own role.

Postgres-gated on ``TEST_DATABASE_URL``; the schema is disposable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.db.session import set_tenant_rls_context
from app.models import (
    BankReportingPeriod,
    BiFactEngineMetric,
    BiFactPositionDaily,
    BiMartBuild,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
    RegulatoryRun,
)
from app.services.bi import mart_builder, partitions
from tests.api.helpers import ORG_1, USER_1
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

_ = set_tenant_rls_context  # the after_begin hook that sets the tenant GUC

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the Postgres builder test.",
)

AS_OF = FIXTURE_AS_OF  # 2026-06-30
CHILDREN = {
    "bi_fact_position_daily": "bi_fact_position_daily_y2026m06",
    "bi_agg_position_daily": "bi_agg_position_daily_y2026m06",
    "bi_fact_loan_event": "bi_fact_loan_event_y2026m06",
    "bi_fact_position_eom": "bi_fact_position_eom_y2026",
}


@pytest.fixture(scope="module")
def builder_schema() -> Iterator[MigratedPostgresSchema]:
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_bi_builder_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    monkeypatch = pytest.MonkeyPatch()
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    app_engine = create_engine(database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        command.upgrade(alembic_config_for_app(), "head")
        with app_engine.connect() as connection:
            bypasses = connection.execute(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).scalar()
            connection.rollback()
        if bypasses:
            pytest.skip(
                "Current TEST_DATABASE_URL role bypasses RLS; the proof needs a tenant role."
            )
        yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
    finally:
        monkeypatch.undo()
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture(scope="module")
def session(builder_schema: MigratedPostgresSchema) -> Iterator[Session]:
    """One tenant-bound ORM session for the whole module (the builder commits)."""
    db = Session(builder_schema.app_engine, autoflush=False, expire_on_commit=False)
    db.info["organization_id"] = ORG_1
    try:
        materialize_canonical_test_book(db)
        db.flush()
        seed_canonical_fixture(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        db.commit()
        yield db
    finally:
        db.rollback()
        db.close()


def _children(db: Session, parent: str) -> set[str]:
    return set(
        db.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = CAST(:parent AS regclass)"
            ),
            {"parent": parent},
        ).scalars()
    )


def _direct_count(db: Session, relation: str) -> int:
    """Rows in ``relation`` addressed BY NAME (so a child's own RLS policy applies)."""
    return int(db.execute(text(f"SELECT count(*) FROM {relation}")).scalar() or 0)  # noqa: S608


def _add_deposit(db: Session, reference: str, balance: str) -> None:
    """One more accepted deposit at ``AS_OF`` through the canonical models."""
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=AS_OF,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="pg-builder-test",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": AS_OF,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }
    position = CanonicalPosition(
        **common, source_reference=reference, position_type="DEPOSIT", currency="GHS"
    )
    db.add(position)
    db.flush()
    db.add(
        CanonicalPositionSnapshot(
            **common,
            source_reference=reference,
            position_id=position.id,
            balance=Decimal(balance),
            attributes={"balance_ghs": balance},
        )
    )
    db.commit()


def _build(db: Session, as_of: date = AS_OF) -> mart_builder.BuildOutcome:
    outcome = mart_builder.refresh_bank_as_of(
        db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=as_of, reason="postgres-test"
    )
    db.commit()
    return outcome


def test_partitions_are_created_by_the_definer_before_the_slice_lands(session: Session) -> None:
    assert partitions.is_partitioned(session, "bi_fact_position_daily") is True
    for parent, child in CHILDREN.items():
        assert child not in _children(session, parent)

    outcome = _build(session)
    assert outcome.status == "succeeded"
    assert outcome.row_counts["bi_fact_position_daily"] == 18
    for parent, child in CHILDREN.items():
        assert child in _children(session, parent), parent
    # every row is in the month child, none fell through to DEFAULT
    assert _direct_count(session, "bi_fact_position_daily_y2026m06") == 18
    assert _direct_count(session, "bi_fact_position_daily_default") == 0
    assert _direct_count(session, "bi_fact_position_eom_y2026") == 18
    assert _direct_count(session, "bi_agg_position_daily_default") == 0
    # the child carries RLS of its own: another tenant, or no tenant, sees nothing by name
    with session.begin_nested():
        session.execute(text("SELECT set_config('app.organization_id', 'OR-1S000002', true)"))
        assert _direct_count(session, "bi_fact_position_daily_y2026m06") == 0
        session.execute(text("SELECT set_config('app.organization_id', '', true)"))
        assert _direct_count(session, "bi_fact_position_daily_y2026m06") == 0
        session.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
        )
    assert partitions.month_children(session, "bi_fact_position_daily") == [
        ("bi_fact_position_daily_y2026m06", date(2026, 6, 1))
    ]


def test_second_run_skips_and_a_changed_book_replaces_the_slice_in_place(session: Session) -> None:
    first = mart_builder.fingerprint_for(
        session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF
    )
    skipped = _build(session)
    assert skipped.status == "skipped"
    assert skipped.fingerprint == first
    builds = {
        row.scope: row
        for row in session.scalars(
            select(BiMartBuild).where(
                BiMartBuild.bank_id == SAMPLE_BANK_ID, BiMartBuild.as_of_date == AS_OF
            )
        )
    }
    assert all(row.status == "succeeded" for row in builds.values())

    _add_deposit(session, "DEP/PG", "1234")

    rebuilt = _build(session)
    assert rebuilt.status == "succeeded"
    assert rebuilt.fingerprint != first
    assert rebuilt.row_counts["bi_fact_position_daily"] == 19
    assert _direct_count(session, "bi_fact_position_daily_y2026m06") == 19  # replaced, not appended
    assert _direct_count(session, "bi_fact_position_eom_y2026") == 19
    references = session.scalars(
        select(BiFactPositionDaily.source_reference).where(
            BiFactPositionDaily.bank_id == SAMPLE_BANK_ID, BiFactPositionDaily.as_of_date == AS_OF
        )
    ).all()
    assert len(references) == len(set(references)) == 19
    assert "DEP/PG" in references


def test_retention_drops_only_daily_month_children_and_never_the_eom_parent(
    session: Session,
) -> None:
    # nothing is old enough yet
    assert mart_builder.apply_retention(session, retention_days=95, today=date(2026, 7, 15)) == ()
    session.commit()
    dropped = mart_builder.apply_retention(session, retention_days=95, today=date(2027, 1, 1))
    session.commit()
    assert set(dropped) == {"bi_fact_position_daily_y2026m06", "bi_agg_position_daily_y2026m06"}
    assert "bi_fact_position_daily_y2026m06" not in _children(session, "bi_fact_position_daily")
    assert "bi_agg_position_daily_y2026m06" not in _children(session, "bi_agg_position_daily")
    # the month-end year child and the loan-event child are untouched
    assert "bi_fact_position_eom_y2026" in _children(session, "bi_fact_position_eom")
    assert "bi_fact_loan_event_y2026m06" in _children(session, "bi_fact_loan_event")
    assert _direct_count(session, "bi_fact_position_eom_y2026") == 19
    assert int(session.scalar(select(func.count()).select_from(BiFactPositionDaily)) or 0) == 0
    # Retention leaves ``bi_mart_builds`` alone, so a refresh for a retained-out date
    # with unchanged inputs is a SKIP (the daily slice stays dropped on purpose)...
    assert _build(session).status == "skipped"
    assert "bi_fact_position_daily_y2026m06" not in _children(session, "bi_fact_position_daily")
    # ...and a changed book rebuilds: the definer recreates the children and refills them
    _add_deposit(session, "DEP/PG2", "77")
    outcome = _build(session)
    assert outcome.status == "succeeded"
    assert _direct_count(session, "bi_fact_position_daily_y2026m06") == 20
    assert _direct_count(session, "bi_agg_position_daily_default") == 0


def test_official_tier_row_status_fits_the_column(session: Session) -> None:
    period = session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID, BankReportingPeriod.period_end == AS_OF
        )
    )
    if period is None:
        period = BankReportingPeriod(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            period_start=date(2026, 6, 1),
            period_end=AS_OF,
            label="2026-06",
            status="open",
        )
        session.add(period)
        session.flush()
    session.add(
        RegulatoryRun(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            reporting_period_id=period.id,
            module="capital",
            scenario_code="baseline",
            status="succeeded",
            engine_version="test-engine",
            input_schema_version="v1",
            output_schema_version="v1",
            input_hash="a" * 64,
            inputs={},
            metrics={"car_pct": "13.75"},
            completed_at=datetime(2026, 7, 1, tzinfo=UTC),
            created_by=USER_1,
        )
    )
    session.commit()
    try:
        outcome = _build(session)
    except Exception:
        session.rollback()
        raise
    assert outcome.status == "succeeded"
    official = session.scalars(
        select(BiFactEngineMetric).where(
            BiFactEngineMetric.bank_id == SAMPLE_BANK_ID, BiFactEngineMetric.tier == "official"
        )
    ).all()
    assert [row.metric_id for row in official] == ["car_pct"]
