"""Shared, dependency-light feature engineering + confidence scoring (numpy only).

Model-specific feature matrices are built in each model module; this holds the
common time-series statistics, the calendar/seasonality helpers, and the
per-product confidence score used by all three models.
"""

from __future__ import annotations

import datetime

import numpy as np


def month_num(d: datetime.date) -> int:
    return d.year * 12 + (d.month - 1)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def trailing_series_features(values: list[float]) -> dict[str, float]:
    """Level/volatility/growth features from a product's balance series up to t."""
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return {"cov": 0.0, "min_mean": 0.0, "g3": 0.0, "g6": 0.0, "g12": 0.0,
                "log_level": 0.0, "n_obs": 0.0}
    mean = float(v.mean())
    cov = float(v.std() / mean) if mean > 0 else 0.0
    min_mean = float(v.min() / mean) if mean > 0 else 0.0

    def growth(k: int) -> float:
        if v.size > k and v[-1 - k] > 0:
            return float(v[-1] / v[-1 - k] - 1.0)
        return 0.0

    return {
        "cov": cov, "min_mean": min_mean,
        "g3": growth(3), "g6": growth(6), "g12": growth(12),
        "log_level": float(np.log1p(max(v[-1], 0.0))),
        "n_obs": float(v.size),
    }


def stability_score(feats: dict[str, float]) -> float:
    """0..1 core-stability score from balance features (high = sticky, low-vol)."""
    return clamp(feats.get("min_mean", 0.0) * (1.0 - clamp(feats.get("cov", 0.0), 0.0, 1.0)),
                 0.0, 1.0)


def month_sin_cos(d: datetime.date) -> tuple[float, float]:
    ang = 2.0 * np.pi * (d.month - 1) / 12.0
    return float(np.sin(ang)), float(np.cos(ang))


def compute_confidence(  # noqa: PLR0913
    method: str, n_samples: int, target_n: int, coverage_months: int, target_months: int,
    cv_rmse: float | None, label_std: float | None,
) -> float:
    """confidence = method_cap x data_sufficiency x skill, in [0,1]."""
    cap = 1.0 if method == "ml" else 0.35
    suff = (min(1.0, n_samples / max(target_n, 1))
            * min(1.0, coverage_months / max(target_months, 1)))
    if cv_rmse is not None and label_std and label_std > 0:
        skill = clamp(1.0 - cv_rmse / label_std, 0.0, 1.0)
    else:
        skill = 0.5
    return round(cap * suff * skill, 3)


def timeseries_cv_rmse(
    fit_predict, x: np.ndarray, y: np.ndarray, month_index: np.ndarray, folds: int,
) -> tuple[float | None, float | None]:
    """Expanding-window forward-chaining CV over the month axis. Returns (rmse, mae)."""
    months = np.unique(month_index)
    if months.size < folds + 1:
        return None, None
    cut_points = months[np.linspace(1, months.size - 1, folds, dtype=int)]
    errs: list[np.ndarray] = []
    for cut in cut_points:
        train = month_index < cut
        val = month_index == cut
        if train.sum() < 5 or val.sum() == 0:
            continue
        try:
            pred = fit_predict(x[train], y[train], x[val])
        except Exception:  # noqa: BLE001 - a bad fold degrades CV, not the run
            continue
        errs.append(np.asarray(y[val], float) - np.asarray(pred, float))
    if not errs:
        return None, None
    e = np.concatenate(errs)
    return float(np.sqrt(np.mean(e**2))), float(np.mean(np.abs(e)))
