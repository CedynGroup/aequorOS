from __future__ import annotations

import datetime
import math

from app.baseline import StaticBaseline, forecast_static
from app.features import is_month_end_window
from app.synthetic import generate_daily_series


def test_forecast_static_shape():
    series = generate_daily_series(days=380)
    forecast = forecast_static(series, horizon=30)
    assert len(forecast) == 30
    assert all(math.isfinite(value) for value in forecast)


def test_month_end_bucket_predicts_payroll_outflow():
    series = generate_daily_series(days=380)
    baseline = StaticBaseline.fit(series[:300])
    # 2026-04-29/30 are the last two business days of April 2026.
    month_end_day = datetime.date(2026, 4, 30)
    assert is_month_end_window(month_end_day)
    regular_same_dow = datetime.date(2026, 4, 9)  # also a Thursday
    assert not is_month_end_window(regular_same_dow)
    assert baseline.predict_date(month_end_day) < baseline.predict_date(regular_same_dow) - 5.0


def test_unseen_bucket_falls_back_to_dow_mean():
    series = generate_daily_series(days=380)
    business_only = [f for f in series[:300] if not is_month_end_window(f.date)]
    baseline = StaticBaseline.fit(business_only)
    month_end_day = datetime.date(2026, 4, 30)
    prediction = baseline.predict_date(month_end_day)
    assert prediction == baseline.dow_means[month_end_day.weekday()]
