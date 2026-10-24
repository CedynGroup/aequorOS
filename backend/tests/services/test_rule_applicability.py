from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalPositionSnapshot,
    LiveFinding,
    LiveMetric,
    LiveMetricSnapshot,
    ParamCapitalThreshold,
    RegulatoryParameter,
)
from app.schemas.forecasting import ForecastRunCreate
from app.services import alerts, live_view, regulatory_forecasting, sdi_views
from app.services.fact_derivation import derive_current_facts, derive_facts
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.fixtures.capital_structure import MAKER, REPORTING_DATE, seed_book
from tests.services.test_sdi_capital_summary import _bank, _seed_capital, _seed_positions


@pytest.mark.usefixtures("forecasting_run_authority")
@pytest.mark.parametrize("capital_breach", [False, True])
def test_forecast_assesses_only_in_force_constituents(
    db_session: Session, capital_breach: bool
) -> None:
    seed_book(db_session)
    for threshold in db_session.scalars(select(ParamCapitalThreshold)):
        if threshold.threshold_code in ("lcr_min", "nsfr_min"):
            threshold.value_pct = Decimal("10000")
        if capital_breach and threshold.threshold_code == "car_min":
            threshold.value_pct = Decimal("10000")
    for parameter in db_session.scalars(select(RegulatoryParameter)):
        if capital_breach and parameter.param_code == "car_min":
            parameter.value_numeric = Decimal("10000")
    db_session.flush()
    derived = derive_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    derive_current_facts(db_session, MAKER, SAMPLE_BANK_ID, REPORTING_DATE)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    period = db_session.get(BankReportingPeriod, derived.reporting_period_id)
    assert bank is not None and period is not None
    live = regulatory_forecasting.compute_live(db_session, MAKER, bank, period)
    assert live.status == ("red" if capital_breach else "green")
    assert live.metrics["year5_lcr_status"] == "na"
    assert live.metrics["year5_nsfr_status"] == "na"
    assert {finding.rule_id for finding in live.findings} == (
        {"year5_car_above_minimum"} if capital_breach else set()
    )
    run = regulatory_forecasting.create_forecast_run(
        db_session,
        MAKER,
        bank.id,
        ForecastRunCreate(reporting_period_id=period.id, scenario_code="base"),
    )
    assert run.status == "succeeded", run.error
    assert not any(
        row.rule_code in ("year5_lcr_above_minimum", "year5_nsfr_above_minimum")
        for row in run.validations
    )
    metrics = {row.metric_code: row for row in run.metric_results}
    assert metrics["year5_car_pct"].status == ("red" if capital_breach else "green")
    assert metrics["year5_lcr_pct"].status == "na"
    assert metrics["year5_nsfr_pct"].status == "na"


def test_sdi_exposure_status_and_threshold_change_only_on_directive_commencement(
    db_session: Session,
) -> None:
    bank = _bank(db_session)
    _seed_capital(db_session, bank, [("paid_up_capital", "1000", "CET1")])
    _seed_positions(db_session, bank, [("LOAN/1", "LOAN", "160")])
    snapshot = db_session.scalars(
        select(CanonicalPositionSnapshot).where(CanonicalPositionSnapshot.bank_id == bank.id)
    ).one()
    snapshot.attributes = {**snapshot.attributes, "issuer": "Synthetic obligor"}
    db_session.flush()
    for as_of, limit, status in [
        (date(2026, 12, 31), Decimal("25"), "ok"),
        (date(2027, 1, 1), Decimal("15"), "above_limit"),
    ]:
        snapshot.as_of_date = as_of
        db_session.flush()
        result = sdi_views.get_sdi_large_exposures(db_session, MAKER, bank, as_of)
        assert len(result.exposures) == 1
        exposure = result.exposures[0]
        assert exposure.pct_net_own_funds == Decimal("16")
        assert exposure.single_obligor_limit_pct == Decimal("25")
        assert exposure.large_exposure_limit_pct == Decimal("15")
        assert exposure.applicable_limit_pct == limit
        assert exposure.status == status


@pytest.mark.usefixtures("forecasting_run_authority")
def test_cached_forecast_views_and_alert_counts_drop_reference_checks(db_session: Session) -> None:
    seed_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    period = db_session.scalars(
        select(BankReportingPeriod).where(BankReportingPeriod.bank_id == bank.id)
    ).first()
    assert period is not None
    metrics = {
        "year5_car_pct": "20",
        "year5_lcr_pct": "80",
        "year5_nsfr_pct": "90",
        "year5_car_status": "green",
        "year5_lcr_status": "red",
        "year5_nsfr_status": "red",
    }
    db_session.add(
        LiveMetric(
            organization_id=bank.organization_id,
            bank_id=bank.id,
            module="forecast",
            metrics=metrics,
            status="red",
            computed_at=datetime.now(UTC),
        )
    )
    db_session.add(
        LiveMetricSnapshot(
            organization_id=bank.organization_id,
            bank_id=bank.id,
            module="forecast",
            reporting_period_id=period.id,
            snapshot_date=date.today(),
            metrics=metrics,
            status="red",
            computed_at=datetime.now(UTC),
        )
    )
    for ratio in ("car", "lcr", "nsfr"):
        db_session.add(
            LiveFinding(
                organization_id=bank.organization_id,
                bank_id=bank.id,
                module="forecast",
                rule_id=f"year5_{ratio}_above_minimum",
                severity="critical",
                message=ratio,
            )
        )
    db_session.flush()
    summary = live_view.get_live_summary(db_session, MAKER, bank.id)
    assert next(row for row in summary.modules if row.module == "forecast").status == "green"
    snapshots = live_view.list_live_snapshots(db_session, MAKER, bank.id, module="forecast")
    assert snapshots.snapshots[0].status == "green"
    feed = alerts.get_bank_alerts(db_session, MAKER, bank.id)
    assert [item.rule_id for item in feed.items] == ["year5_car_above_minimum"]
    assert feed.total == 1
    assert feed.by_severity == {"critical": 1}
    assert feed.by_module == {"forecast": 1}
