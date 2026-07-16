"""Loan-prepayment model: annual CPR per loan product + a rate-incentive curve.

From per-loan monthly snapshots, realized prepayment per product-month is the
unscheduled principal — partial paydowns on surviving loans plus early closures
(a loan that vanishes before maturity) — over the beginning balance. SMM →
annual CPR is the label. Features: rate incentive (note − refi), age, remaining
term, balance, macro rate, lagged CPR. Pooled GBM; the fitted model is swept
over an incentive grid for the UI curve. Output: ``PREPAYMENT_RATE`` annual.
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
from app.ml.behavioral.features import month_num, month_sin_cos

_SLUG = "prepayment"
_N_FEATURES = 9
_CURVE_BPS = list(range(-200, 301, 50))  # incentive grid for the UI partial-dependence curve


def _cpr_from_smm(unscheduled: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    smm = max(0.0, min(1.0, unscheduled / denominator))
    return 1.0 - (1.0 - smm) ** 12


def estimate(db: Session, ctx: TenantContext, bank_id: UUID, as_of: datetime.date,  # noqa: PLR0912, PLR0915
             cfg: BehavioralTrainingConfig) -> ModelResult:
    rows = history.load_loan_month_rows(db, ctx, bank_id, as_of, cfg.window_months)
    short = history.load_ghs_short_rate_history(db, ctx, bank_id, as_of, cfg.window_months)

    loans: dict[str, dict[int, history.LoanMonthRow]] = defaultdict(dict)
    all_months: set[int] = set()
    mn_to_date: dict[int, datetime.date] = {}
    for r in rows:
        mn = month_num(r.as_of_date)
        loans[r.source_reference][mn] = r
        all_months.add(mn)
        mn_to_date[mn] = r.as_of_date
    ordered = sorted(all_months)
    next_of = {m: ordered[i + 1] for i, m in enumerate(ordered[:-1])}
    as_of_mn = month_num(as_of)

    # per (product, month): prepayment numerator/denominator + balance-weighted features
    cells: dict[tuple[str, int], dict] = defaultdict(
        lambda: {"unsch": 0.0, "den": 0.0, "rate_num": 0.0, "rate_bal": 0.0,
                 "age_num": 0.0, "rem_num": 0.0, "bal": 0.0})
    for months_map in loans.values():
        for mn, r in months_map.items():
            bal = r.balance_ghs
            if bal <= 0:
                continue
            pc = r.product_code or "<none>"
            cell = cells[(pc, mn)]
            # prepayment observation requires seeing the next month
            if mn in next_of:
                tnext = next_of[mn]
                nr = months_map.get(tnext)
                if nr is None:  # loan vanished → early closure counts as prepayment
                    mat_mn = month_num(r.contractual_maturity) if r.contractual_maturity else None
                    closed_early = tnext <= as_of_mn and (mat_mn is None or mat_mn > tnext)
                    cell["unsch"] += bal if closed_early else 0.0
                else:
                    cell["unsch"] += max(0.0, bal - nr.balance_ghs - nr.scheduled_principal_ghs)
                cell["den"] += bal
            # features (accumulated for every month, incl. the as-of month for prediction)
            if r.interest_rate is not None:
                cell["rate_num"] += r.interest_rate * bal
                cell["rate_bal"] += bal
            if r.contractual_maturity is not None:
                cell["rem_num"] += ((r.contractual_maturity - r.as_of_date).days / 30.0) * bal
            cell["age_num"] += (r.months_on_book or 0) * bal
            cell["bal"] += bal

    # assemble per (product, month) feature rows + CPR labels, then lagged CPR
    cpr: dict[tuple[str, int], float] = {}
    feats: dict[tuple[str, int], list[float]] = {}
    for (pc, mn), c in cells.items():
        mdate = mn_to_date[mn]
        avg_note = c["rate_num"] / c["rate_bal"] if c["rate_bal"] > 0 else np.nan
        sr = short.get(mdate, np.nan)
        incentive = (avg_note - sr) if not (np.isnan(avg_note) or np.isnan(sr)) else np.nan
        avg_age = c["age_num"] / c["bal"] if c["bal"] > 0 else np.nan
        avg_rem = c["rem_num"] / c["bal"] if c["bal"] > 0 else np.nan
        s, co = month_sin_cos(mdate)
        feats[(pc, mn)] = [incentive, avg_note, avg_age, avg_rem,
                           float(np.log1p(c["bal"])), sr, s, co, np.nan]  # lagged filled below
        if c["den"] > 0 and mn in next_of:
            cpr[(pc, mn)] = _cpr_from_smm(c["unsch"], c["den"])

    x_num: list[list[float]] = []
    products: list[str] = []
    y: list[float] = []
    months: list[int] = []
    latest: dict[str, np.ndarray] = {}
    by_product_months: dict[str, list[int]] = defaultdict(list)
    for (pc, mn) in feats:
        by_product_months[pc].append(mn)
    for pc, mns in by_product_months.items():
        mns.sort()
        for i, mn in enumerate(mns):
            row = list(feats[(pc, mn)])
            row[8] = cpr.get((pc, mns[i - 1]), np.nan) if i > 0 else np.nan  # lagged CPR
            if mn == max(mns):
                latest[pc] = np.asarray(row, dtype=float)
            if (pc, mn) in cpr:
                x_num.append(row)
                products.append(pc)
                y.append(cpr[(pc, mn)])
                months.append(mn)

    lo, hi = VALUE_RANGE[_SLUG]
    x_arr = (np.asarray(x_num, dtype=float).reshape(-1, _N_FEATURES)
             if x_num else np.empty((0, _N_FEATURES)))
    result, predictor = _estimate(
        model_slug=_SLUG, x_num=x_arr, products=products, y=y, months=months,
        latest_by_product=latest, cfg=cfg, assumption_type=ASSUMPTION_TYPE[_SLUG],
        unit=UNIT[_SLUG], clamp_lo=lo, clamp_hi=hi, generic_prior=GENERIC_PRIOR[_SLUG])

    # rate-incentive partial-dependence curve per product (ml only)
    if predictor is not None:
        curves: dict[str, dict] = {}
        for pc, base in latest.items():
            grid_rows = []
            for bps in _CURVE_BPS:
                r = base.copy()
                r[0] = bps / 10000.0  # incentive column
                grid_rows.append(r)
            preds = predictor.predict_num(np.asarray(grid_rows, dtype=float), pc)
            if not np.all(np.isnan(preds)):
                curves[pc] = {"incentiveCurve": [
                    {"incentiveBps": bps, "cpr": round(float(min(max(p, lo), hi)), 6)}
                    for bps, p in zip(_CURVE_BPS, preds, strict=True)
                ]}
        result = _attach_extra(result, curves)
    return result


def _attach_extra(result: ModelResult, curves: dict[str, dict]) -> ModelResult:
    from app.ml.behavioral.config import ProductEstimate  # noqa: PLC0415

    products = [
        ProductEstimate(
            product_code=p.product_code, assumption_type=p.assumption_type, value=p.value,
            unit=p.unit, confidence=p.confidence, method=p.method,
            extra={**p.extra, **curves.get(p.product_code, {})},
        )
        for p in result.products
    ]
    return ModelResult(model_id=result.model_id, model_version=result.model_version,
                       method=result.method, as_of_date=result.as_of_date,
                       accuracy=result.accuracy, products=products)
