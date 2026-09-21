"""IRRBB Pillar 2 capital from the standardised framework (D-009, P5-DESIGN §1.6).

The interim method in ``irrbb.py`` reads the legacy engine's per-scenario
deltas and takes the worst adverse one, labelling itself ``interim_non_sf`` so
nobody mistakes it for the framework (D-013). This method is the framework: it
reads the ECONOMIC VALUE RISK MEASURE a sealed ``irr_sf`` run already computed
— the largest loss across the supervisory outlier scenario set, in the
reporting currency — and carries it into the register as the Pillar 2 amount.

It re-derives nothing. The buckets, the six shock shapes, the behavioural
options and the per-currency aggregation are the run's arithmetic, sealed and
reproducible from its own ``input_hash``; recomputing any of it here would give
the report two answers that could disagree.

**A refusal stays a refusal.** A run that refused — a book holding interest-rate
options is refused under the single name ``irrbb_sf_options_unsupported``
(D-061, DV-010) — produces ``not_computable``, never a zero and never a
partial figure. An understated capital number that looks complete is worse than
an honest gap: the gap gets fixed, the wrong number gets filed.

**Assumptions stay counted.** The run tallies every APPLICATION of a modelling
default, and the tallies arrive here per marker and leave per marker. A default
applied to forty positions is a different exposure from one applied to a single
position, and summing them into a flag would throw that away.

No regulatory number appears here. The outlier threshold is a governed code and
its absence is a typed :class:`MissingParameter` (D-024).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

from app.domain.icaap.pillar2.types import (
    MethodResult,
    MethodStatus,
    MissingParameter,
    ParameterUse,
    detail_of,
    text,
)
from app.domain.icaap.units import HUNDRED, ZERO, Basis, amount, ratio

METHOD = "irrbb_standardised_framework"
METHOD_VERSION = "v1"

PARAM_OUTLIER_THRESHOLD = "irrbb_outlier_threshold_pct_tier1"

#: Machine reasons. ``REASON_REFUSED`` carries the run's own refusal code as its
#: tail, so the ONE canonical name (D-061) travels unchanged from the engine to
#: the register; nothing maps it to a second spelling.
REASON_REFUSED = "standardised_framework_refused"
REASON_MEASURE_ABSENT = "standardised_framework_measure_absent"
REASON_SOURCE_ABSENT = "standardised_framework_run_absent"

SCENARIO_SET_NAME = "Supervisory outlier scenario set (standardised framework)"


@dataclass(frozen=True)
class SfFigures:
    """What the bound ``irrbb_sf`` block reports, as the method reads it.

    Every field is what the SEALED RUN recorded. ``None`` means the run did not
    report it; it never means zero.
    """

    eve_risk_measure: Decimal | None = None
    tier1: Decimal | None = None
    pct_tier1: Decimal | None = None
    outlier: bool | None = None
    worst_scenario: str | None = None
    max_delta_nii: Decimal | None = None
    currencies_in_scope: str | None = None
    mandatory: bool | None = None
    run_id: str | None = None
    run_input_hash: str | None = None
    measure_set: str | None = None
    #: marker -> how many positions the default was applied to. Never summed.
    assumption_tallies: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    representative_parameters: tuple[str, ...] = ()
    pending_parameters: tuple[str, ...] = ()
    scenarios_in_measure: tuple[str, ...] = ()


def _base_detail(figures: SfFigures | None, refusal: str | None) -> dict[str, str | None]:
    detail: dict[str, str | None] = {
        "sf_run_id": None if figures is None else figures.run_id,
        "sf_input_hash": None if figures is None else figures.run_input_hash,
        "sf_refusal_code": refusal,
    }
    if figures is None:
        return detail
    detail["measure_set"] = figures.measure_set
    detail["worst_scenario"] = figures.worst_scenario
    detail["currencies_in_scope"] = figures.currencies_in_scope
    detail["sf_mandatory"] = None if figures.mandatory is None else str(figures.mandatory).lower()
    detail["tier1"] = text(figures.tier1)
    detail["max_delta_nii"] = text(figures.max_delta_nii)
    total = 0
    for marker, count in sorted(figures.assumption_tallies.items()):
        detail[f"assumption_default:{marker}"] = text(int(count))
        total += int(count)
    detail["assumption_defaults_applied"] = text(total)
    detail["representative_parameters"] = (
        ", ".join(sorted(figures.representative_parameters)) or None
    )
    detail["parameters_pending_confirmation"] = (
        ", ".join(sorted(figures.pending_parameters)) or None
    )
    return detail


def _uses(figures: SfFigures | None) -> tuple[ParameterUse, ...]:
    """The governed codes this figure rested on, with the role each played.

    The run's own representative and pending codes are named here as well as
    the outlier threshold, because the register's warning about a representative
    calibration is built from exactly this list — and a calibration that shaped
    the measure but never appeared on the item would be invisible to the reader
    who most needs to see it.
    """
    uses: list[ParameterUse] = [ParameterUse(PARAM_OUTLIER_THRESHOLD, "outlier statement")]
    if figures is None:
        return tuple(uses)
    for code in sorted(figures.representative_parameters):
        uses.append(ParameterUse(code, "standardised framework (representative)"))
    for code in sorted(figures.pending_parameters):
        if code not in figures.representative_parameters:
            uses.append(ParameterUse(code, "standardised framework (pending confirmation)"))
    return tuple(uses)


def _scenario_definition(figures: SfFigures | None) -> dict[str, object]:
    return {
        "name": SCENARIO_SET_NAME,
        "scenarios": list(() if figures is None else figures.scenarios_in_measure),
        "measure_set": None if figures is None else figures.measure_set,
        "source": "irr_sf_run",
        "sf_run_id": None if figures is None else figures.run_id,
    }


def standardised_framework(
    *,
    figures: SfFigures | None,
    refusal: str | None,
    refusal_reasons: Sequence[str] = (),
    outlier_threshold_pct_tier1: Decimal | None,
    overlay_stressed_loss: Decimal | None = None,
) -> MethodResult:
    """The Pillar 2 IRRBB amount from a sealed standardised framework run."""
    if outlier_threshold_pct_tier1 is None:
        raise MissingParameter(PARAM_OUTLIER_THRESHOLD)

    detail = _base_detail(figures, refusal)
    detail["irrbb_outlier_threshold_pct_tier1"] = text(outlier_threshold_pct_tier1)
    uses = _uses(figures)
    scenario_definition = _scenario_definition(figures)

    if refusal is not None:
        return MethodResult(
            method=METHOD,
            method_version=METHOD_VERSION,
            status=MethodStatus.NOT_COMPUTABLE,
            baseline_derivation="not_applicable",
            stressed_derivation="not_applicable",
            scenario_definition=scenario_definition,
            detail=detail_of(detail),
            reasons=(f"{REASON_REFUSED}:{refusal}", *refusal_reasons),
            parameters_used=uses,
        )

    if figures is None:
        return MethodResult(
            method=METHOD,
            method_version=METHOD_VERSION,
            status=MethodStatus.NOT_COMPUTABLE,
            baseline_derivation="not_applicable",
            stressed_derivation="not_applicable",
            scenario_definition=scenario_definition,
            detail=detail_of(detail),
            reasons=(REASON_SOURCE_ABSENT,),
            parameters_used=uses,
        )

    if figures.eve_risk_measure is None:
        # The run exists but reported no measure. Reporting zero here would
        # read as "this bank has no interest-rate risk in the banking book".
        return MethodResult(
            method=METHOD,
            method_version=METHOD_VERSION,
            status=MethodStatus.INCOMPLETE,
            baseline_derivation="not_applicable",
            stressed_derivation="not_applicable",
            scenario_definition=scenario_definition,
            detail=detail_of(detail),
            reasons=(REASON_MEASURE_ABSENT,),
            parameters_used=uses,
        )

    # The framework reports losses positive already; the floor is a guard
    # against an improving book becoming a negative capital requirement, not a
    # calibration.
    baseline = amount(max(ZERO, figures.eve_risk_measure))
    detail["sf_eve_risk_measure"] = text(figures.eve_risk_measure)

    measure = figures.pct_tier1
    if measure is None and figures.tier1 is not None and figures.tier1 > ZERO:
        measure = ratio(baseline * HUNDRED / figures.tier1)
    detail["irrbb_outlier_measure_pct"] = text(measure)
    detail["outlier"] = (
        None
        if figures.outlier is None and measure is None
        else str(
            figures.outlier
            if figures.outlier is not None
            else (measure is not None and measure > outlier_threshold_pct_tier1)
        ).lower()
    )

    if overlay_stressed_loss is None:
        stressed = baseline
        stressed_derivation = "same_as_baseline"
    else:
        stressed = amount(max(baseline, overlay_stressed_loss))
        stressed_derivation = "max_of_baseline_and_scenario"
    detail["overlay_stressed_loss"] = text(overlay_stressed_loss)

    return MethodResult(
        method=METHOD,
        method_version=METHOD_VERSION,
        status=MethodStatus.COMPUTED,
        basis=Basis.ABSOLUTE,
        basis_value=baseline,
        baseline_amount=baseline,
        stressed_amount=stressed,
        baseline_derivation="method",
        stressed_derivation=stressed_derivation,
        scenario_definition=scenario_definition,
        detail=detail_of(detail),
        parameters_used=uses,
    )


__all__ = [
    "METHOD",
    "METHOD_VERSION",
    "PARAM_OUTLIER_THRESHOLD",
    "REASON_MEASURE_ABSENT",
    "REASON_REFUSED",
    "REASON_SOURCE_ABSENT",
    "SCENARIO_SET_NAME",
    "SfFigures",
    "standardised_framework",
]
