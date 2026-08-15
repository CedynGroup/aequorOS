"""Governance report comparison: what moved between two immutable runs, and whether it is good.

A ``RegulatoryRun`` is the immutable, versioned unit of a filing — one per
``(bank, reporting period, module, scenario)`` attempt, carrying a stable-keyed
``metrics`` map of the return's headline figures. Comparing two runs is therefore
a diff over those keyed metrics, which is why the comparable **line unit is the
run metric**, not a rendered package cell: the metric key (``car_pct``,
``lcr_pct``, ``total_rwa_ghs``…) is stable across versions AND across reporting
periods, and it is exactly what the favorable-direction registry judges. Package
snapshot cells are keyed on per-return regulator row codes that neither carry a
favorable direction nor align across return forms; the cell-level version diff is
already served by ``regulatory_reporting.snapshot_diff``.

Two modes, both over the same module:

* ``version`` — the same reporting period, two run versions (original filing vs a
  resubmission/restatement).
* ``period`` — two reporting periods, the latest succeeded run of each.

For each comparable line the engine computes the absolute delta, the percentage
delta (in percentage points, matching ``snapshot_diff``; ``None`` when the base
is zero), the direction, and the **favorability** — the substantive judgment
layer. Favorability combines the observed direction with a governed
favorable-direction registry (``FAVORABLE_DIRECTION`` below): a metric where more
is stronger (CAR, LCR, CET1) going up is *favorable*; a risk or cost metric (NPL,
RWA, VaR, cost-to-income) going up is *adverse*; a raw balance or signed exposure
has no defined favorable direction and is always *neutral*. Unknown keys default
to neutral, so the registry never guesses.

Pure-ish: reads runs/periods from the DB, computes in memory, writes nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryRun
from app.schemas.report_comparison import (
    ComparisonGroupRead,
    ComparisonLineRead,
    ComparisonSideRead,
    LineDirection,
    LineFavorability,
    LineUnit,
    ReportComparisonRead,
    ReportComparisonRequest,
)

# ---------------------------------------------------------------------------
# Favorable-direction registry — the substantive judgment layer.
#
# Keyed on the run metric key. A key's membership answers one question: when this
# figure INCREASES, is the bank stronger or weaker?
#
#   higher_better  → up is favorable, down is adverse  (buffers, coverage, returns)
#   lower_better   → up is adverse,   down is favorable (risk, losses, cost)
#   neutral        → no defined favorable direction     (raw balances, signed gaps)
#
# Everything not listed defaults to neutral (see ``favorable_direction``). Only
# unambiguous outcome metrics are classified; raw balances, denominators and
# signed exposure/gap figures are deliberately left neutral because their
# "better" direction depends on sign and context a line diff cannot see.
# ---------------------------------------------------------------------------

FavorableDirection = Literal["higher_better", "lower_better", "neutral"]

#: Metrics where a higher value is the stronger position.
HIGHER_BETTER: frozenset[str] = frozenset(
    {
        # Capital adequacy & resources — bigger buffer over the minimum is safer.
        "car_pct",
        "tier1_ratio_pct",
        "cet1_ratio_pct",
        "tier1_ratio",
        "cet1_ratio",
        # Basel leverage ratio = Tier 1 / total exposure: a capital-strength floor,
        # so HIGHER is better (distinct from balance-sheet gearing, which is not).
        "leverage_ratio_pct",
        "leverage_ratio",
        "total_capital_ghs",
        "year5_car_pct",
        "baseline_worst_cet1_pct",
        "worst_cet1_at_breach_pct",
        "worst_cet1_at_k_max_pct",
        # Liquidity coverage & stable funding.
        "lcr_pct",
        "nsfr_pct",
        "baseline_lcr_pct",
        "lcr_at_breach_pct",
        "lcr_at_k_max_pct",
        "hqla_total_ghs",
        "asf_total_ghs",
        "provision_coverage_pct",
        "npl_coverage_pct",
        # Profitability, margin & contribution.
        "avg_roe_pct",
        "roe_pct",
        "portfolio_nim_pct",
        "ftp_adjusted_nim_pct",
        "net_margin_pct",
        "weighted_asset_yield_pct",
        "total_contribution_ghs",
        "net_contribution_ghs",
        "contribution_ghs",
        "total_branch_contribution_ghs",
        # Interest-rate risk: more economic value / base earnings is stronger.
        "eve_ghs",
        "eve_base_ghs",
        "nii_base_ghs",
    }
)

#: Metrics where a higher value is the weaker/riskier position.
LOWER_BETTER: frozenset[str] = frozenset(
    {
        # Risk-weighted assets — more RWA consumes more capital.
        "total_rwa_ghs",
        "credit_rwa_ghs",
        "market_rwa_ghs",
        "operational_rwa_ghs",
        "quarterly_rwa_growth_pct",
        # Asset quality — non-performing / past due.
        "npl_ratio_pct",
        "npl_pct",
        "gross_npl_ratio_pct",
        "past_due_pct",
        "par30_pct",
        "par90_pct",
        # Expected credit loss — larger allowances mean worse asset quality.
        "ecl_total_ghs",
        "ecl_general_ghs",
        "ecl_specific_ghs",
        "ecl_stage1_ghs",
        "ecl_stage2_ghs",
        "ecl_stage3_ghs",
        # Interest-rate sensitivity — more rate risk in the banking book.
        "delta_eve_pct_tier1",
        "delta_eve_ghs",
        "worst_eve_change_pct_tier1",
        "worst_eve_change_ghs",
        "ear_up_200_ghs",
        "ear_up_450_ghs",
        "ear_down_200_ghs",
        "ear_down_450_ghs",
        # FX open position & value at risk — larger exposure is riskier.
        "nop_pct_tier1",
        "single_ccy_max_pct",
        "abs_pct_tier1",
        "nop_ghs",
        "var_99_1d_ghs",
        "stressed_var_ghs",
        "standalone_var_ghs",
        "standalone_var_total_ghs",
        # Cost & margin pressure.
        "cost_to_income_pct",
        "operating_cost_pct",
        "products_below_min_margin",
    }
)

# Documented family fallbacks, applied only when the exact key is unlisted. These
# cover unambiguous families so future metric variants inherit the right judgment
# instead of silently defaulting to neutral. Kept intentionally small.
_LOWER_BETTER_SUFFIXES: tuple[str, ...] = ("_rwa_ghs",)
_LOWER_BETTER_PREFIXES: tuple[str, ...] = ("ecl_", "ear_")


def favorable_direction(key: str) -> FavorableDirection:
    """Which way is 'better' for this metric key? Defaults to neutral."""
    if key in HIGHER_BETTER:
        return "higher_better"
    if key in LOWER_BETTER:
        return "lower_better"
    if key.endswith(_LOWER_BETTER_SUFFIXES):
        return "lower_better"
    if key.startswith(_LOWER_BETTER_PREFIXES) and key.endswith("_ghs"):
        return "lower_better"
    if "_var_ghs" in key or key.startswith("var_"):
        return "lower_better"
    return "neutral"


# ---------------------------------------------------------------------------
# Unit inference — tells the UI how to format a line and whether its delta is money.
# ---------------------------------------------------------------------------

_CCY_SUFFIXES: tuple[str, ...] = ("_ghs", "_amount", "_amt")
_COUNT_KEYS: frozenset[str] = frozenset({"products_below_min_margin", "feasible_count"})


def classify_unit(key: str) -> LineUnit:
    """Map a metric key to a formatting unit (``ccy`` | ``pct`` | ``ratio`` | ``count``)."""
    if key in _COUNT_KEYS or key.endswith("_count"):
        return "count"
    if key.endswith("_pct") or key.endswith("_bps"):
        return "pct"
    if key.endswith(_CCY_SUFFIXES):
        return "ccy"
    if key.endswith("_ratio"):
        return "ratio"
    if key.endswith("_years") or key.endswith("_days"):
        return "count"
    return "ratio"


# ---------------------------------------------------------------------------
# Line labels & section grouping.
# ---------------------------------------------------------------------------

_LABELS: dict[str, str] = {
    "car_pct": "Capital adequacy ratio (CAR)",
    "tier1_ratio_pct": "Tier 1 ratio",
    "cet1_ratio_pct": "CET1 ratio",
    "leverage_ratio_pct": "Leverage ratio",
    "total_capital_ghs": "Total regulatory capital",
    "total_rwa_ghs": "Total risk-weighted assets",
    "credit_rwa_ghs": "Credit RWA",
    "market_rwa_ghs": "Market RWA",
    "operational_rwa_ghs": "Operational RWA",
    "lcr_pct": "Liquidity coverage ratio (LCR)",
    "nsfr_pct": "Net stable funding ratio (NSFR)",
    "hqla_total_ghs": "Total HQLA",
    "net_outflows_30d_ghs": "Net 30-day outflows",
    "delta_eve_pct_tier1": "ΔEVE / Tier 1",
    "worst_eve_change_pct_tier1": "Worst ΔEVE / Tier 1",
    "nop_pct_tier1": "Net open position / Tier 1",
    "portfolio_nim_pct": "Portfolio NIM",
    "avg_roe_pct": "Average ROE",
    "ecl_total_ghs": "Total expected credit loss",
    "npl_ratio_pct": "NPL ratio",
}

# Finer sections within a module; unmapped keys fall back to the module section.
_SECTION_BY_KEY: dict[str, str] = {
    # capital
    "car_pct": "Capital adequacy ratios",
    "tier1_ratio_pct": "Capital adequacy ratios",
    "cet1_ratio_pct": "Capital adequacy ratios",
    "leverage_ratio_pct": "Capital adequacy ratios",
    "tier1_ratio": "Capital adequacy ratios",
    "cet1_ratio": "Capital adequacy ratios",
    "leverage_ratio": "Capital adequacy ratios",
    "year5_car_pct": "Capital adequacy ratios",
    "baseline_worst_cet1_pct": "Capital stress path",
    "worst_cet1_at_breach_pct": "Capital stress path",
    "worst_cet1_at_k_max_pct": "Capital stress path",
    "quarterly_rwa_growth_pct": "Risk-weighted assets",
    "total_rwa_ghs": "Risk-weighted assets",
    "credit_rwa_ghs": "Risk-weighted assets",
    "market_rwa_ghs": "Risk-weighted assets",
    "operational_rwa_ghs": "Risk-weighted assets",
    "total_capital_ghs": "Capital resources & provisions",
    "tier1_ghs": "Capital resources & provisions",
    "ecl_total_ghs": "Capital resources & provisions",
    "ecl_general_ghs": "Capital resources & provisions",
    "ecl_specific_ghs": "Capital resources & provisions",
    "ecl_stage1_ghs": "Capital resources & provisions",
    "ecl_stage2_ghs": "Capital resources & provisions",
    "ecl_stage3_ghs": "Capital resources & provisions",
    "npl_ratio_pct": "Asset quality",
    # liquidity
    "lcr_pct": "Liquidity ratios",
    "nsfr_pct": "Liquidity ratios",
    "baseline_lcr_pct": "Liquidity ratios",
    "lcr_at_breach_pct": "Liquidity ratios",
    "lcr_at_k_max_pct": "Liquidity ratios",
    "hqla_total_ghs": "Liquidity resources",
    "asf_total_ghs": "Liquidity resources",
    "rsf_total_ghs": "Liquidity resources",
    "net_outflows_30d_ghs": "Liquidity resources",
    "fx_funding_gap_ghs": "FX funding",
    "stressed_fx_funding_gap_ghs": "FX funding",
    "fx_share_of_liabilities_pct": "FX funding",
    # irr
    "eve_ghs": "Economic value & earnings",
    "eve_base_ghs": "Economic value & earnings",
    "delta_eve_ghs": "Economic value & earnings",
    "delta_eve_pct_tier1": "Economic value & earnings",
    "worst_eve_change_ghs": "Economic value & earnings",
    "worst_eve_change_pct_tier1": "Economic value & earnings",
    "nii_base_ghs": "Economic value & earnings",
    "ear_up_200_ghs": "Earnings at risk",
    "ear_up_450_ghs": "Earnings at risk",
    "ear_down_200_ghs": "Earnings at risk",
    "ear_down_450_ghs": "Earnings at risk",
    "gap_ghs": "Repricing gap",
    "cumulative_gap_ghs": "Repricing gap",
    "cumulative_12m_gap_ghs": "Repricing gap",
    "rsa_ghs": "Repricing gap",
    "rsl_ghs": "Repricing gap",
    "pv_assets_ghs": "Present value",
    "pv_liabilities_ghs": "Present value",
    # fx
    "nop_pct_tier1": "Net open position",
    "single_ccy_max_pct": "Net open position",
    "abs_pct_tier1": "Net open position",
    "nop_ghs": "Net open position",
    "net_ghs": "Net open position",
    "sum_long_ghs": "Net open position",
    "sum_short_ghs": "Net open position",
    "var_99_1d_ghs": "Value at risk",
    "stressed_var_ghs": "Value at risk",
    "standalone_var_ghs": "Value at risk",
    "standalone_var_total_ghs": "Value at risk",
    "diversification_benefit_ghs": "Value at risk",
    "hedge_aggregate_mtm_ghs": "Hedging",
    "hedge_effective_count": "Hedging",
    "hedge_ineffective_count": "Hedging",
    "hedge_total_count": "Hedging",
    # ftp
    "portfolio_nim_pct": "Margins & contribution",
    "ftp_adjusted_nim_pct": "Margins & contribution",
    "net_margin_pct": "Margins & contribution",
    "weighted_asset_yield_pct": "Margins & contribution",
    "total_contribution_ghs": "Margins & contribution",
    "net_contribution_ghs": "Margins & contribution",
    "contribution_ghs": "Margins & contribution",
    "total_branch_contribution_ghs": "Margins & contribution",
    "products_below_min_margin": "Margins & contribution",
    "nmd_core_pct": "Funding profile",
    "core_pct": "Funding profile",
    "volatile_pct": "Funding profile",
    "curve_shift_pct": "Funding profile",
    # forecast
    "avg_roe_pct": "Profitability",
    "roe_pct": "Profitability",
}

_MODULE_SECTION: dict[str, str] = {
    "capital": "Capital",
    "liquidity": "Liquidity",
    "irr": "Interest-rate risk",
    "fx": "FX risk",
    "ftp": "Funds transfer pricing",
    "forecast": "Forecast",
}

# Section display order; sections outside this list sort after it, alphabetically.
_SECTION_ORDER: tuple[str, ...] = (
    "Capital adequacy ratios",
    "Risk-weighted assets",
    "Capital resources & provisions",
    "Asset quality",
    "Capital stress path",
    "Liquidity ratios",
    "Liquidity resources",
    "FX funding",
    "Economic value & earnings",
    "Earnings at risk",
    "Repricing gap",
    "Present value",
    "Net open position",
    "Value at risk",
    "Hedging",
    "Margins & contribution",
    "Funding profile",
    "Profitability",
)


def _label_for(key: str) -> str:
    if key in _LABELS:
        return _LABELS[key]
    cleaned = key
    for suffix in ("_pct", "_ghs", "_bps", "_ratio", "_years", "_days", "_count"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned.replace("_", " ").strip().capitalize() or key


def _section_for(module: str, key: str) -> str:
    return _SECTION_BY_KEY.get(key) or _MODULE_SECTION.get(module, module.title())


def _section_sort_key(title: str) -> tuple[int, str]:
    try:
        return (_SECTION_ORDER.index(title), "")
    except ValueError:
        return (len(_SECTION_ORDER), title)


# ---------------------------------------------------------------------------
# Numeric flattening & line construction.
# ---------------------------------------------------------------------------

_PCT_QUANTUM = Decimal("0.01")


def _to_decimal(value: object) -> Decimal | None:
    """Parse a scalar metric into a Decimal, or ``None`` for non-numeric values.

    Run ``metrics`` mix scalar figures (decimal strings) with structural entries
    (lists like ``stress_path``, textual codes like ``worst_scenario``). Only the
    scalars are comparable lines; everything else is skipped.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except (InvalidOperation, ValueError):
            return None
    return None


def _numeric_metrics(metrics: dict[str, object]) -> dict[str, Decimal]:
    parsed: dict[str, Decimal] = {}
    for key, value in metrics.items():
        decimal_value = _to_decimal(value)
        if decimal_value is not None:
            parsed[key] = decimal_value
    return parsed


def _favorability(key: str, direction: LineDirection) -> LineFavorability:
    if direction == "flat":
        return "neutral"
    disposition = favorable_direction(key)
    if disposition == "neutral":
        return "neutral"
    good_when_up = disposition == "higher_better"
    line_went_up = direction == "up"
    return "favorable" if good_when_up == line_went_up else "adverse"


def _build_line(key: str, left: Decimal | None, right: Decimal | None) -> ComparisonLineRead:
    unit = classify_unit(key)
    left_value = str(left) if left is not None else None
    right_value = str(right) if right is not None else None

    if left is None or right is None:
        # A key present on only one side: no baseline to judge against. Flag as
        # ``new`` when it is the comparison side that carries the value.
        return ComparisonLineRead(
            key=key,
            label=_label_for(key),
            unit=unit,
            left_value=left_value,
            right_value=right_value,
            delta_ccy=None,
            delta_pct=None,
            direction="flat",
            favorability="neutral",
            new=left is None,
        )

    delta = right - left
    if delta > 0:
        direction: LineDirection = "up"
    elif delta < 0:
        direction = "down"
    else:
        direction = "flat"

    if left == 0:
        delta_pct: str | None = None
        is_new = True
    else:
        try:
            delta_pct = str((delta / left * 100).quantize(_PCT_QUANTUM))
        except (DivisionByZero, InvalidOperation):
            delta_pct = None
        is_new = False

    return ComparisonLineRead(
        key=key,
        label=_label_for(key),
        unit=unit,
        left_value=left_value,
        right_value=right_value,
        delta_ccy=str(delta),
        delta_pct=delta_pct,
        direction=direction,
        favorability=_favorability(key, direction),
        new=is_new,
    )


def _diff_runs(
    module: str, left_run: RegulatoryRun, right_run: RegulatoryRun
) -> tuple[list[ComparisonGroupRead], dict[str, int]]:
    left_metrics = _numeric_metrics(left_run.metrics)
    right_metrics = _numeric_metrics(right_run.metrics)
    keys = sorted(set(left_metrics) | set(right_metrics))

    grouped: dict[str, list[ComparisonLineRead]] = defaultdict(list)
    counts = {"favorable": 0, "adverse": 0, "neutral": 0}
    for key in keys:
        line = _build_line(key, left_metrics.get(key), right_metrics.get(key))
        grouped[_section_for(module, key)].append(line)
        counts[line.favorability] += 1

    groups = [
        ComparisonGroupRead(title=title, lines=grouped[title])
        for title in sorted(grouped, key=_section_sort_key)
    ]
    return groups, counts


# ---------------------------------------------------------------------------
# Resolution of the two sides from the DB.
# ---------------------------------------------------------------------------


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_run_or_404(db: Session, ctx: TenantContext, bank: Bank, run_id: UUID) -> RegulatoryRun:
    run = db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == run_id,
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.status == "succeeded",
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comparable run not found."
        )
    return run


def _get_period_or_404(
    db: Session, ctx: TenantContext, bank: Bank, period_id: UUID
) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.id == period_id,
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    return period


def _succeeded_runs(  # noqa: PLR0913 - one read carries its full scoping
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period_id: UUID,
    module: str,
    scenario_code: str,
) -> list[RegulatoryRun]:
    """Succeeded runs for one period/module/scenario, oldest first (v1 … vN)."""
    return list(
        db.scalars(
            select(RegulatoryRun)
            .where(
                RegulatoryRun.organization_id == ctx.organization_id,
                RegulatoryRun.bank_id == bank.id,
                RegulatoryRun.reporting_period_id == period_id,
                RegulatoryRun.module == module,
                RegulatoryRun.scenario_code == scenario_code,
                RegulatoryRun.status == "succeeded",
            )
            .order_by(RegulatoryRun.created_at.asc(), RegulatoryRun.id.asc())
        )
    )


def _version_ordinal(runs: Iterable[RegulatoryRun], run_id: UUID) -> int:
    for index, run in enumerate(runs, start=1):
        if run.id == run_id:
            return index
    return 1


def _side(run: RegulatoryRun, period: BankReportingPeriod, version: int) -> ComparisonSideRead:
    return ComparisonSideRead(
        run_id=run.id,
        version=version,
        label=f"{period.label} · v{version}",
        period_label=period.label,
        reporting_date=period.period_end,
        reporting_period_id=period.id,
        scenario_code=run.scenario_code,
        engine_version=run.engine_version,
    )


def _resolve_version_mode(
    db: Session, ctx: TenantContext, bank: Bank, req: ReportComparisonRequest
) -> tuple[ComparisonSideRead, ComparisonSideRead, RegulatoryRun, RegulatoryRun]:
    left_run = _get_run_or_404(db, ctx, bank, req.left)
    right_run = _get_run_or_404(db, ctx, bank, req.right)
    if left_run.module != right_run.module or left_run.module != req.module:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "not_comparable",
                "message": "Runs belong to different return families and cannot be compared.",
            },
        )
    if left_run.reporting_period_id != right_run.reporting_period_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "not_comparable",
                "message": "Version comparison requires both runs in the same reporting period.",
            },
        )
    period = _get_period_or_404(db, ctx, bank, left_run.reporting_period_id)
    left_chain = _succeeded_runs(db, ctx, bank, period.id, left_run.module, left_run.scenario_code)
    right_chain = (
        left_chain
        if right_run.scenario_code == left_run.scenario_code
        else _succeeded_runs(db, ctx, bank, period.id, right_run.module, right_run.scenario_code)
    )
    left_side = _side(left_run, period, _version_ordinal(left_chain, left_run.id))
    right_side = _side(right_run, period, _version_ordinal(right_chain, right_run.id))
    return left_side, right_side, left_run, right_run


def _resolve_period_mode(
    db: Session, ctx: TenantContext, bank: Bank, req: ReportComparisonRequest
) -> tuple[ComparisonSideRead, ComparisonSideRead, RegulatoryRun, RegulatoryRun]:
    left_period = _get_period_or_404(db, ctx, bank, req.left)
    right_period = _get_period_or_404(db, ctx, bank, req.right)
    left_chain = _succeeded_runs(db, ctx, bank, left_period.id, req.module, req.scenario_code)
    right_chain = _succeeded_runs(db, ctx, bank, right_period.id, req.module, req.scenario_code)
    if not left_chain or not right_chain:
        missing = left_period.label if not left_chain else right_period.label
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No succeeded {req.module}/{req.scenario_code} run for reporting period "
                f"{missing}."
            ),
        )
    left_run = left_chain[-1]
    right_run = right_chain[-1]
    left_side = _side(left_run, left_period, len(left_chain))
    right_side = _side(right_run, right_period, len(right_chain))
    return left_side, right_side, left_run, right_run


def build_comparison(
    db: Session, ctx: TenantContext, bank_id: str, req: ReportComparisonRequest
) -> ReportComparisonRead:
    """Resolve both sides per mode, then diff their metrics into favorability-scored lines."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    if req.mode == "version":
        left_side, right_side, left_run, right_run = _resolve_version_mode(db, ctx, bank, req)
    else:
        left_side, right_side, left_run, right_run = _resolve_period_mode(db, ctx, bank, req)

    groups, counts = _diff_runs(req.module, left_run, right_run)
    return ReportComparisonRead(
        mode=req.mode,
        module=req.module,
        left=left_side,
        right=right_side,
        groups=groups,
        favorable_count=counts["favorable"],
        adverse_count=counts["adverse"],
        neutral_count=counts["neutral"],
    )


__all__ = [
    "HIGHER_BETTER",
    "LOWER_BETTER",
    "build_comparison",
    "classify_unit",
    "favorable_direction",
]
