"""Deposit-stability model: the sticky fraction of a deposit book, per product.

Label per product-month = forward retained fraction ``min(balance over next N
months) / balance_now``, clamped [0,1]. Features summarize the trailing balance
dynamics (volatility, min/mean, growth), scale, rate, and macro rate. Pooled GBM
over all deposit products; falls back to the per-product empirical retained
fraction when history is thin. Output: ``DEPOSIT_STABILITY`` fraction (consumed
by ``fact_derivation._split_deposits`` → LCR/FTP).
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from uuid import UUID

import numpy as np
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.ml.behavioral import history
from app.ml.behavioral.baseline import GENERIC_PRIOR, VALUE_RANGE
from app.ml.behavioral.config import ASSUMPTION_TYPE, UNIT, BehavioralTrainingConfig, ModelResult
from app.ml.behavioral.estimator import estimate as _estimate
from app.ml.behavioral.features import month_num, month_sin_cos, trailing_series_features

_SLUG = "deposit-stability"


def _product_series(aggs) -> dict[str, list]:
    """Collapse (product, counterparty_type, month) → per-product monthly points."""
    by_pm: dict[tuple[str, datetime.date], dict] = defaultdict(
        lambda: {"balance": 0.0, "n": 0, "rate_num": 0.0, "rate_den": 0.0})
    for a in aggs:
        cell = by_pm[(a.product_code, a.as_of_date)]
        cell["balance"] += a.balance_ghs
        cell["n"] += a.n_accounts
        if a.avg_rate is not None:
            cell["rate_num"] += a.avg_rate * a.balance_ghs
            cell["rate_den"] += a.balance_ghs
    series: dict[str, list] = defaultdict(list)
    for (pc, d), cell in by_pm.items():
        rate = cell["rate_num"] / cell["rate_den"] if cell["rate_den"] > 0 else np.nan
        series[pc].append((d, cell["balance"], cell["n"], rate))
    for points in series.values():
        points.sort(key=lambda t: t[0])
    return series


def _feature_row(balances: list[float], d: datetime.date, n_accounts: int, rate: float,
                 short_rate: float) -> list[float]:
    f = trailing_series_features(balances)
    s, c = month_sin_cos(d)
    return [f["cov"], f["min_mean"], f["g3"], f["g6"], f["g12"], f["log_level"], f["n_obs"],
            float(np.log1p(max(n_accounts, 0))), rate, short_rate, s, c]


def estimate(db: Session, ctx: TenantContext, bank_id: UUID, as_of: datetime.date,
             cfg: BehavioralTrainingConfig) -> ModelResult:
    aggs = history.load_deposit_month_aggregates(
        db, ctx, bank_id, as_of, cfg.window_months, non_maturing_only=False)
    short = history.load_ghs_short_rate_history(db, ctx, bank_id, as_of, cfg.window_months)
    series = _product_series(aggs)

    x_num: list[list[float]] = []
    products: list[str] = []
    y: list[float] = []
    months: list[int] = []
    latest: dict[str, np.ndarray] = {}
    fw = cfg.forward_window

    for pc, points in series.items():
        balances = [p[1] for p in points]
        for i, (d, bal, n_acc, rate) in enumerate(points):
            sr = short.get(d, np.nan)
            row = _feature_row(balances[: i + 1], d, n_acc, rate, sr)
            if i == len(points) - 1:
                latest[pc] = np.asarray(row, dtype=float)
            if i + fw < len(points) and bal > 0:
                future_min = min(balances[i + 1: i + 1 + fw])
                label = max(0.0, min(1.0, future_min / bal))
                x_num.append(row)
                products.append(pc)
                y.append(label)
                months.append(month_num(d))

    lo, hi = VALUE_RANGE[_SLUG]
    result, _ = _estimate(
        model_slug=_SLUG,
        x_num=np.asarray(x_num, dtype=float).reshape(-1, 12) if x_num else np.empty((0, 12)),
        products=products, y=y, months=months, latest_by_product=latest, cfg=cfg,
        assumption_type=ASSUMPTION_TYPE[_SLUG], unit=UNIT[_SLUG],
        clamp_lo=lo, clamp_hi=hi, generic_prior=GENERIC_PRIOR[_SLUG])
    return result
