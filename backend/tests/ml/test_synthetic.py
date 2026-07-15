from __future__ import annotations

import datetime
from statistics import fmean, median

from app.ml.features import is_business_day, is_mid_month, is_month_end_window, is_payday
from app.ml.synthetic import DEMO_AS_OF_DATE, generate_daily_series


def test_same_seed_is_deterministic():
    assert generate_daily_series(seed=42) == generate_daily_series(seed=42)


def test_different_seed_changes_series():
    assert generate_daily_series(seed=42) != generate_daily_series(seed=7)


def test_series_spans_expected_dates():
    series = generate_daily_series(days=730)
    assert len(series) == 730
    assert series[-1].date == DEMO_AS_OF_DATE == datetime.date(2026, 3, 31)
    assert series[0].date == DEMO_AS_OF_DATE - datetime.timedelta(days=729)
    for flow in series:
        assert flow.net == round(flow.inflow - flow.outflow, 6)


def test_month_end_outflow_spike():
    series = generate_daily_series()
    month_end_outflows = [f.outflow for f in series if is_month_end_window(f.date)]
    median_outflow = median(f.outflow for f in series)
    assert fmean(month_end_outflows) > 1.8 * median_outflow


def test_weekends_carry_reduced_volume():
    series = generate_daily_series()
    quiet_business = [
        f
        for f in series
        if is_business_day(f.date)
        and not (is_month_end_window(f.date) or is_payday(f.date) or is_mid_month(f.date))
    ]
    weekends = [f for f in series if not is_business_day(f.date) and not is_payday(f.date)]
    assert fmean(f.inflow for f in weekends) < 0.25 * fmean(f.inflow for f in quiet_business)


def test_payday_and_mid_month_inflow_bumps():
    series = generate_daily_series()
    quiet_business = [
        f.inflow
        for f in series
        if is_business_day(f.date)
        and not (is_month_end_window(f.date) or is_payday(f.date) or is_mid_month(f.date))
    ]
    payday_business = [f.inflow for f in series if is_business_day(f.date) and is_payday(f.date)]
    mid_month_business = [
        f.inflow for f in series if is_business_day(f.date) and is_mid_month(f.date)
    ]
    assert fmean(payday_business) > fmean(quiet_business) + 3.0
    assert fmean(mid_month_business) > fmean(quiet_business) + 0.5
