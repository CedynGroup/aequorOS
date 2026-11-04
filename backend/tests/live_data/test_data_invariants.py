"""Invariants the REAL dataset must always satisfy (read-only; see conftest).

These encode standing orders as executable checks — above all the 2026-07-21
rule that NO bank data is seeded: every canonical row must trace to an
ingestion batch, i.e. it entered through the Data Engine (upload, core-banking
adapter, or API push)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InternalError, OperationalError
from sqlalchemy.orm import Session


def _scalar(db: Session, sql: str) -> int:
    value = db.execute(text(sql)).scalar()
    return int(value or 0)


def test_every_canonical_position_is_ingestion_traced(live_db: Session) -> None:
    untraced = _scalar(
        live_db, "SELECT count(*) FROM canonical_positions WHERE ingestion_batch_id IS NULL"
    )
    assert untraced == 0, f"{untraced} canonical positions lack an ingestion trail (seed signature)"


def test_every_reference_row_is_ingestion_traced(live_db: Session) -> None:
    untraced = _scalar(
        live_db,
        "SELECT count(*) FROM canonical_reference_rows WHERE ingestion_batch_id IS NULL",
    )
    assert untraced == 0, f"{untraced} reference rows lack an ingestion trail (seed signature)"


def test_at_least_one_bank_with_ingested_data(live_db: Session) -> None:
    banks = _scalar(live_db, "SELECT count(*) FROM banks")
    assert banks >= 1, "no banks — a bank should exist via its first ingestion"
    positions = _scalar(
        live_db, "SELECT count(*) FROM canonical_positions WHERE superseded_by IS NULL"
    )
    assert positions > 0, "no active canonical positions"


def test_reporting_periods_are_monthly_contiguous(live_db: Session) -> None:
    """No gaps in the period spine: months between first and last == row count."""
    # Calendar-month arithmetic on truncated dates — age() month-counting is
    # off-by-one across 30/31-day month-ends.
    row = live_db.execute(
        text(
            """
            SELECT count(DISTINCT date_trunc('month', period_end)) AS months,
                   ((date_part('year', max(period_end)) * 12
                     + date_part('month', max(period_end)))
                    - (date_part('year', min(period_end)) * 12
                       + date_part('month', min(period_end)))
                    + 1) AS span
            FROM bank_reporting_periods
            """
        )
    ).one()
    assert row.months > 0, "no reporting periods"
    assert int(row.months) == int(row.span), (
        f"period spine has gaps: {row.months} distinct months across a {int(row.span)}-month span"
    )


def test_every_reporting_period_has_facts(live_db: Session) -> None:
    empty = _scalar(
        live_db,
        """
        SELECT count(*) FROM bank_reporting_periods p
        WHERE NOT EXISTS (
            SELECT 1 FROM bank_financial_facts f WHERE f.reporting_period_id = p.id
        )
        """,
    )
    assert empty == 0, f"{empty} reporting periods have zero derived facts"


def test_live_metrics_exist(live_db: Session) -> None:
    metrics = _scalar(live_db, "SELECT count(*) FROM live_metrics")
    assert metrics > 0, "live engine has produced no metrics for the real dataset"


def test_every_active_user_has_a_sign_in_method(live_db: Session) -> None:
    stranded = _scalar(
        live_db,
        """
        SELECT count(*) FROM users
        WHERE is_active AND password_hash IS NULL AND sso_subject IS NULL
        """,
    )
    assert stranded == 0, f"{stranded} active users can sign in by neither password nor SSO"


def test_session_is_physically_read_only(live_db: Session) -> None:
    """The suite's safety property, asserted: writes are rejected server-side.

    (An UPDATE probe, not CREATE TEMP — Postgres permits temporary objects in
    read-only transactions, so only a real-table write proves the guard.)"""
    with pytest.raises((DBAPIError, InternalError, OperationalError)):
        live_db.execute(text("UPDATE organizations SET name = name"))
    live_db.rollback()
