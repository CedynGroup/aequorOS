"""Window analytics: engine-computed start/end-date series + daily aggregates.

The resolution contract mirrors the module dashboards' ``_build_trend``:
stored baseline runs win (``stored=True``), otherwise the period is recomputed
inline from canonical facts (``stored=False``), and unresolvable periods are
skipped. Empty windows are a valid 200 with empty lists, never a 500.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.models import BankReportingPeriod, RegulatoryRun
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import (
    job_queue,
    pipeline,
    regulatory_capital,
    regulatory_liquidity,
    window_analytics,
)
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.api.helpers import headers
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)
ALL_RATIOS = ("lcr_pct", "nsfr_pct", "car_pct", "cet1_ratio_pct")


def _period_id(db: Session, period_end: date = REPORTING_DATE) -> UUID:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == period_end,
        )
    )
    assert period_id is not None
    return period_id


def _mint_baseline_runs(db: Session, period_id: UUID) -> dict[str, dict]:
    """Official baseline liquidity + capital runs, returning stored metrics."""
    metrics: dict[str, dict] = {}
    run = regulatory_liquidity.create_liquidity_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded"
    stored = db.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
    assert stored is not None
    metrics["liquidity"] = stored.metrics
    run = regulatory_capital.create_capital_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded"
    stored = db.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run.id))
    assert stored is not None
    metrics["capital"] = stored.metrics
    return metrics


def test_single_period_window_over_stored_runs(db_session: Session) -> None:
    """A one-period window: start==end==avg==min==max, change 0, stored=True."""
    materialize_canonical_test_book(db_session)
    official = _mint_baseline_runs(db_session, _period_id(db_session))

    result = window_analytics.compute_window(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 31),
    )

    assert result.bank_id == SAMPLE_BANK_ID
    assert result.period_count == 1
    assert [stat.ratio for stat in result.ratios] == list(ALL_RATIOS)
    for stat in result.ratios:
        assert len(stat.points) == 1
        point = stat.points[0]
        assert point.period_end == REPORTING_DATE
        assert point.stored is True  # run-backed, not recomputed
        assert stat.start_value == point.value
        assert stat.end_value == point.value
        assert stat.avg == point.value
        assert stat.min == point.value
        assert stat.max == point.value
        assert stat.change == Decimal("0")
    # Values match the immutable official runs exactly.
    by_ratio = {stat.ratio: stat for stat in result.ratios}
    assert by_ratio["lcr_pct"].end_value == Decimal(str(official["liquidity"]["lcr_pct"]))
    assert by_ratio["nsfr_pct"].end_value == Decimal(str(official["liquidity"]["nsfr_pct"]))
    assert by_ratio["car_pct"].end_value == Decimal(str(official["capital"]["car_pct"]))
    assert by_ratio["cet1_ratio_pct"].end_value == Decimal(
        str(official["capital"]["cet1_ratio_pct"])
    )
    # No daily snapshots were created — the daily section stays honest-empty.
    assert result.daily == []


def test_window_without_stored_runs_computes_inline(db_session: Session) -> None:
    """No baseline runs → the trend fallback recomputes inline (stored=False)."""
    materialize_canonical_test_book(db_session)

    result = window_analytics.compute_window(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 2, 28),
    )

    assert result.period_count == 2
    assert [stat.ratio for stat in result.ratios] == list(ALL_RATIOS)
    for stat in result.ratios:
        assert len(stat.points) == 2
        assert all(point.stored is False for point in stat.points)
        assert stat.points[0].period_end == date(2026, 1, 31)
        assert stat.points[1].period_end == date(2026, 2, 28)
        values = [point.value for point in stat.points]
        assert stat.start_value == values[0]
        assert stat.end_value == values[1]
        assert stat.change == values[1] - values[0]
        assert stat.min == min(values)
        assert stat.max == max(values)
        assert stat.avg == (sum(values, Decimal(0)) / 2).quantize(Decimal("0.000001"))


def test_inverted_window_is_422(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    with pytest.raises(HTTPException) as excinfo:
        window_analytics.compute_window(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 3, 31),
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["error_code"] == "invalid_window"  # type: ignore[index]


def test_oversized_window_is_422(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    with pytest.raises(HTTPException) as excinfo:
        window_analytics.compute_window(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            start_date=date(2023, 1, 31),
            end_date=date(2026, 3, 31),
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["error_code"] == "window_too_large"  # type: ignore[index]


def test_empty_window_returns_valid_empty_payload(db_session: Session) -> None:
    """A window with no periods and no snapshots is a 200, never a 500."""
    materialize_canonical_test_book(db_session)
    result = window_analytics.compute_window(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        start_date=date(2010, 1, 1),
        end_date=date(2010, 12, 31),
    )
    assert result.period_count == 0
    assert result.ratios == []
    assert result.daily == []


def test_daily_stats_appear_only_when_snapshots_exist(db_session: Session) -> None:
    """The daily section aggregates the snapshot ladder inside the window."""
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID)
    db_session.commit()
    job = job_queue.enqueue(
        db_session,
        DEMO_ORG_ID,
        "pipeline_refresh",
        bank_id=SAMPLE_BANK_ID,
        payload={"as_of_date": FIXTURE_AS_OF.isoformat()},
    )
    db_session.commit()
    pipeline.run_refresh(db_session, job)

    today = date.today()
    result = window_analytics.compute_window(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        start_date=today - timedelta(days=2),
        end_date=today + timedelta(days=2),
    )
    daily = {row.module: row for row in result.daily}
    assert {"liquidity", "capital"} <= set(daily)
    liq = daily["liquidity"]
    assert liq.metric_key == "lcr_pct"
    assert liq.day_count == 1
    assert liq.min <= liq.avg <= liq.max
    assert daily["capital"].metric_key == "car_pct"

    # A window that misses the snapshot days carries no daily rows.
    off_window = window_analytics.compute_window(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        start_date=today - timedelta(days=30),
        end_date=today - timedelta(days=10),
    )
    assert off_window.daily == []


def test_endpoint_wiring_serializes_decimals_as_strings(db_client: TestClient) -> None:
    """GET /banks/{id}/analytics/window through the router: Decimals-as-str."""
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/analytics/window",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
        headers=headers(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["bank_id"] == SAMPLE_BANK_ID
    assert payload["period_count"] == 1
    assert [stat["ratio"] for stat in payload["ratios"]] == list(ALL_RATIOS)
    for stat in payload["ratios"]:
        assert isinstance(stat["avg"], str)
        assert isinstance(stat["change"], str)
        assert isinstance(stat["points"][0]["value"], str)

    inverted = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/analytics/window",
        params={"start_date": "2026-03-31", "end_date": "2026-03-01"},
        headers=headers(),
    )
    assert inverted.status_code == 422
