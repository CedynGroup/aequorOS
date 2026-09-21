"""Interim IRRBB add-on: the worst economic-value LOSS (D-013).

This is explicitly an interim method, and it says so in its own status
(``interim_non_sf``) rather than in a footnote someone can drop. The
standardised framework — repricing buckets, behavioural options, per-currency
aggregation — is P5's; until it lands, a bank still has to put a number on
interest-rate risk in the banking book, and this is the defensible one:

* **losses only.** ``delta_eve`` arrives in the engine's sign convention
  (``eve_shocked − eve_base``, so a negative number is a loss). A scenario that
  IMPROVES economic value is not a capital need, and the legacy outlier test's
  ``abs()`` — which counts a gain as a breach — is the defect D-013 records
  rather than copies.
* **the regulator's shock set, from the console.** Which scenarios count, and
  which of them are mandatory, is a governed parameter. A run missing a
  mandatory shock is ``incomplete``: the figure is understated and must not
  read as complete.
* **the outlier statement is a statement, not a charge.** The measure against
  Tier 1 is reported in ``detail``; it never becomes an add-on by itself.

The legacy engine runs one curve and one ladder, so the per-currency sum over
adverse currencies reduces to a single term. That is recorded in ``detail``
rather than assumed away.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.icaap.pillar2.types import (
    MethodResult,
    MethodStatus,
    MissingParameter,
    ParameterUse,
    detail_of,
    text,
)
from app.domain.icaap.units import HUNDRED, ZERO, Basis, amount, ratio

PARAM_SCENARIOS = "icaap_irrbb_interim_scenarios"
PARAM_OUTLIER_THRESHOLD = "irrbb_outlier_threshold_pct_tier1"

METHOD = "irrbb_interim_delta_eve"
SCENARIO_SET_NAME = "Supervisory rate shocks (interim, legacy engine)"


@dataclass(frozen=True)
class ScenarioDelta:
    """One shock's economic-value change, in the engine's sign convention."""

    code: str
    delta_eve: Decimal


def interim_delta_eve(  # noqa: PLR0913 - the shock set, the run and the outlier inputs
    *,
    deltas: Sequence[ScenarioDelta],
    scenario_codes: Sequence[str] | None,
    required_codes: Sequence[str] | None,
    tier1: Decimal | None,
    outlier_threshold_pct_tier1: Decimal | None,
    overlay_stressed_loss: Decimal | None = None,
    irr_run_id: str | None = None,
) -> MethodResult:
    """The worst adverse ΔEVE across the governed shock set."""
    if scenario_codes is None or required_codes is None:
        raise MissingParameter(PARAM_SCENARIOS)
    if outlier_threshold_pct_tier1 is None:
        raise MissingParameter(PARAM_OUTLIER_THRESHOLD)

    available = {delta.code: delta.delta_eve for delta in deltas}
    reasons: list[str] = []
    losses: dict[str, Decimal] = {}
    for code in scenario_codes:
        if code not in available:
            continue
        losses[code] = max(ZERO, -available[code])

    missing_required = [code for code in required_codes if code not in available]
    for code in missing_required:
        reasons.append(f"required_scenario_missing:{code}")

    detail: dict[str, str | None] = {
        "currency_aggregation": "single_curve_engine_one_term",
        "irr_run_id": irr_run_id,
        "tier1": text(tier1),
    }
    for code in scenario_codes:
        detail[f"delta_eve:{code}"] = text(available.get(code))
        detail[f"loss:{code}"] = text(losses.get(code))

    scenario_definition = {
        "name": SCENARIO_SET_NAME,
        "scenarios": list(scenario_codes),
        "required": list(required_codes),
        "source": "governed_parameter",
        "irr_run_id": irr_run_id,
    }
    uses = (
        ParameterUse(PARAM_SCENARIOS, "shock set"),
        ParameterUse(PARAM_OUTLIER_THRESHOLD, "outlier statement"),
    )

    if not losses:
        return MethodResult(
            method=METHOD,
            method_version="v1",
            status=MethodStatus.NOT_COMPUTABLE,
            baseline_derivation="not_applicable",
            stressed_derivation="not_applicable",
            scenario_definition=scenario_definition,
            detail=detail_of(detail),
            reasons=(*reasons, "no_governed_scenario_available"),
            parameters_used=uses,
        )

    worst_code = max(losses, key=lambda code: (losses[code], code))
    worst_loss = losses[worst_code]
    baseline = amount(worst_loss)
    detail["worst_scenario"] = worst_code
    detail["worst_loss"] = text(worst_loss)

    measure: Decimal | None = None
    if tier1 is not None and tier1 > ZERO:
        measure = ratio(baseline * HUNDRED / tier1)
    detail["irrbb_outlier_measure_pct"] = text(measure)
    detail["irrbb_outlier_threshold_pct_tier1"] = text(outlier_threshold_pct_tier1)
    detail["outlier"] = (
        None if measure is None else str(measure > outlier_threshold_pct_tier1).lower()
    )

    if overlay_stressed_loss is None:
        stressed = baseline
        stressed_derivation = "same_as_baseline"
    else:
        stressed = amount(max(baseline, overlay_stressed_loss))
        stressed_derivation = "max_of_baseline_and_scenario"
    detail["overlay_stressed_loss"] = text(overlay_stressed_loss)

    status = MethodStatus.INCOMPLETE if missing_required else MethodStatus.INTERIM_NON_SF
    return MethodResult(
        method=METHOD,
        method_version="v1",
        status=status,
        basis=Basis.ABSOLUTE,
        basis_value=baseline,
        baseline_amount=baseline,
        stressed_amount=stressed,
        baseline_derivation="method",
        stressed_derivation=stressed_derivation,
        scenario_definition=scenario_definition,
        detail=detail_of(detail),
        reasons=tuple(reasons),
        parameters_used=uses,
    )


__all__ = [
    "METHOD",
    "PARAM_OUTLIER_THRESHOLD",
    "PARAM_SCENARIOS",
    "SCENARIO_SET_NAME",
    "ScenarioDelta",
    "interim_delta_eve",
]
