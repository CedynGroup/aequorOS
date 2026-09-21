"""Judgemental items: a number the Board owns, with what it rests on.

Strategic, reputational, legal, model, climate and cyber risk have no
defensible formula on this data. That is not a reason to leave them out — the
guideline's own Table 5 has rows for them — so the platform takes a Board
judgement and demands what makes a judgement reviewable rather than arbitrary:

* a written **rationale**;
* at least one live **evidence** attachment;
* a **stressed** figure, or an explicit declaration that it is the same as the
  baseline — never a silent blank in the stress column;
* for a MATERIAL risk carried at zero, a written justification of the zero.
  "Material, and no capital" is a defensible position; it is not a default.

``diversification_benefit`` is the one item that may be NEGATIVE, and only when
the governed switch allows it (M19). Diversification across risk types is an
assumption that fails exactly when it is needed, so the platform's aggregation
is a simple sum unless a supervisor has agreed otherwise — and that agreement
is a console row, not a checkbox in the workspace.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.icaap.pillar2.types import (
    MethodResult,
    MethodStatus,
    MissingParameter,
    ParameterUse,
    detail_of,
    text,
)
from app.domain.icaap.units import ZERO, Basis, amount

PARAM_DIVERSIFICATION_ALLOWED = "icaap_diversification_benefit_allowed"
METHOD = "judgemental"
COMPONENT_DIVERSIFICATION = "diversification_benefit"


def validate_judgemental(  # noqa: PLR0913, PLR0912 - one parameter and one check per requirement
    *,
    component: str,
    baseline: Decimal | None,
    stressed: Decimal | None = None,
    stressed_same_as_baseline: bool = False,
    rationale: str | None = None,
    evidence_count: int = 0,
    zero_amount_justification: str | None = None,
    risk_is_material: bool = False,
    diversification_allowed: bool | None = None,
) -> MethodResult:
    """Check an entered judgement and return it in the canonical shape."""
    reasons: list[str] = []
    uses: tuple[ParameterUse, ...] = ()
    is_diversification = component == COMPONENT_DIVERSIFICATION

    if is_diversification:
        if diversification_allowed is None:
            raise MissingParameter(PARAM_DIVERSIFICATION_ALLOWED)
        uses = (ParameterUse(PARAM_DIVERSIFICATION_ALLOWED, "aggregation policy"),)
        if not diversification_allowed:
            reasons.append("diversification_not_allowed")

    if baseline is None:
        reasons.append("baseline_required")
    elif is_diversification:
        if baseline > ZERO:
            reasons.append("diversification_must_not_increase_capital")
    elif baseline < ZERO:
        reasons.append("negative_amount_not_allowed")
    elif baseline == ZERO and risk_is_material and not (zero_amount_justification or "").strip():
        reasons.append("zero_amount_justification_required")

    if not (rationale or "").strip():
        reasons.append("rationale_required")
    if evidence_count < 1:
        reasons.append("evidence_required")

    if stressed is None and not stressed_same_as_baseline:
        reasons.append("stressed_required")

    resolved_baseline = None if baseline is None else amount(baseline)
    if stressed is not None:
        resolved_stressed = amount(stressed)
        derivation = "method"
    elif stressed_same_as_baseline:
        resolved_stressed = resolved_baseline
        derivation = "same_as_baseline"
    else:
        resolved_stressed = None
        derivation = "not_assessed"

    return MethodResult(
        method=METHOD,
        method_version="v1",
        status=MethodStatus.INCOMPLETE if reasons else MethodStatus.COMPUTED,
        basis=Basis.ABSOLUTE,
        basis_value=resolved_baseline,
        baseline_amount=resolved_baseline,
        stressed_amount=resolved_stressed,
        baseline_derivation="method",
        stressed_derivation=derivation,
        detail=detail_of(
            {
                "component": component,
                "baseline": text(resolved_baseline),
                "stressed": text(resolved_stressed),
                "rationale_present": str(bool((rationale or "").strip())).lower(),
                "evidence_count": str(evidence_count),
                "zero_amount_justification_present": str(
                    bool((zero_amount_justification or "").strip())
                ).lower(),
            }
        ),
        reasons=tuple(reasons),
        parameters_used=uses,
    )


__all__ = [
    "COMPONENT_DIVERSIFICATION",
    "METHOD",
    "PARAM_DIVERSIFICATION_ALLOWED",
    "validate_judgemental",
]
