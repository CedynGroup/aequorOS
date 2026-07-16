"""NMD-duration model: effective behavioral duration (months) of non-maturity deposits.

Label per product-month = an empirical effective duration derived from the
trailing balance's core-stability score (sticky, low-volatility books map to
longer duration). Features summarize balance dynamics, scale, rate, and macro
rate. Pooled GBM over non-maturing deposit products; falls back to the
per-product empirical mean. Output: ``NMD_DURATION`` months (+ ``corePct``);
consumed by ``fact_derivation`` → ftp_nmd / ftp_product / irr_position.
"""

from __future__ import annotations

import datetime
from uuid import UUID

import numpy as np
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.ml.behavioral import history
from app.ml.behavioral.baseline import GENERIC_PRIOR, VALUE_RANGE
from app.ml.behavioral.config import ASSUMPTION_TYPE, UNIT, BehavioralTrainingConfig, ModelResult
from app.ml.behavioral.deposit_stability import _feature_row, _product_series
from app.ml.behavioral.estimator import estimate as _estimate
from app.ml.behavioral.features import month_num, stability_score, trailing_series_features

_SLUG = "nmd-duration"
_FLOOR_MONTHS = 6.0   # duration of a maximally-volatile NMD book
_CEIL_MONTHS = 84.0   # duration of a maximally-sticky NMD book


def _duration_from_stability(score: float) -> float:
    """Map a 0..1 core-stability score to an effective duration (months)."""
    return _FLOOR_MONTHS + (_CEIL_MONTHS - _FLOOR_MONTHS) * score


def estimate(db: Session, ctx: TenantContext, bank_id: UUID, as_of: datetime.date,
             cfg: BehavioralTrainingConfig) -> ModelResult:
    aggs = history.load_deposit_month_aggregates(
        db, ctx, bank_id, as_of, cfg.window_months, non_maturing_only=True)
    short = history.load_ghs_short_rate_history(db, ctx, bank_id, as_of, cfg.window_months)
    series = _product_series(aggs)

    x_num: list[list[float]] = []
    products: list[str] = []
    y: list[float] = []
    months: list[int] = []
    latest: dict[str, np.ndarray] = {}
    extra: dict[str, dict] = {}

    for pc, points in series.items():
        balances = [p[1] for p in points]
        for i, (d, _bal, n_acc, rate) in enumerate(points):
            trail = balances[: i + 1]
            feats = trailing_series_features(trail)
            row = _feature_row(trail, d, n_acc, rate, short.get(d, np.nan))
            label = _duration_from_stability(stability_score(feats))
            x_num.append(row)
            products.append(pc)
            y.append(label)
            months.append(month_num(d))
            if i == len(points) - 1:
                latest[pc] = np.asarray(row, dtype=float)
                extra[pc] = {"corePct": round(feats["min_mean"], 4)}

    lo, hi = VALUE_RANGE[_SLUG]
    result, _ = _estimate(
        model_slug=_SLUG,
        x_num=np.asarray(x_num, dtype=float).reshape(-1, 12) if x_num else np.empty((0, 12)),
        products=products, y=y, months=months, latest_by_product=latest, cfg=cfg,
        assumption_type=ASSUMPTION_TYPE[_SLUG], unit=UNIT[_SLUG],
        clamp_lo=lo, clamp_hi=hi, generic_prior=GENERIC_PRIOR[_SLUG], extra_by_product=extra)
    return result
