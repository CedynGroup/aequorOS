"""Operational-risk scenario simulation (docs/stress.md §1.7 AppI ¶11-14, Phase 4 item 3).

Appendix I ¶12 prescribes annual operational-risk scenario simulations over a
fixed set of severe operational events. This module runs the **seven prescribed
scenarios** — cloud outage, cyber / data corruption, payments outage, telecom
failure, staff unavailability, flood / epidemic, civil strife — turns each into
an operational-loss estimate, and maps that loss onto **capital** (a hit to
retained earnings → CAR) and **liquidity** (event-driven outflows). Pure,
deterministic, Decimal-only.

Loss model (transparent and hand-computable, ¶45): each scenario has a
parameterised severity — a fraction of annual **gross income** lost during the
disruption plus a fixed direct incident cost — so the loss is
``gross_income × income_loss_pct + fixed_cost_ghs``. The liquidity impact is a
fraction of the liquidity base (HQLA / stable funding) that flows out while the
event runs. Severities are a **documented default set** with an ``overrides``
seam, exactly like the Phase-1 translation elasticities.

The headline **capital** impact is the WORST single scenario (a single severe
operational event, not all seven simultaneously — ¶12 runs them as independent
simulations); the summed "combined tail" is reported alongside as context. The
worst loss also feeds Appendix II Table 5's Pillar-2 "other" (operational) slot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")

# The seven Appendix I ¶12 operational scenarios, in the directive's order.
SCENARIO_CLOUD_OUTAGE = "cloud_outage"
SCENARIO_CYBER_DATA_CORRUPTION = "cyber_data_corruption"
SCENARIO_PAYMENTS_OUTAGE = "payments_outage"
SCENARIO_TELECOM_FAILURE = "telecom_failure"
SCENARIO_STAFF_UNAVAILABILITY = "staff_unavailability"
SCENARIO_FLOOD_EPIDEMIC = "flood_epidemic"
SCENARIO_CIVIL_STRIFE = "civil_strife"

OPERATIONAL_SCENARIOS: tuple[str, ...] = (
    SCENARIO_CLOUD_OUTAGE,
    SCENARIO_CYBER_DATA_CORRUPTION,
    SCENARIO_PAYMENTS_OUTAGE,
    SCENARIO_TELECOM_FAILURE,
    SCENARIO_STAFF_UNAVAILABILITY,
    SCENARIO_FLOOD_EPIDEMIC,
    SCENARIO_CIVIL_STRIFE,
)


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class OperationalSeverity:
    """One scenario's severity parameterisation (¶45 documented + overridable)."""

    scenario: str
    #: Fraction (%) of annual gross income lost through the disruption.
    income_loss_pct: Decimal
    #: Direct fixed incident cost (GHS): remediation, penalties, restoration.
    fixed_cost_ghs: Decimal
    #: Fraction (%) of the liquidity base that flows out while the event runs.
    liquidity_outflow_pct: Decimal
    #: Indicative disruption length (days) — BCP context, not used in the maths.
    duration_days: int


def _sev(
    scenario: str,
    income_loss_pct: str,
    fixed_cost_ghs: str,
    liquidity_outflow_pct: str,
    duration_days: int,
) -> OperationalSeverity:
    return OperationalSeverity(
        scenario=scenario,
        income_loss_pct=Decimal(income_loss_pct),
        fixed_cost_ghs=Decimal(fixed_cost_ghs),
        liquidity_outflow_pct=Decimal(liquidity_outflow_pct),
        duration_days=duration_days,
    )


# Documented default severities. Calibrated relative to one another: cyber/data
# corruption and payments outages are the most damaging (direct fraud + fine +
# customer outflow), infrastructure failures (cloud/telecom) are shorter, and
# the environmental/social scenarios blend income loss with elevated outflows.
DEFAULT_OPERATIONAL_SEVERITIES: dict[str, OperationalSeverity] = {
    SCENARIO_CLOUD_OUTAGE: _sev(SCENARIO_CLOUD_OUTAGE, "4", "2000000", "3", 3),
    SCENARIO_CYBER_DATA_CORRUPTION: _sev(
        SCENARIO_CYBER_DATA_CORRUPTION, "12", "15000000", "10", 14
    ),
    SCENARIO_PAYMENTS_OUTAGE: _sev(SCENARIO_PAYMENTS_OUTAGE, "8", "6000000", "8", 5),
    SCENARIO_TELECOM_FAILURE: _sev(SCENARIO_TELECOM_FAILURE, "3", "1500000", "2", 2),
    SCENARIO_STAFF_UNAVAILABILITY: _sev(SCENARIO_STAFF_UNAVAILABILITY, "5", "1000000", "3", 10),
    SCENARIO_FLOOD_EPIDEMIC: _sev(SCENARIO_FLOOD_EPIDEMIC, "7", "4000000", "6", 21),
    SCENARIO_CIVIL_STRIFE: _sev(SCENARIO_CIVIL_STRIFE, "9", "5000000", "12", 14),
}


@dataclass(frozen=True)
class OperationalInputs:
    """The operational-stress input set (gross income + optional coupling bases)."""

    annual_gross_income: Decimal
    liquidity_base: Decimal = _ZERO
    total_capital: Decimal | None = None
    total_rwa: Decimal | None = None
    #: Override hook — a partial map replaces only the named scenarios' severities.
    severities: Mapping[str, OperationalSeverity] | None = None


@dataclass(frozen=True)
class OperationalScenarioResult:
    scenario: str
    loss_ghs: Decimal
    liquidity_outflow_ghs: Decimal
    duration_days: int


@dataclass(frozen=True)
class OperationalResult:
    scenarios: tuple[OperationalScenarioResult, ...]
    worst_scenario: str
    worst_loss_ghs: Decimal
    aggregate_loss_ghs: Decimal
    worst_liquidity_outflow_ghs: Decimal
    pillar2_operational_charge: Decimal
    base_car_pct: Decimal | None
    stressed_car_pct: Decimal | None
    car_impact_pp: Decimal | None

    def serialize(self) -> dict[str, object]:
        return {
            "worst_scenario": self.worst_scenario,
            "worst_loss_ghs": str(self.worst_loss_ghs),
            "aggregate_loss_ghs": str(self.aggregate_loss_ghs),
            "worst_liquidity_outflow_ghs": str(self.worst_liquidity_outflow_ghs),
            "pillar2_operational_charge": str(self.pillar2_operational_charge),
            "base_car_pct": None if self.base_car_pct is None else str(self.base_car_pct),
            "stressed_car_pct": (
                None if self.stressed_car_pct is None else str(self.stressed_car_pct)
            ),
            "car_impact_pp": None if self.car_impact_pp is None else str(self.car_impact_pp),
            "scenarios": [
                {
                    "scenario": row.scenario,
                    "loss_ghs": str(row.loss_ghs),
                    "liquidity_outflow_ghs": str(row.liquidity_outflow_ghs),
                    "duration_days": row.duration_days,
                }
                for row in self.scenarios
            ],
        }


def _resolve_severities(
    overrides: Mapping[str, OperationalSeverity] | None,
) -> dict[str, OperationalSeverity]:
    resolved = dict(DEFAULT_OPERATIONAL_SEVERITIES)
    if overrides:
        resolved.update(overrides)
    return resolved


def compute_operational(inputs: OperationalInputs) -> OperationalResult:
    """Simulate the seven prescribed scenarios and map them onto capital + liquidity."""
    severities = _resolve_severities(inputs.severities)
    gross_income = max(inputs.annual_gross_income, _ZERO)
    liquidity_base = max(inputs.liquidity_base, _ZERO)

    scenarios: list[OperationalScenarioResult] = []
    for name in OPERATIONAL_SCENARIOS:
        severity = severities[name]
        loss = money(gross_income * severity.income_loss_pct / _HUNDRED + severity.fixed_cost_ghs)
        outflow = money(liquidity_base * severity.liquidity_outflow_pct / _HUNDRED)
        scenarios.append(
            OperationalScenarioResult(
                scenario=name,
                loss_ghs=loss,
                liquidity_outflow_ghs=outflow,
                duration_days=severity.duration_days,
            )
        )

    worst = max(scenarios, key=lambda row: (row.loss_ghs, row.scenario))
    aggregate_loss = money(sum((row.loss_ghs for row in scenarios), _ZERO))
    worst_outflow = max((row.liquidity_outflow_ghs for row in scenarios), default=_ZERO)

    base_car: Decimal | None = None
    stressed_car: Decimal | None = None
    car_impact: Decimal | None = None
    if (
        inputs.total_capital is not None
        and inputs.total_rwa is not None
        and inputs.total_rwa > _ZERO
    ):
        base_car = ratio_pct(inputs.total_capital / inputs.total_rwa * _HUNDRED)
        stressed_car = ratio_pct(
            (inputs.total_capital - worst.loss_ghs) / inputs.total_rwa * _HUNDRED
        )
        car_impact = ratio_pct(stressed_car - base_car)

    return OperationalResult(
        scenarios=tuple(scenarios),
        worst_scenario=worst.scenario,
        worst_loss_ghs=worst.loss_ghs,
        aggregate_loss_ghs=aggregate_loss,
        worst_liquidity_outflow_ghs=worst_outflow,
        pillar2_operational_charge=worst.loss_ghs,
        base_car_pct=base_car,
        stressed_car_pct=stressed_car,
        car_impact_pp=car_impact,
    )


@dataclass(frozen=True)
class OperationalConfig:
    """The orchestrator's operational config: gross income + optional overrides.

    The orchestrator fills ``liquidity_base`` / ``total_capital`` / ``total_rwa``
    from the computed capital + liquidity results, so only the gross-income base
    and any severity overrides are authored up front.
    """

    annual_gross_income: Decimal
    severities: Mapping[str, OperationalSeverity] | None = field(default=None)
