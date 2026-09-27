"""``certified_engine`` measures: one per tier-produced registry authority × tier.

An engine measure is a COPY of a figure the regulatory plane computed
(``bi_fact_engine_metric``), never a recomputation. It is keyed by
``(metric_id, regime)`` because fifteen metric ids are multi-authority
(``car_pct`` under CRD and s.29 are different law), and by tier because the
live and sealed figures are never blended (H-008). Each carries the registry's
advisory designation verbatim (D-022), an explicit favourable direction
(``directions.py``, parity-tested against ``report_comparison``) and the
register CODE its limit resolves from — never a number.
"""

from __future__ import annotations

from app.domain.authority.registry import MetricAuthority
from app.domain.bi.authority import (
    AUTHORIZATION_MODULE,
    ENTITLEMENT_SLUG,
    TEXT_VALUED_METRIC_IDS,
    designation_of,
    engine_authorities,
    engine_module_for,
)
from app.domain.bi.catalogue.directions import engine_direction
from app.domain.bi.catalogue.members import (
    DPD_COMPLETENESS,
    ColumnRef,
    EngineRule,
    MeasureDef,
    Tier,
    ValueType,
)

ENGINE_TABLE = "bi_fact_engine_metric"
TIERS: tuple[Tier, ...] = ("official", "live")

#: Production labels per registry metric id. Jurisdiction-neutral by rule: the
#: reporting unit is the bank's own, so no label names a currency, and no label
#: names a regulator.
ENGINE_LABELS: dict[str, str] = {
    # capital
    "car_pct": "Capital adequacy ratio (CAR)",
    "tier1_ratio_pct": "Tier 1 ratio",
    "cet1_ratio_pct": "CET1 ratio",
    "leverage_ratio_pct": "Leverage ratio",
    "total_capital_ghs": "Total regulatory capital",
    "tier1_capital": "Tier 1 capital",
    "tier2_capital": "Tier 2 capital",
    "cet1_capital": "CET1 capital",
    "total_rwa_ghs": "Total risk-weighted assets",
    "credit_rwa_ghs": "Credit risk-weighted assets",
    "market_rwa_ghs": "Market risk-weighted assets",
    "operational_rwa_ghs": "Operational risk-weighted assets",
    "ecl_total_ghs": "Total expected credit loss",
    "ecl_general_ghs": "General expected credit loss",
    "ecl_specific_ghs": "Specific expected credit loss",
    "net_own_funds_ghs": "Net own funds",
    # liquidity
    "lcr_pct": "Liquidity coverage ratio (LCR)",
    "nsfr_pct": "Net stable funding ratio (NSFR)",
    "hqla_total_ghs": "Total high-quality liquid assets",
    "net_outflows_30d_ghs": "Net 30-day outflows",
    "asf_total_ghs": "Available stable funding",
    "rsf_total_ghs": "Required stable funding",
    "fx_funding_gap_ghs": "Foreign-currency funding gap",
    "stressed_fx_funding_gap_ghs": "Stressed foreign-currency funding gap",
    # interest-rate risk in the banking book
    "worst_eve_change_pct_tier1": "Worst change in economic value of equity / Tier 1",
    "eve_base_ghs": "Economic value of equity (base)",
    "nii_base_ghs": "Net interest income (base)",
    "ear_up_200_ghs": "Earnings at risk, +200 bp",
    "ear_down_200_ghs": "Earnings at risk, −200 bp",
    "ear_up_450_ghs": "Earnings at risk, +450 bp",
    "ear_down_450_ghs": "Earnings at risk, −450 bp",
    "asset_duration": "Asset duration",
    "liability_duration": "Liability duration",
    "duration_gap": "Duration gap",
    "cumulative_12m_gap_ghs": "Cumulative 12-month repricing gap",
    # foreign exchange
    "nop_ghs": "Net open position",
    "nop_pct_tier1": "Net open position / Tier 1",
    "single_ccy_max_pct": "Largest single-currency open position / Tier 1",
    "var_99_1d_ghs": "Value at risk (99%, one day)",
    "stressed_var_ghs": "Stressed value at risk",
    # funds transfer pricing
    "portfolio_nim_pct": "Portfolio net interest margin",
    "weighted_asset_yield_pct": "Weighted asset yield",
    "weighted_funding_credit_pct": "Weighted funding credit",
    "nmd_core_pct": "Non-maturity deposit core share",
    "total_branch_contribution_ghs": "Total branch contribution",
    # forecasting
    "avg_roe_pct": "Average return on equity (projection)",
    "year5_car_pct": "Year-5 capital adequacy ratio (projection)",
    "year5_lcr_pct": "Year-5 liquidity coverage ratio (projection)",
    "year5_nsfr_pct": "Year-5 net stable funding ratio (projection)",
    "cumulative_net_income": "Cumulative net income (projection)",
    "min_car_pct": "Lowest capital adequacy ratio on the path (projection)",
    "min_lcr_pct": "Lowest liquidity coverage ratio on the path (projection)",
    "min_nsfr_pct": "Lowest net stable funding ratio on the path (projection)",
    # credit
    "gross_loans_ghs": "Gross loan book",
    "npl_exposure_ghs": "Non-performing exposure",
    "npl_ratio": "NPL ratio (fraction)",
    "npl_ratio_pct": "NPL ratio",
    "par_30_pct": "Portfolio at risk, 30 days",
    "par_60_pct": "Portfolio at risk, 60 days",
    "par_90_pct": "Portfolio at risk, 90 days",
    "provision_coverage_pct": "Provision coverage",
    "provision_held_ghs": "Provisions held",
    "total_provision_required_ghs": "Provisions required",
    "unclassified_exposure_ghs": "Unclassified exposure",
    # implied rating
    "pit_pd_lower_pct": "Point-in-time PD, lower band",
    "pit_pd_point_pct": "Point-in-time PD, point estimate",
    "pit_pd_upper_pct": "Point-in-time PD, upper band",
    "pit_pd_central_pct": "Point-in-time PD, central tendency",
    "pit_systematic_factor": "Point-in-time systematic factor",
    "ttc_pd_lower_pct": "Through-the-cycle PD, lower band",
    "ttc_pd_point_pct": "Through-the-cycle PD, point estimate",
    "ttc_pd_upper_pct": "Through-the-cycle PD, upper band",
    "ttc_pd_central_pct": "Through-the-cycle PD, central tendency",
    "ddep_post_stress_capital_ratio_pct": "Post-stress capital ratio (sovereign debt exchange)",
}

TIER_LABELS: dict[Tier, str] = {"official": "Official", "live": "Live"}

#: Register / parameter CODES an engine measure's limit resolves from (recon
#: §B8). Codes only — the number is governed in the control plane or the
#: tenant's board register and is resolved at read time for the tenant's class.
ENGINE_THRESHOLDS: dict[str, str] = {
    "car_pct": "car_min",
    "cet1_ratio_pct": "cet1_min",
    "tier1_ratio_pct": "tier1_min",
    "leverage_ratio_pct": "leverage_min",
    "lcr_pct": "lcr_min",
    "nsfr_pct": "nsfr_min",
    "npl_ratio_pct": "npl_limit_pct",
    "worst_eve_change_pct_tier1": "eve_tier1_limit_pct",
    "ear_up_200_ghs": "irr_nii_limit_pct",
    "ear_down_200_ghs": "irr_nii_limit_pct",
    "ear_up_450_ghs": "irr_nii_limit_pct",
    "ear_down_450_ghs": "irr_nii_limit_pct",
    "nop_pct_tier1": "fx_nop_aggregate_limit_pct",
    "single_ccy_max_pct": "fx_nop_single_limit_pct",
}

#: What the number IS, for every metric id whose own unit suffix does not say.
#: There is no catch-all: ``engine_value_type`` REFUSES an id it cannot place,
#: because the guess it used to make — "anything without a unit suffix is a
#: ratio" — is how ``nop_pct_tier1`` and ``worst_eve_change_pct_tier1`` came to
#: be typed as bare ratios. Both are ``x / tier1 × 100`` in their own engines
#: (``app/domain/fx/engine.py``, ``app/domain/irr/engine.py``), so they are
#: percentages that were rendered unscaled, reading ``0.12`` where the bank filed
#: ``12.00 %``. A new registry metric now has to say what its figure is.
_VALUE_TYPE_OVERRIDES: dict[str, ValueType] = {
    # Percentages that carry ``_pct`` as an INFIX, not as the id's unit suffix.
    "nop_pct_tier1": "pct",
    "worst_eve_change_pct_tier1": "pct",
    # A proportion of one: the same figure as the ``_pct`` twin beside it,
    # divided by a hundred.
    "npl_ratio": "fraction",
    # Durations, in years. A duration gap is a difference of two of them and is
    # still years — it is not a ratio of anything, and it may be negative.
    "asset_duration": "duration_years",
    "liability_duration": "duration_years",
    "duration_gap": "duration_years",
    # The standardised systematic factor Z of the point-in-time conditioning: a
    # signed number on its own scale, never a percentage of anything.
    "pit_systematic_factor": "index",
    # Amounts whose id names no unit.
    "cet1_capital": "amount",
    "tier1_capital": "amount",
    "tier2_capital": "amount",
    "cumulative_net_income": "amount",
}

#: Engine measures may be sliced by time and by the copy's own state flags only.
ENGINE_DIMENSIONS: tuple[str, ...] = (
    "time.date",
    "time.calendar_month",
    "time.calendar_quarter",
    "time.calendar_year",
    "time.fiscal_year",
    "time.fiscal_quarter",
    "engine.status",
    "engine.pipeline_state",
    "engine.reconciliation_blocked",
)

#: R1 reconciles the portfolio's NPL figures to these engine figures.
_R1_METRICS: frozenset[str] = frozenset({"npl_ratio_pct", "npl_exposure_ghs", "gross_loans_ghs"})

#: Engine metrics whose VALUE depends on the bank having supplied days-past-due
#: (D-046). ``regulatory_credit._portfolio_at_risk`` divides raw DPD exposures, so
#: a book that states no arrears data yields a genuine engine ``0`` — which BI
#: copies verbatim, as it must, and then badges. R10 is what keeps that badge
#: honest; the value is never touched. The engine's own presentation of the same
#: gap is the credit owner's item (H-014).
_DPD_DEPENDENT_METRICS: frozenset[str] = frozenset({"par_30_pct", "par_60_pct", "par_90_pct"})


class UndeclaredValueType(ValueError):
    """A registry metric whose figure the catalogue cannot name (see the table)."""


def engine_value_type(metric_id: str) -> ValueType:
    """What the metric's figure IS: declared, or read from the id's unit suffix.

    The two suffixes are the platform's own load-bearing wire-key conventions and
    are trusted. Anything else must be declared in ``_VALUE_TYPE_OVERRIDES``: a
    figure nobody has said the unit of cannot be rendered, exported or compared
    against a target without guessing, and the guess is silent.
    """
    override = _VALUE_TYPE_OVERRIDES.get(metric_id)
    if override is not None:
        return override
    if metric_id.endswith("_pct"):
        return "pct"
    if metric_id.endswith("_ghs"):
        return "amount"
    raise UndeclaredValueType(
        f"{metric_id} does not say what its figure is. Add it to _VALUE_TYPE_OVERRIDES in "
        "app/domain/bi/catalogue/engine.py, naming whether it is an amount, a percentage "
        "already multiplied by a hundred, a fraction of one, an index on its own scale, or "
        "a duration in years."
    )


def engine_measure_id(metric_id: str, regime: str, tier: Tier) -> str:
    return f"engine.{metric_id}.{regime}.{tier}"


def _reconciliation_checks(metric_id: str, tier: Tier) -> tuple[str, ...]:
    checks: list[str] = []
    if metric_id in _R1_METRICS:
        checks.append("R1")
    if metric_id in _DPD_DEPENDENT_METRICS:
        checks.append(DPD_COMPLETENESS)
    if tier == "live":
        # Freshness (mart as-of vs live as-of) and the balance identity the
        # live plane stamps as ``reconciliation_blocked`` apply to live copies.
        checks.extend(("R8", "R9"))
    return tuple(checks)


def engine_measure(entry: MetricAuthority, tier: Tier) -> MeasureDef:
    """The ``certified_engine`` measure for one registry authority on one tier."""
    module = engine_module_for(entry)
    if module is None:  # pragma: no cover - engine_authorities() filters these
        raise ValueError(f"{entry.key} is not tier-produced")
    metric_id = entry.metric_id
    regime = entry.regime.value
    label = ENGINE_LABELS[metric_id]
    return MeasureDef(
        id=engine_measure_id(metric_id, regime, tier),
        module=AUTHORIZATION_MODULE[module],
        sensitivity="aggregated",
        label=f"{label} · {TIER_LABELS[tier]}",
        source=ColumnRef(ENGINE_TABLE, "value"),
        description=(
            f"Copied from the {TIER_LABELS[tier].lower()} tier of the {module} module "
            f"under the {regime} regime, methodology {entry.methodology_id}."
        ),
        measure_kind="certified_engine",
        aggregation="last_value",
        time_behaviour="stock",
        allowed_dimensions=ENGINE_DIMENSIONS,
        grain="institution",
        entitlement=ENTITLEMENT_SLUG[module],
        favourable_direction=engine_direction(metric_id),
        thresholds_source=ENGINE_THRESHOLDS.get(metric_id),
        reconciliation_checks=_reconciliation_checks(metric_id, tier),
        engine_rule=EngineRule(metric_id=metric_id, module=module, tier=tier),
        fx_rule=None,
        advisory_designation=designation_of(entry),
        value_type=engine_value_type(metric_id),
    )


def engine_measures() -> tuple[MeasureDef, ...]:
    """Every engine measure: primary tier-produced authorities × both tiers."""
    return tuple(
        engine_measure(entry, tier)
        for entry in engine_authorities()
        if entry.metric_id not in TEXT_VALUED_METRIC_IDS
        for tier in TIERS
    )
