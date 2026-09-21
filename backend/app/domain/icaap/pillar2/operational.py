"""Operational risk add-on: the worst severe-but-plausible loss, net of Pillar 1.

Pillar 1 already holds capital for operational risk (the basic-indicator charge
on gross income). A Pillar 2 add-on is only the part a severe scenario would
cost BEYOND that, so the charge is ``max(0, worst scenario loss − Pillar 1
operational capital)`` and a bank whose Pillar 1 charge already covers its worst
scenario adds nothing. That netting is what the legacy path omitted (audit M7).

Scenarios come from two places and both are declared:

* **governed** — severities as a percentage of annual gross income, one per
  scenario key, held in the console. The scenario SET (cloud outage, cyber,
  payments failure, telecom, key-staff loss, flood or epidemic, civil strife)
  is the Stress guideline's; the severities are platform calibrations, seeded
  REPRESENTATIVE and labelled as such wherever they are printed.
* **bank-defined** — an amount the bank evidenced itself, which wins if it is
  worse. A bank-defined scenario with no written definition is ``incomplete``:
  an unexplained number is not a scenario.

The absolute severities in the legacy stress module are deliberately NOT used
(audit J11): they were reporting-currency amounts, so they meant different
things to a small bank and a large one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
from app.domain.icaap.units import HUNDRED, ZERO, Basis, amount, pillar1_capital

PARAM_SEVERITIES = "op_p2_scenario_severity_pct_gross_income"
PARAM_CAR_MIN = "car_min"
METHOD = "operational_scenario_net_p1"

SOURCE_GOVERNED = "governed_severity_pct_gross_income"
SOURCE_BANK = "bank_defined_amount"


@dataclass(frozen=True)
class OperationalScenario:
    """A scenario the bank defined itself, with its own evidenced loss."""

    key: str
    definition: str | None = None
    loss_amount: Decimal | None = None
    evidence_attachment_id: str | None = None


@dataclass(frozen=True)
class ScenarioLoss:
    key: str
    source: str
    loss: Decimal
    definition: str | None


def operational_scenario_net_p1(  # noqa: PLR0913, PLR0912 - governed and bank inputs, each checked
    *,
    gross_income: Decimal | None,
    severities_pct_gross_income: Mapping[str, Decimal] | None,
    operational_rwa: Decimal | None,
    car_min_pct: Decimal | None,
    bank_scenarios: Sequence[OperationalScenario] = (),
    stressed_operational_rwa: Decimal | None = None,
    stressed_car_min_pct: Decimal | None = None,
) -> MethodResult:
    """The worst scenario loss above the Pillar 1 operational charge."""
    if severities_pct_gross_income is None:
        raise MissingParameter(PARAM_SEVERITIES)
    if car_min_pct is None:
        raise MissingParameter(PARAM_CAR_MIN)

    uses = (
        ParameterUse(PARAM_SEVERITIES, "scenario severities"),
        ParameterUse(PARAM_CAR_MIN, "pillar 1 operational charge"),
    )
    reasons: list[str] = []
    losses: list[ScenarioLoss] = []

    if gross_income is None:
        if severities_pct_gross_income:
            reasons.append("gross_income_unavailable")
    else:
        for key, severity in sorted(severities_pct_gross_income.items()):
            losses.append(
                ScenarioLoss(
                    key=key,
                    source=SOURCE_GOVERNED,
                    loss=amount(gross_income * severity / HUNDRED),
                    definition=None,
                )
            )

    for scenario in bank_scenarios:
        if scenario.loss_amount is None:
            reasons.append(f"scenario_loss_missing:{scenario.key}")
            continue
        if not (scenario.definition or "").strip():
            reasons.append(f"scenario_definition_missing:{scenario.key}")
        losses.append(
            ScenarioLoss(
                key=scenario.key,
                source=SOURCE_BANK,
                loss=amount(scenario.loss_amount),
                definition=scenario.definition,
            )
        )

    detail: dict[str, str | None] = {
        "gross_income": text(gross_income),
        "operational_rwa": text(operational_rwa),
    }
    for entry in losses:
        detail[f"loss:{entry.key}"] = text(entry.loss)
        detail[f"source:{entry.key}"] = entry.source

    if not losses:
        return MethodResult(
            method=METHOD,
            method_version="v1",
            status=MethodStatus.NOT_COMPUTABLE,
            baseline_derivation="not_applicable",
            stressed_derivation="not_applicable",
            detail=detail_of(detail),
            reasons=(*reasons, "no_scenario_available"),
            parameters_used=uses,
        )

    worst = max(losses, key=lambda entry: (entry.loss, entry.key))
    rwa = operational_rwa if operational_rwa is not None else ZERO
    pillar1 = pillar1_capital(rwa, car_min_pct)
    baseline = amount(max(ZERO, worst.loss - pillar1))
    detail["worst_scenario"] = worst.key
    detail["worst_loss"] = text(worst.loss)
    detail["pillar1_operational_capital"] = text(pillar1)

    if stressed_operational_rwa is None:
        stressed = baseline
        stressed_derivation = "same_as_baseline"
    else:
        stressed_pillar1 = pillar1_capital(
            stressed_operational_rwa,
            stressed_car_min_pct if stressed_car_min_pct is not None else car_min_pct,
        )
        stressed = amount(max(ZERO, worst.loss - stressed_pillar1))
        stressed_derivation = "method"
        detail["stressed_pillar1_operational_capital"] = text(stressed_pillar1)

    status = MethodStatus.INCOMPLETE if reasons else MethodStatus.COMPUTED
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
        scenario_definition={
            "worst_scenario": worst.key,
            "definition": worst.definition,
            "severity_basis": worst.source,
            "gross_income": text(gross_income),
            "all_losses": {entry.key: str(entry.loss) for entry in losses},
        },
        detail=detail_of(detail),
        reasons=tuple(reasons),
        parameters_used=uses,
    )


__all__ = [
    "METHOD",
    "PARAM_CAR_MIN",
    "PARAM_SEVERITIES",
    "SOURCE_BANK",
    "SOURCE_GOVERNED",
    "OperationalScenario",
    "ScenarioLoss",
    "operational_scenario_net_p1",
]
