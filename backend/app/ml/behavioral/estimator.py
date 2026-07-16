"""Shared estimator core: gate → pooled GBM (or baseline) → CV → confidence.

Each model module reduces a bank's history to per-product-month observations
(numeric features + a label) and calls :func:`estimate`. This routine:
- gates on data volume (``min_samples`` labels AND ``min_months`` distinct months);
- above the gate, trains one pooled ``HistGradientBoostingRegressor`` with
  ``product_code`` as a native categorical, scored by expanding-window CV;
- below the gate, or if sklearn is unavailable/fails, falls back to the
  per-product empirical mean (else a generic prior) with a capped confidence.

sklearn is imported lazily so a broken/missing install degrades to the baseline
rather than breaking the service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from app.ml.behavioral.config import (
    MODEL_VERSIONS,
    Accuracy,
    BehavioralTrainingConfig,
    ModelResult,
    ProductEstimate,
)
from app.ml.behavioral.features import clamp, compute_confidence, timeseries_cv_rmse

_TARGET_N = 60        # product-month samples for full data-sufficiency
_TARGET_MONTHS = 24   # month coverage for full data-sufficiency


@dataclass(frozen=True, slots=True)
class Predictor:
    """Handle to the fitted GBM so callers can do extra grid predictions."""

    model: object
    code_to_idx: dict[str, int]

    def predict_num(self, x_num_rows: np.ndarray, product_code: str) -> np.ndarray:
        idx = self.code_to_idx.get(product_code)
        if idx is None:
            return np.full(len(x_num_rows), np.nan)
        ord_col = np.full((len(x_num_rows), 1), float(idx))
        return self.model.predict(np.hstack([np.asarray(x_num_rows, dtype=float), ord_col]))


def _make_gbm(cfg: BehavioralTrainingConfig, cat_index: list[int]):
    from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: PLC0415 - lazy heavy import

    return HistGradientBoostingRegressor(
        max_leaf_nodes=cfg.max_leaf_nodes,
        min_samples_leaf=cfg.min_samples_leaf,
        l2_regularization=cfg.l2_regularization,
        max_iter=cfg.max_iter,
        learning_rate=cfg.learning_rate,
        random_state=cfg.random_state,
        categorical_features=cat_index,
    )


def estimate(  # noqa: PLR0913, PLR0915
    *,
    model_slug: str,
    x_num: np.ndarray,
    products: list[str],
    y: list[float],
    months: list[int],
    latest_by_product: dict[str, np.ndarray],
    cfg: BehavioralTrainingConfig,
    assumption_type: str,
    unit: str,
    clamp_lo: float,
    clamp_hi: float,
    generic_prior: float,
    extra_by_product: dict[str, dict] | None = None,
) -> tuple[ModelResult, Predictor | None]:
    model_id = MODEL_VERSIONS[model_slug]
    prod_arr = np.asarray(products)
    y_arr = np.asarray(y, dtype=float)
    months_arr = np.asarray(months)
    uniq = sorted(set(products))
    n = int(y_arr.size)
    coverage = int(np.unique(months_arr).size) if n else 0
    label_std = float(np.std(y_arr)) if n else 0.0

    method = "ml" if (n >= cfg.min_samples and coverage >= cfg.min_months) else "baseline"
    cv_rmse: float | None = None
    cv_mae: float | None = None
    trained = None  # (model, code_to_idx)

    if method == "ml":
        try:
            code_to_idx = {c: i for i, c in enumerate(uniq)}
            prod_ord = np.array([[code_to_idx[p]] for p in products], dtype=float)
            # HGB cannot bin an entirely-NaN column (a real bank may have one, e.g.
            # a feature it never populates); neutralize such columns to 0.
            num = np.asarray(x_num, dtype=float)
            if num.size:
                all_nan = np.all(np.isnan(num), axis=0)
                if all_nan.any():
                    num[:, all_nan] = 0.0
            x = np.hstack([num, prod_ord])
            cat_index = [x.shape[1] - 1]

            def fit_predict(xt, yt, xv):
                m = _make_gbm(cfg, cat_index)
                m.fit(xt, yt)
                return m.predict(xv)

            cv_rmse, cv_mae = timeseries_cv_rmse(fit_predict, x, y_arr, months_arr, cfg.cv_folds)
            model = _make_gbm(cfg, cat_index)
            model.fit(x, y_arr)
            trained = (model, code_to_idx)
        except Exception as exc:  # noqa: BLE001 - sklearn missing/broken → degrade to baseline
            logging.getLogger(__name__).warning(
                "behavioral GBM training failed for %s (%s); using baseline.", model_slug, exc)
            method = "baseline"

    # per-product empirical mean (baseline value + fallback for unpredictable products)
    empirical: dict[str, float] = {}
    for p in uniq:
        mask = prod_arr == p
        if mask.any():
            empirical[p] = float(np.mean(y_arr[mask]))

    extra_by_product = extra_by_product or {}
    products_out: list[ProductEstimate] = []
    # union of products seen in labels and products we can score at the latest month
    for p in sorted(set(uniq) | set(latest_by_product)):
        if trained is not None and p in latest_by_product and p in trained[1]:
            model, code_to_idx = trained
            xrow = np.hstack([latest_by_product[p], [float(code_to_idx[p])]]).reshape(1, -1)
            value = float(model.predict(xrow)[0])
            pmethod = "ml"
        else:
            value = empirical.get(p, generic_prior)
            pmethod = "baseline"
        value = clamp(value, clamp_lo, clamp_hi)
        n_p = int((prod_arr == p).sum())
        conf = compute_confidence(
            pmethod, n_p, _TARGET_N, coverage, _TARGET_MONTHS, cv_rmse, label_std)
        products_out.append(ProductEstimate(
            product_code=p, assumption_type=assumption_type, value=round(value, 6),
            unit=unit, confidence=conf, method=pmethod, extra=extra_by_product.get(p, {}),
        ))

    products_out.sort(key=lambda e: e.product_code)
    accuracy = Accuracy(cv_rmse=cv_rmse, cv_mae=cv_mae, sample_count=n, month_coverage=coverage,
                        method=method)
    result = ModelResult(model_id=model_id, model_version=model_id, method=method, as_of_date=None,
                         accuracy=accuracy, products=products_out)
    predictor = Predictor(model=trained[0], code_to_idx=trained[1]) if trained is not None else None
    return result, predictor
