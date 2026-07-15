"""Static behavioral baseline: mean net flow by calendar bucket.

Buckets are keyed by (day-of-week, is-month-end-window, is-payday). The
baseline is always fitted on the history it is given -- for holdout metrics
that is the TRAIN window only, so it never sees validation data.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from app.ml.features import is_month_end_window, is_payday
from app.ml.synthetic import DailyFlow

BucketKey = tuple[int, bool, bool]


def _bucket_key(day: datetime.date) -> BucketKey:
    return (day.weekday(), is_month_end_window(day), is_payday(day))


@dataclass(frozen=True, slots=True)
class StaticBaseline:
    """Bucket means with day-of-week and global fallbacks for unseen buckets."""

    bucket_means: dict[BucketKey, float]
    dow_means: dict[int, float]
    global_mean: float

    @classmethod
    def fit(cls, history: Sequence[DailyFlow]) -> StaticBaseline:
        if not history:
            raise ValueError("history must not be empty")
        by_bucket: dict[BucketKey, list[float]] = defaultdict(list)
        by_dow: dict[int, list[float]] = defaultdict(list)
        for flow in history:
            by_bucket[_bucket_key(flow.date)].append(flow.net)
            by_dow[flow.date.weekday()].append(flow.net)
        return cls(
            bucket_means={key: fmean(values) for key, values in by_bucket.items()},
            dow_means={dow: fmean(values) for dow, values in by_dow.items()},
            global_mean=fmean(flow.net for flow in history),
        )

    def predict_date(self, day: datetime.date) -> float:
        key = _bucket_key(day)
        if key in self.bucket_means:
            return self.bucket_means[key]
        if day.weekday() in self.dow_means:
            return self.dow_means[day.weekday()]
        return self.global_mean

    def predict(self, dates: Sequence[datetime.date]) -> list[float]:
        return [self.predict_date(day) for day in dates]


def forecast_static(history: Sequence[DailyFlow], horizon: int) -> list[float]:
    """Forecast the next ``horizon`` daily net flows after the end of ``history``."""
    model = StaticBaseline.fit(history)
    last = history[-1].date
    future = [last + datetime.timedelta(days=step) for step in range(1, horizon + 1)]
    return model.predict(future)
