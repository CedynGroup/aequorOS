"""Window analytics: engine-computed start/end-date series + daily aggregates.

The resolution contract mirrors the module dashboards' ``_build_trend``:
stored baseline runs win (``stored=True``), otherwise the period is recomputed
inline from canonical facts (``stored=False``), and unresolvable periods are
skipped. Empty windows are a valid 200 with empty lists, never a 500.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.domain.reporting.period_windows import trailing_month_end_window
from app.models import BankReportingPeriod, LiveMetricSnapshot, RegulatoryRun, User
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import (
    authorization,
    job_queue,
    pipeline,
    regulatory_capital,
    regulatory_liquidity,
    window_analytics,
)
from tests.api.helpers import headers
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

pytestmark = pytest.mark.usefixtures("capital_run_authority")

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)
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


def test_period_count_counts_every_period_the_caller_asked_for(db_session: Session) -> None:
    """``period_count`` is the wire count of periods the CALLER's dates cover.

    The five module dashboards select their sparkline through
    ``trailing_month_end_window`` because a fixed 13-ROW slice meant 13
    month-ends for a monthly feeder and 13 business days for a daily one. This
    surface has no such stand-in — the horizon is the two dates that were sent,
    and the window-analysis footer reads this number as "N periods". Thinning
    the selection to month-ends would drop periods the caller asked for, so the
    two selections are pinned here as different contracts (audit A1-10).
    """
    materialize_canonical_test_book(db_session)
    for period_end in (date(2026, 3, 6), date(2026, 3, 13), date(2026, 3, 20)):
        db_session.add(
            BankReportingPeriod(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                period_start=date(2026, 3, 1),
                period_end=period_end,
                label=period_end.isoformat(),
                status="open",
            )
        )
    db_session.flush()

    result = window_analytics.compute_window(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 31),
    )

    # Three weekly closes plus the month-end: the count follows the dates, not
    # the cadence, and not how many periods happened to resolve a ratio.
    assert result.period_count == 4
    assert [stat.ratio for stat in result.ratios] == list(ALL_RATIOS)
    assert all(len(stat.points) == 1 for stat in result.ratios)

    # The dashboards' helper over the same rows keeps only the month's last
    # period. Swapping it in here would report 1 period for a window that
    # covers 4 — the field would stop answering the question it is asked.
    periods = db_session.scalars(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end >= date(2026, 3, 1),
            BankReportingPeriod.period_end <= date(2026, 3, 31),
        )
    ).all()
    assert [period.period_end for period in trailing_month_end_window(periods)] == [
        date(2026, 3, 31)
    ]


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


def test_daily_stats_read_the_measured_headline_for_irr_and_credit(db_session: Session) -> None:
    """IRR aggregates the SIGNED ΔEVE / Tier 1 the engine measured — never the
    limit (``eve_limit_pct``, a parameter that would make a flat series) — and
    credit aggregates the NPL ratio; rows come out in live-module order."""
    materialize_canonical_test_book(db_session)
    period_id = _period_id(db_session)
    snapshot_date = date(2026, 3, 31)
    ladder = (
        ("capital", {"car_pct": "14.1"}),
        ("credit", {"npl_ratio_pct": "6.5", "npl_limit_pct": "10"}),
        # A limit without the measured change is not a headline: no irr row.
        ("irr", {"eve_limit_pct": "15"}),
        ("liquidity", {"lcr_pct": "131.2"}),
    )
    for module, metrics in ladder:
        db_session.add(
            LiveMetricSnapshot(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                module=module,
                reporting_period_id=period_id,
                snapshot_date=snapshot_date,
                metrics=metrics,
                status="green",
                computed_at=utc_now(),
            )
        )
    db_session.commit()

    limit_only = window_analytics.compute_window(
        db_session, MAKER, SAMPLE_BANK_ID, start_date=snapshot_date, end_date=snapshot_date
    )
    assert [row.module for row in limit_only.daily] == ["liquidity", "capital", "credit"]
    credit = next(row for row in limit_only.daily if row.module == "credit")
    assert credit.metric_key == "npl_ratio_pct"
    assert credit.min == credit.avg == credit.max == Decimal("6.5")

    db_session.add(
        LiveMetricSnapshot(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            module="irr",
            reporting_period_id=period_id,
            snapshot_date=date(2026, 3, 30),
            metrics={"worst_eve_change_pct_tier1": "-7.25", "eve_limit_pct": "15"},
            status="green",
            computed_at=utc_now(),
        )
    )
    db_session.commit()
    measured = window_analytics.compute_window(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        start_date=date(2026, 3, 30),
        end_date=snapshot_date,
    )
    assert [row.module for row in measured.daily] == ["liquidity", "capital", "credit", "irr"]
    irr = next(row for row in measured.daily if row.module == "irr")
    assert irr.metric_key == "worst_eve_change_pct_tier1"
    # The signed value survives aggregation as stored (loss negative).
    assert irr.day_count == 1
    assert irr.min == irr.avg == irr.max == Decimal("-7.25")


def test_daily_stats_aggregate_the_rating_ladder(db_session: Session) -> None:
    """The rating engine's PD band is advisory, and it still gets a daily row.

    ``_PRIMARY_METRIC_KEY`` had no ``rating`` entry, so ``_daily_stats`` skipped
    every rating snapshot: the module computed and the window never mentioned it
    (decision D-031). Advisory standing is a labelling rule — the panel marks the
    line advisory — not grounds for dropping an engine from the analysis.
    """
    materialize_canonical_test_book(db_session)
    period_id = _period_id(db_session)
    for snapshot_date, upper in ((date(2026, 3, 30), "1.20"), (date(2026, 3, 31), "1.80")):
        db_session.add(
            LiveMetricSnapshot(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                module="rating",
                reporting_period_id=period_id,
                snapshot_date=snapshot_date,
                metrics={"pit_pd_upper_pct": upper, "pit_rating_grade": "bb"},
                status="green",
                computed_at=utc_now(),
            )
        )
    db_session.commit()

    result = window_analytics.compute_window(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        start_date=date(2026, 3, 30),
        end_date=date(2026, 3, 31),
    )

    rating = next(row for row in result.daily if row.module == "rating")
    assert rating.metric_key == "pit_pd_upper_pct"
    assert rating.day_count == 2
    assert rating.min == Decimal("1.20")
    assert rating.max == Decimal("1.80")
    assert rating.avg == Decimal("1.500000")


def _principal_with(
    db: Session, *, module_scope: ModuleScope, sensitivity_scope: SensitivityScope
) -> TenantContext:
    """A fresh human in the demo org holding exactly one Viewer sentence."""
    user = User(
        id=uuid4(),
        organization_id=DEMO_ORG_ID,
        email=f"{module_scope.value}.{sensitivity_scope.value}.viewer@example.test",
        display_name=f"{module_scope.value} {sensitivity_scope.value} viewer",
    )
    db.add(user)
    db.flush()
    authorization.create_role_binding(
        db,
        organization_id=DEMO_ORG_ID,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION, SAMPLE_BANK_ID, module_scope, sensitivity_scope
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "window-analytics-test"),
        reason="Exercise credit daily-ladder gating.",
        commit=False,
    )
    db.flush()
    return TenantContext(
        organization_id=DEMO_ORG_ID,
        actor_user_id=user.id,
        authorization_version=user.authorization_version,
    )


def test_daily_stats_serve_credit_only_to_a_credit_or_all_aggregated_view(
    db_session: Session,
) -> None:
    """Credit daily rows are gated like every other engine (D-017 retro-gate).

    Capital also requires its own authority; Credit never opens Capital.
    """
    materialize_canonical_test_book(db_session)
    period_id = _period_id(db_session)
    snapshot_date = date(2026, 3, 31)
    for module, metrics in (
        ("capital", {"car_pct": "14.1"}),
        ("credit", {"npl_ratio_pct": "6.5"}),
    ):
        db_session.add(
            LiveMetricSnapshot(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                module=module,
                reporting_period_id=period_id,
                snapshot_date=snapshot_date,
                metrics=metrics,
                status="green",
                computed_at=utc_now(),
            )
        )
    db_session.flush()

    def daily_modules(ctx: TenantContext) -> list[str]:
        result = window_analytics.compute_window(
            db_session, ctx, SAMPLE_BANK_ID, start_date=snapshot_date, end_date=snapshot_date
        )
        return [row.module for row in result.daily]

    liquidity_only = _principal_with(
        db_session,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.AGGREGATED,
    )
    assert daily_modules(liquidity_only) == []

    credit_confidential = _principal_with(
        db_session,
        module_scope=ModuleScope.CREDIT,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    # Sensitivity is exact: a confidential credit sentence is not the aggregated one.
    assert daily_modules(credit_confidential) == []

    credit_aggregated = _principal_with(
        db_session,
        module_scope=ModuleScope.CREDIT,
        sensitivity_scope=SensitivityScope.AGGREGATED,
    )
    assert daily_modules(credit_aggregated) == ["credit"]

    # The hermetic fixture's org-wide viewer/all/all sentence keeps seeing it.
    assert daily_modules(MAKER) == ["capital", "credit"]


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
