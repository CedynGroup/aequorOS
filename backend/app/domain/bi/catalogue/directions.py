"""Favourable direction per engine metric, declared explicitly.

The substantive judgment ("when this figure rises, is the bank stronger or
weaker?") lives in ``app.services.report_comparison`` — the module the
comparison and board-pack surfaces already use. ``app.domain`` may not import
``app.services``, so the catalogue restates the direction for each engine
metric it exposes, and ``tests/services/bi/test_catalogue_directions.py``
asserts, for EVERY engine measure, that the value here equals
``report_comparison.favorable_direction(metric_id)``. A metric absent from this
table is ``neutral``, exactly as it is there.

Why restate rather than derive: a thin adapter that takes the mapping as input
would still leave the catalogue unable to build itself without a service
import, and the cost of a stale entry here is one failing parity test, which
is the cheaper contract.
"""

from __future__ import annotations

from app.domain.bi.catalogue.members import FavourableDirection

ENGINE_DIRECTIONS: dict[str, FavourableDirection] = {
    # Capital adequacy and resources: a bigger buffer over the minimum is safer.
    "car_pct": "higher_better",
    "tier1_ratio_pct": "higher_better",
    "cet1_ratio_pct": "higher_better",
    "leverage_ratio_pct": "higher_better",
    "total_capital_ghs": "higher_better",
    "year5_car_pct": "higher_better",
    # Liquidity coverage and stable funding.
    "lcr_pct": "higher_better",
    "nsfr_pct": "higher_better",
    "hqla_total_ghs": "higher_better",
    "asf_total_ghs": "higher_better",
    "provision_coverage_pct": "higher_better",
    # Profitability, margin and contribution.
    "avg_roe_pct": "higher_better",
    "portfolio_nim_pct": "higher_better",
    "weighted_asset_yield_pct": "higher_better",
    "total_branch_contribution_ghs": "higher_better",
    # Interest-rate risk: more economic value / base earnings is stronger.
    "eve_base_ghs": "higher_better",
    "nii_base_ghs": "higher_better",
    # Risk-weighted assets consume capital.
    "total_rwa_ghs": "lower_better",
    "credit_rwa_ghs": "lower_better",
    "market_rwa_ghs": "lower_better",
    "operational_rwa_ghs": "lower_better",
    # Asset quality.
    "npl_ratio_pct": "lower_better",
    "npl_exposure_ghs": "lower_better",
    "par_30_pct": "lower_better",
    "par_60_pct": "lower_better",
    "par_90_pct": "lower_better",
    "unclassified_exposure_ghs": "lower_better",
    # Expected credit loss: larger allowances mean worse asset quality.
    "ecl_total_ghs": "lower_better",
    "ecl_general_ghs": "lower_better",
    "ecl_specific_ghs": "lower_better",
    # FX open position and value at risk: larger exposure is riskier.
    "nop_pct_tier1": "lower_better",
    "single_ccy_max_pct": "lower_better",
    "nop_ghs": "lower_better",
    "var_99_1d_ghs": "lower_better",
    "stressed_var_ghs": "lower_better",
    # Signed deltas judged on magnitude (D-013 / H-007).
    "worst_eve_change_pct_tier1": "magnitude_lower_better",
    "ear_up_200_ghs": "magnitude_lower_better",
    "ear_up_450_ghs": "magnitude_lower_better",
    "ear_down_200_ghs": "magnitude_lower_better",
    "ear_down_450_ghs": "magnitude_lower_better",
}


def engine_direction(metric_id: str) -> FavourableDirection:
    """The declared direction, ``neutral`` when the metric is unlisted."""
    return ENGINE_DIRECTIONS.get(metric_id, "neutral")
