"""Bottom-up credit & counterparty stress (docs/stress.md §1.7, §3.6, Phase 4 item 1).

Appendix I's credit-and-counterparty method done bottom-up: exposure-level PD,
LGD and EAD, a downgrade / rating-migration step under the macro scenario, and
FX-loan revaluation. This closes the two Phase-2 limitations the projection
documented:

1. **RWA rating-migration + FX-loan revaluation are now modelled.** Phase-2
   stress capital-*ratio* erosion in the 3-year projection was P&L/loss-driven
   only — a mild scenario could look resilient purely by deleveraging. Here a
   documented fraction of each performing exposure **downgrades** (attracts a
   higher risk weight) as the macro PD multiplier rises, and foreign-currency
   exposures are **revalued** by the cedi-depreciation path. Both raise credit
   RWA independently of balance-sheet growth. The aggregate uplift factor feeds
   ``projection.py`` so the stress leg's RWA rises from migration + FX, not only
   from deleveraging.
2. **Real exposure-class loss decomposition.** Phase-2 Table 1 allocated the
   adverse impairment across the CRD exposure classes by credit-RWA share — a
   documented proxy. Here the incremental expected loss is computed per exposure
   (``EAD × PD × LGD`` stressed minus baseline) and aggregated by the exposure's
   own CRD class, so the decomposition is genuinely bottom-up and sums to the
   total incremental loss by construction.

Design (pure, deterministic, Decimal-only — no DB, no FastAPI):
- **Migration.** A share ``migration_fraction = min(sensitivity × (pd_mult − 1),
  cap)`` of every exposure downgrades one band; the downgraded slice's risk
  weight is ``min(rw + step, rw_cap)`` (a one-band downgrade, never below the
  original weight). The un-migrated slice keeps its weight. This is a
  transparent, hand-computable stand-in for a full rating-transition matrix
  (¶45 "justify all overlays … with challenge/validation").
- **FX revaluation.** A foreign-currency exposure's EAD is scaled by
  ``1 + max(fx_frac, 0)`` — cedi depreciation inflates the GHS value of the
  exposure; an appreciation never *reduces* the stressed EAD (floored at the
  base). Only the credit book is revalued here; the market/NOP revaluation is
  the FX engine's job, so there is no double count.
- **IFRS-9 ECL stays Perfect-Foresight / Single-Scenario** (¶48–49): the PD/LGD
  multipliers come from the single stress scenario at 100% weight (via the
  capital translation), and each projected year can be evaluated off its own
  macro (``result_for_year``) so every year knows its macro from day one.

Every channel is neutral under a base scenario (``pd_mult == 1``, ``fx_frac ==
0``): ``migration_fraction`` is 0, the stressed EAD equals the base EAD, the
uplift factor is 1.0 and the incremental loss is 0 — the zero-delta invariant
the whole stress framework relies on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.domain.stress.translation import (
    MacroPathPoint,
    ShockMapping,
    translate,
)

MONEY = Decimal("0.0001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


# --- CRD exposure classes (canonical home; Appendix II Table 1) --------------
# The BoG CRD 2018 exposure classes the Table 1 "Impact of Adverse" line splits
# losses across. Defined here (the bottom-up decomposition's natural home) and
# re-exported by ``appendix_ii`` so both read one list.
CRD_EXPOSURE_CLASSES: tuple[str, ...] = (
    "gog",
    "bog",
    "other_sovereigns_central_banks",
    "public_sector_entities",
    "multilateral_development_banks",
    "banks",
    "other_financial_institutions",
    "corporates",
    "retail_sme",
    "past_due",
    "high_risk",
    "other",
)
_CRD_CLASS_SET = frozenset(CRD_EXPOSURE_CLASSES)
_OTHER_CLASS = "other"


@dataclass(frozen=True)
class MigrationParams:
    """Documented rating-migration + FX-revaluation coefficients (¶45).

    ``migration_sensitivity`` is the share of an exposure that downgrades per
    unit of PD-multiplier uplift above 1.0; ``max_migration_fraction`` caps it.
    ``downgrade_rw_step_pct`` is the additive risk-weight step (pp) the migrated
    slice attracts, capped at ``rw_cap_pct``.
    """

    migration_sensitivity: Decimal = Decimal("0.5")
    max_migration_fraction: Decimal = Decimal("0.5")
    downgrade_rw_step_pct: Decimal = Decimal("50")
    rw_cap_pct: Decimal = Decimal("150")


DEFAULT_MIGRATION_PARAMS = MigrationParams()


@dataclass(frozen=True)
class CreditExposure:
    """One exposure-level credit position for the bottom-up stress.

    ``crd_class`` is the CRD exposure class (validated to the registry).
    ``is_foreign_currency`` marks a non-base-currency exposure the FX path
    revalues. PD/LGD/risk-weight are through-the-cycle percentages.
    """

    exposure_id: str
    crd_class: str
    ead: Decimal
    pd_pct: Decimal
    lgd_pct: Decimal
    risk_weight_pct: Decimal
    is_foreign_currency: bool = False

    def normalized_class(self) -> str:
        return self.crd_class if self.crd_class in _CRD_CLASS_SET else _OTHER_CLASS


@dataclass(frozen=True)
class ExposureClassImpact:
    """One CRD class's aggregate base/stressed RWA and expected loss."""

    exposure_class: str
    base_rwa: Decimal
    stressed_rwa: Decimal
    base_expected_loss: Decimal
    stressed_expected_loss: Decimal
    incremental_expected_loss: Decimal


@dataclass(frozen=True)
class BottomUpCreditResult:
    """The exposure-level credit-stress outcome (Perfect-Foresight, one scenario)."""

    pd_multiplier: Decimal
    lgd_multiplier: Decimal
    fx_fraction: Decimal
    base_credit_rwa: Decimal
    stressed_credit_rwa: Decimal
    fx_revaluation_rwa: Decimal
    migration_rwa: Decimal
    credit_rwa_uplift_factor: Decimal
    base_expected_loss: Decimal
    stressed_expected_loss: Decimal
    incremental_expected_loss: Decimal
    exposure_count: int
    by_class: tuple[ExposureClassImpact, ...]

    def incremental_loss_by_class(self) -> dict[str, Decimal]:
        """``{crd_class: incremental_expected_loss}`` for every class (0 if none)."""
        losses = {name: _ZERO for name in CRD_EXPOSURE_CLASSES}
        for impact in self.by_class:
            losses[impact.exposure_class] = impact.incremental_expected_loss
        return losses

    def serialize(self) -> dict[str, object]:
        return {
            "pd_multiplier": str(self.pd_multiplier),
            "lgd_multiplier": str(self.lgd_multiplier),
            "fx_fraction": str(self.fx_fraction),
            "base_credit_rwa": str(self.base_credit_rwa),
            "stressed_credit_rwa": str(self.stressed_credit_rwa),
            "fx_revaluation_rwa": str(self.fx_revaluation_rwa),
            "migration_rwa": str(self.migration_rwa),
            "credit_rwa_uplift_factor": str(self.credit_rwa_uplift_factor),
            "base_expected_loss": str(self.base_expected_loss),
            "stressed_expected_loss": str(self.stressed_expected_loss),
            "incremental_expected_loss": str(self.incremental_expected_loss),
            "exposure_count": self.exposure_count,
            "by_class": [
                {
                    "exposure_class": impact.exposure_class,
                    "base_rwa": str(impact.base_rwa),
                    "stressed_rwa": str(impact.stressed_rwa),
                    "base_expected_loss": str(impact.base_expected_loss),
                    "stressed_expected_loss": str(impact.stressed_expected_loss),
                    "incremental_expected_loss": str(impact.incremental_expected_loss),
                }
                for impact in self.by_class
            ],
        }


@dataclass
class _ClassAccumulator:
    base_rwa: Decimal = _ZERO
    stressed_rwa: Decimal = _ZERO
    base_el: Decimal = _ZERO
    stressed_el: Decimal = _ZERO


def _capped_pct(value: Decimal) -> Decimal:
    return min(max(value, _ZERO), _HUNDRED)


def compute_bottom_up_credit(
    exposures: Sequence[CreditExposure],
    *,
    pd_multiplier: Decimal,
    lgd_multiplier: Decimal,
    fx_fraction: Decimal,
    params: MigrationParams = DEFAULT_MIGRATION_PARAMS,
) -> BottomUpCreditResult:
    """Stress every exposure and aggregate by CRD class.

    ``pd_multiplier`` / ``lgd_multiplier`` are the scenario's macro conditioning
    (from the capital translation); ``fx_fraction`` is the fractional cedi
    depreciation the FX path implies. All three are 1.0 / 1.0 / 0.0 under a base
    scenario, collapsing the stress onto the base.
    """
    fx_uplift = max(fx_fraction, _ZERO)
    migration_fraction = min(
        params.migration_sensitivity * max(pd_multiplier - _ONE, _ZERO),
        params.max_migration_fraction,
    )

    accumulators: dict[str, _ClassAccumulator] = {}
    fx_reval_rwa = _ZERO
    migration_rwa = _ZERO
    for exposure in exposures:
        crd_class = exposure.normalized_class()
        acc = accumulators.setdefault(crd_class, _ClassAccumulator())
        base_ead = exposure.ead
        stressed_ead = (
            money(base_ead * (_ONE + fx_uplift)) if exposure.is_foreign_currency else base_ead
        )
        rw = exposure.risk_weight_pct
        downgraded_rw = min(max(rw + params.downgrade_rw_step_pct, rw), params.rw_cap_pct)
        effective_rw = (_ONE - migration_fraction) * rw + migration_fraction * downgraded_rw

        base_rwa = money(base_ead * rw / _HUNDRED)
        stressed_rwa = money(stressed_ead * effective_rw / _HUNDRED)
        # Additive attribution: base + fx-revaluation + migration == stressed.
        fx_reval_rwa += money((stressed_ead - base_ead) * rw / _HUNDRED)
        migration_rwa += money(stressed_ead * (effective_rw - rw) / _HUNDRED)

        base_el = money(base_ead * exposure.pd_pct / _HUNDRED * exposure.lgd_pct / _HUNDRED)
        stressed_pd = _capped_pct(exposure.pd_pct * pd_multiplier)
        stressed_lgd = _capped_pct(exposure.lgd_pct * lgd_multiplier)
        stressed_el = money(stressed_ead * stressed_pd / _HUNDRED * stressed_lgd / _HUNDRED)

        acc.base_rwa += base_rwa
        acc.stressed_rwa += stressed_rwa
        acc.base_el += base_el
        acc.stressed_el += stressed_el

    by_class = tuple(
        ExposureClassImpact(
            exposure_class=name,
            base_rwa=accumulators[name].base_rwa,
            stressed_rwa=accumulators[name].stressed_rwa,
            base_expected_loss=accumulators[name].base_el,
            stressed_expected_loss=accumulators[name].stressed_el,
            incremental_expected_loss=max(
                accumulators[name].stressed_el - accumulators[name].base_el, _ZERO
            ),
        )
        for name in CRD_EXPOSURE_CLASSES
        if name in accumulators
    )
    base_credit_rwa = money(sum((impact.base_rwa for impact in by_class), _ZERO))
    stressed_credit_rwa = money(sum((impact.stressed_rwa for impact in by_class), _ZERO))
    base_el = money(sum((impact.base_expected_loss for impact in by_class), _ZERO))
    stressed_el = money(sum((impact.stressed_expected_loss for impact in by_class), _ZERO))
    uplift = (
        (stressed_credit_rwa / base_credit_rwa).quantize(Decimal("0.000001"))
        if base_credit_rwa > _ZERO
        else _ONE
    )
    return BottomUpCreditResult(
        pd_multiplier=pd_multiplier,
        lgd_multiplier=lgd_multiplier,
        fx_fraction=fx_fraction,
        base_credit_rwa=base_credit_rwa,
        stressed_credit_rwa=stressed_credit_rwa,
        fx_revaluation_rwa=money(fx_reval_rwa),
        migration_rwa=money(migration_rwa),
        credit_rwa_uplift_factor=uplift,
        base_expected_loss=base_el,
        stressed_expected_loss=stressed_el,
        incremental_expected_loss=max(money(stressed_el - base_el), _ZERO),
        exposure_count=len(exposures),
        by_class=by_class,
    )


def _year_points(paths: Sequence[MacroPathPoint], year_index: int) -> list[MacroPathPoint]:
    return [point for point in paths if point.year_index == year_index]


def _macro_conditioning(
    year_points: Sequence[MacroPathPoint],
    overrides: Mapping[str, Sequence[ShockMapping]] | None,
) -> tuple[Decimal, Decimal, Decimal]:
    """(pd_mult, lgd_mult, fx_frac) implied by one set of macro points."""
    shocks = translate(year_points, "capital", overrides) if year_points else {}
    pd_mult = shocks.get("ecl_pd_multiplier", _ONE)
    lgd_mult = shocks.get("ecl_lgd_multiplier", _ONE)
    fx_frac = _ZERO
    for point in year_points:
        if point.variable == "fx_usd_ghs" and point.base_value != _ZERO:
            fx_frac = (point.stress_value - point.base_value) / point.base_value
            break
    return pd_mult, lgd_mult, fx_frac


def result_for_year(
    exposures: Sequence[CreditExposure],
    scenario_paths: Sequence[MacroPathPoint],
    year_index: int,
    *,
    overrides: Mapping[str, Sequence[ShockMapping]] | None = None,
    params: MigrationParams = DEFAULT_MIGRATION_PARAMS,
) -> BottomUpCreditResult:
    """Perfect-foresight bottom-up credit stress for one projected year.

    Each year is conditioned by its OWN macro (¶48): the PD/LGD multipliers and
    the FX fraction are computed from that year's authored macro points, so a
    scenario whose severity varies over the horizon produces a different uplift
    per year.
    """
    pd_mult, lgd_mult, fx_frac = _macro_conditioning(
        _year_points(scenario_paths, year_index), overrides
    )
    return compute_bottom_up_credit(
        exposures,
        pd_multiplier=pd_mult,
        lgd_multiplier=lgd_mult,
        fx_fraction=fx_frac,
        params=params,
    )


def result_at_peak(
    exposures: Sequence[CreditExposure],
    scenario_paths: Sequence[MacroPathPoint],
    *,
    overrides: Mapping[str, Sequence[ShockMapping]] | None = None,
    params: MigrationParams = DEFAULT_MIGRATION_PARAMS,
) -> BottomUpCreditResult:
    """Bottom-up credit stress at the scenario's peak conditioning (enterprise snapshot).

    Uses ``translate`` over the FULL path (peak-year deltas, matching the
    orchestrator's other engines) rather than a single year — the single
    enterprise-snapshot view the orchestrator reports alongside the projection.
    """
    shocks = translate(scenario_paths, "capital", overrides)
    pd_mult = shocks.get("ecl_pd_multiplier", _ONE)
    lgd_mult = shocks.get("ecl_lgd_multiplier", _ONE)
    fx_frac = _peak_fx_fraction(scenario_paths)
    return compute_bottom_up_credit(
        exposures,
        pd_multiplier=pd_mult,
        lgd_multiplier=lgd_mult,
        fx_fraction=fx_frac,
        params=params,
    )


def _peak_fx_fraction(paths: Sequence[MacroPathPoint]) -> Decimal:
    peak = _ZERO
    for point in paths:
        if point.variable != "fx_usd_ghs" or point.base_value == _ZERO:
            continue
        frac = (point.stress_value - point.base_value) / point.base_value
        if abs(frac) > abs(peak):
            peak = frac
    return peak


@dataclass(frozen=True)
class BottomUpCreditInputs:
    """The orchestrator's bottom-up credit inputs (exposures + params)."""

    exposures: Sequence[CreditExposure] = field(default_factory=tuple)
    params: MigrationParams = DEFAULT_MIGRATION_PARAMS
