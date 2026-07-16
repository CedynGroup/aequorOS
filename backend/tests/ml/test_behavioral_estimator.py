"""Robustness contract for the behavioral estimator core (DB-free).

Covers the "train on ANY bank's data" guarantees: the ML gate, graceful
baseline fallback on thin data, bounded confidence, an all-NaN feature not
crashing training, and a generic prior for a product with no labels.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.behavioral.config import BehavioralTrainingConfig
from app.ml.behavioral.estimator import estimate

_CFG = BehavioralTrainingConfig(min_months=3, min_samples=6, cv_folds=2)


def _panel(n_products: int, n_months: int, n_features: int = 4, *, seed: int = 0):
    rng = np.random.default_rng(seed)
    x, products, y, months, latest = [], [], [], [], {}
    for p in range(n_products):
        code = f"P{p}"
        base = rng.uniform(0.2, 0.8)
        for m in range(n_months):
            feat = rng.normal(size=n_features).tolist()
            x.append(feat)
            products.append(code)
            y.append(base + rng.normal(0, 0.02))
            months.append(m)
        latest[code] = np.array(x[-1], dtype=float)
    return dict(x_num=np.array(x, dtype=float), products=products, y=y, months=months,
               latest_by_product=latest)


def _call(panel):
    return estimate(
        model_slug="deposit-stability", cfg=_CFG, assumption_type="DEPOSIT_STABILITY",
        unit="ratio", clamp_lo=0.0, clamp_hi=1.0, generic_prior=0.5, **panel)


def test_ml_when_enough_data():
    result, predictor = _call(_panel(4, 12))
    assert result.method == "ml"
    assert predictor is not None
    assert len(result.products) == 4
    for p in result.products:
        assert 0.0 <= p.value <= 1.0
        assert 0.0 <= p.confidence <= 1.0
        assert p.method == "ml"


def test_baseline_when_thin_data():
    # 1 product x 2 months = 2 samples < min_samples(6) → baseline
    result, predictor = _call(_panel(1, 2))
    assert result.method == "baseline"
    assert predictor is None
    assert result.products and all(p.method == "baseline" for p in result.products)
    # baseline confidence is capped well below ml
    assert all(p.confidence <= 0.35 for p in result.products)


def test_all_nan_feature_column_does_not_crash():
    panel = _panel(4, 12)
    panel["x_num"][:, 1] = np.nan  # an entirely-unpopulated feature
    for arr in panel["latest_by_product"].values():
        arr[1] = np.nan
    result, _ = _call(panel)
    assert result.method == "ml"  # degenerate column neutralized, not fatal


def test_generic_prior_for_product_without_labels():
    panel = _panel(3, 12)
    # a product we can score at latest month but that has no training labels
    panel["latest_by_product"]["UNSEEN"] = np.zeros(panel["x_num"].shape[1])
    result, _ = _call(panel)
    unseen = next(p for p in result.products if p.product_code == "UNSEEN")
    assert unseen.method == "baseline"
    assert unseen.value == pytest.approx(0.5, abs=1e-6)  # generic prior
