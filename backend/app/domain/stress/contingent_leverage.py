"""Contingent-leverage stress (docs/stress.md §1.7 AppI ¶30-32, Phase 4 item 4).

Appendix I ¶30-32: stress the sources of CONTINGENT leverage — derivatives and
SFTs (securities-financing transactions), collateral swaps, and netting — that
inflate the Basel leverage-ratio EXPOSURE measure under stress even when the
on-balance-sheet position is unchanged, and report the leverage-ratio impact.
Pure, deterministic, Decimal-only.

Four channels lift the stressed leverage exposure (¶45 documented + overridable):
- **derivatives** — the potential-future-exposure (PFE) add-ons rise with a
  volatility shock (``pfe_stress_multiplier``);
- **SFTs** — collateral haircuts widen, grossing up the SFT exposure measure
  (``sft_haircut_uplift_pct`` × gross SFT value);
- **netting** — netting benefits erode as correlations break down, so a fraction
  of the recognised netting offset is added back (``netting_breakdown_pct``);
- **collateral swaps** — upgrade/downgrade swaps add exposure under stress
  (``collateral_swap_uplift_pct`` × notional).

``stressed_leverage_exposure = base + Σ uplifts``; the leverage ratio is
``tier1 / exposure``. **Graceful zero:** a bank with no derivatives, SFTs,
collateral swaps or netting has zero uplift, so the stressed exposure equals the
base and the ratio is unchanged — correct, not an error.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ContingentLeverageParams:
    """Documented contingent-leverage stress coefficients (¶45 overrides seam)."""

    #: Uplift on derivative PFE add-ons under a volatility shock (fraction).
    pfe_stress_multiplier: Decimal = Decimal("0.5")
    #: Additional collateral haircut (pp) grossing up the SFT exposure.
    sft_haircut_uplift_pct: Decimal = Decimal("10")
    #: Fraction of the recognised netting benefit added back on correlation breakdown.
    netting_breakdown_pct: Decimal = Decimal("20")
    #: Uplift on collateral-swap notional under stress (fraction, %).
    collateral_swap_uplift_pct: Decimal = Decimal("15")


DEFAULT_CONTINGENT_LEVERAGE_PARAMS = ContingentLeverageParams()


@dataclass(frozen=True)
class DerivativePosition:
    """One derivative's leverage-exposure inputs (SA-CCR-style RC + PFE add-on)."""

    position_id: str
    notional: Decimal
    pfe_addon: Decimal
    replacement_cost: Decimal = _ZERO


@dataclass(frozen=True)
class SftPosition:
    """One securities-financing transaction's gross exposure value."""

    position_id: str
    gross_value: Decimal


@dataclass(frozen=True)
class ContingentLeverageInputs:
    base_leverage_exposure: Decimal
    tier1: Decimal
    derivatives: Sequence[DerivativePosition] = field(default_factory=tuple)
    sfts: Sequence[SftPosition] = field(default_factory=tuple)
    netting_benefit: Decimal = _ZERO
    collateral_swaps_notional: Decimal = _ZERO
    params: ContingentLeverageParams = DEFAULT_CONTINGENT_LEVERAGE_PARAMS


@dataclass(frozen=True)
class ContingentLeverageResult:
    base_leverage_exposure: Decimal
    stressed_leverage_exposure: Decimal
    derivative_addon_uplift: Decimal
    sft_addon_uplift: Decimal
    netting_offset_reduction: Decimal
    collateral_swap_uplift: Decimal
    total_uplift: Decimal
    base_leverage_ratio_pct: Decimal
    stressed_leverage_ratio_pct: Decimal
    leverage_ratio_impact_pp: Decimal
    has_contingent_positions: bool

    def serialize(self) -> dict[str, object]:
        return {
            "base_leverage_exposure": str(self.base_leverage_exposure),
            "stressed_leverage_exposure": str(self.stressed_leverage_exposure),
            "derivative_addon_uplift": str(self.derivative_addon_uplift),
            "sft_addon_uplift": str(self.sft_addon_uplift),
            "netting_offset_reduction": str(self.netting_offset_reduction),
            "collateral_swap_uplift": str(self.collateral_swap_uplift),
            "total_uplift": str(self.total_uplift),
            "base_leverage_ratio_pct": str(self.base_leverage_ratio_pct),
            "stressed_leverage_ratio_pct": str(self.stressed_leverage_ratio_pct),
            "leverage_ratio_impact_pp": str(self.leverage_ratio_impact_pp),
            "has_contingent_positions": self.has_contingent_positions,
        }


def compute_contingent_leverage(
    inputs: ContingentLeverageInputs,
) -> ContingentLeverageResult:
    """Inflate the leverage exposure through the four contingent channels."""
    params = inputs.params
    derivative_uplift = money(
        sum((position.pfe_addon for position in inputs.derivatives), _ZERO)
        * params.pfe_stress_multiplier
    )
    sft_uplift = money(
        sum((position.gross_value for position in inputs.sfts), _ZERO)
        * params.sft_haircut_uplift_pct
        / _HUNDRED
    )
    netting_reduction = money(inputs.netting_benefit * params.netting_breakdown_pct / _HUNDRED)
    collateral_swap_uplift = money(
        inputs.collateral_swaps_notional * params.collateral_swap_uplift_pct / _HUNDRED
    )
    total_uplift = money(
        derivative_uplift + sft_uplift + netting_reduction + collateral_swap_uplift
    )
    stressed_exposure = money(inputs.base_leverage_exposure + total_uplift)

    base_ratio = (
        ratio_pct(inputs.tier1 / inputs.base_leverage_exposure * _HUNDRED)
        if inputs.base_leverage_exposure > _ZERO
        else _ZERO
    )
    stressed_ratio = (
        ratio_pct(inputs.tier1 / stressed_exposure * _HUNDRED)
        if stressed_exposure > _ZERO
        else _ZERO
    )
    has_positions = bool(
        inputs.derivatives
        or inputs.sfts
        or inputs.netting_benefit != _ZERO
        or inputs.collateral_swaps_notional != _ZERO
    )
    return ContingentLeverageResult(
        base_leverage_exposure=money(inputs.base_leverage_exposure),
        stressed_leverage_exposure=stressed_exposure,
        derivative_addon_uplift=derivative_uplift,
        sft_addon_uplift=sft_uplift,
        netting_offset_reduction=netting_reduction,
        collateral_swap_uplift=collateral_swap_uplift,
        total_uplift=total_uplift,
        base_leverage_ratio_pct=base_ratio,
        stressed_leverage_ratio_pct=stressed_ratio,
        leverage_ratio_impact_pp=ratio_pct(stressed_ratio - base_ratio),
        has_contingent_positions=has_positions,
    )
