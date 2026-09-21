"""Capital-plan triggers: the levels a plan promises to act at, evaluated.

A capital plan carries a trigger framework — an early-warning level and an
action level per metric — and until now nothing evaluated it. A promise to act
at a level nobody measures is not a control, so this module measures it: the
current position and every projected year of every scenario, against the levels
and against **that year's** regulatory floor (per-year resolution, DV-005 — a
floor that steps up in year 3 makes a year-3 action level that was fine in year
1 inadequate).

Two definitional findings, because a trigger framework can be self-defeating:

* ``ordering_inconsistent`` — the early warning must fire BEFORE the action
  level, which for a floor metric means a HIGHER number and for a ceiling a
  lower one. Reversed, the bank acts before it warns.
* ``action_weaker_than_floor`` — an action level below the regulatory minimum
  means the plan's own trigger only fires once the bank is already in breach.

The plan stores the metric as free text, so an unrecognised code is
``not_evaluable`` and says so, rather than being silently dropped or guessed
at. Nothing here knows a floor: the floors arrive per metric per date, resolved
from the console (D-024).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

from app.domain.policy import Direction

TriggerStatus = Literal["clear", "early_warning", "action", "regulatory_breach", "not_evaluable"]
FindingCode = Literal[
    "trigger_metric_unknown",
    "ordering_inconsistent",
    "action_weaker_than_floor",
    "early_warning_weaker_than_floor",
]

LEVEL_EARLY_WARNING = "early_warning"
LEVEL_ACTION = "action"
LEVEL_REGULATORY_BREACH = "regulatory_breach"


@dataclass(frozen=True)
class TriggerMetric:
    """A metric a plan may set triggers on. Structural — no levels here."""

    key: str
    aliases: frozenset[str]
    direction: Direction
    fact_key: str
    projection_field: str | None
    #: The governed code holding this metric's regulatory floor.
    floor_code: str | None


_METRICS: tuple[TriggerMetric, ...] = (
    TriggerMetric(
        key="car",
        aliases=frozenset({"car", "car_pct", "total_capital_ratio", "capital_adequacy_ratio"}),
        direction=Direction.FLOOR,
        fact_key="car_pct",
        projection_field="car_pct",
        floor_code="car_min",
    ),
    TriggerMetric(
        key="cet1",
        aliases=frozenset({"cet1", "cet1_ratio", "cet1_ratio_pct", "common_equity_tier1"}),
        direction=Direction.FLOOR,
        fact_key="cet1_ratio_pct",
        projection_field="cet1_ratio_pct",
        floor_code="cet1_min",
    ),
    TriggerMetric(
        key="tier1",
        aliases=frozenset({"tier1", "tier1_ratio", "tier1_ratio_pct"}),
        direction=Direction.FLOOR,
        fact_key="tier1_ratio_pct",
        projection_field="tier1_ratio_pct",
        floor_code="tier1_min",
    ),
    TriggerMetric(
        key="leverage",
        aliases=frozenset({"leverage", "leverage_ratio", "leverage_ratio_pct"}),
        direction=Direction.FLOOR,
        fact_key="leverage_ratio_pct",
        projection_field="leverage_ratio_pct",
        floor_code="leverage_min",
    ),
)

TRIGGER_METRICS: Mapping[str, TriggerMetric] = MappingProxyType(
    {metric.key: metric for metric in _METRICS}
)


def resolve_metric(metric_code: str | None) -> TriggerMetric | None:
    """Match the plan's free-text metric code to the catalogue, or don't."""
    if not metric_code:
        return None
    needle = metric_code.strip().casefold()
    for metric in _METRICS:
        if needle == metric.key or needle in metric.aliases:
            return metric
    return None


@dataclass(frozen=True)
class TriggerDef:
    """One trigger row from the plan's framework."""

    metric_code: str
    early_warning_level: Decimal
    action_level: Decimal


@dataclass(frozen=True)
class PathPoint:
    """A projected value for one year of one scenario."""

    year: int
    period_end: date
    value: Decimal | None


@dataclass(frozen=True)
class TriggerFinding:
    code: FindingCode
    metric_code: str
    scenario: str | None = None
    year: int | None = None
    detail: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class TriggerPoint:
    scenario: str
    year: int
    period_end: date
    value: Decimal | None
    status: TriggerStatus
    floor: Decimal | None


@dataclass(frozen=True)
class TriggerResult:
    metric_code: str
    metric_key: str | None
    direction: Direction | None
    current_value: Decimal | None
    current_status: TriggerStatus
    points: tuple[TriggerPoint, ...]
    #: scenario -> level -> the first projected year it is crossed.
    first_crossing: Mapping[str, Mapping[str, int]]
    findings: tuple[TriggerFinding, ...]


@dataclass(frozen=True)
class TriggerEvaluation:
    results: tuple[TriggerResult, ...]
    findings: tuple[TriggerFinding, ...]


def _weaker(direction: Direction, value: Decimal, level: Decimal) -> bool:
    """Is ``value`` on the unsafe side of ``level``? Equality is not."""
    return value < level if direction is Direction.FLOOR else value > level


def classify(
    direction: Direction,
    value: Decimal | None,
    trigger: TriggerDef,
    floor: Decimal | None,
) -> TriggerStatus:
    """Where one reading sits against the plan's levels and the floor."""
    if value is None:
        return "not_evaluable"
    if floor is not None and _weaker(direction, value, floor):
        return "regulatory_breach"
    if _weaker(direction, value, trigger.action_level):
        return "action"
    if _weaker(direction, value, trigger.early_warning_level):
        return "early_warning"
    return "clear"


def _definition_findings(
    trigger: TriggerDef,
    metric: TriggerMetric,
    scenario_floors: Mapping[date, Decimal],
    years: Mapping[date, int],
) -> list[TriggerFinding]:
    findings: list[TriggerFinding] = []
    if _weaker(metric.direction, trigger.early_warning_level, trigger.action_level):
        findings.append(
            TriggerFinding(
                code="ordering_inconsistent",
                metric_code=trigger.metric_code,
                detail=MappingProxyType(
                    {
                        "early_warning_level": str(trigger.early_warning_level),
                        "action_level": str(trigger.action_level),
                        "direction": metric.direction.value,
                    }
                ),
            )
        )
    for period_end, floor in sorted(scenario_floors.items()):
        if _weaker(metric.direction, trigger.action_level, floor):
            findings.append(
                TriggerFinding(
                    code="action_weaker_than_floor",
                    metric_code=trigger.metric_code,
                    year=years.get(period_end),
                    detail=MappingProxyType(
                        {
                            "action_level": str(trigger.action_level),
                            "floor": str(floor),
                            "period_end": period_end.isoformat(),
                            "floor_code": metric.floor_code or "",
                        }
                    ),
                )
            )
    return findings


def evaluate(
    triggers: Sequence[TriggerDef],
    *,
    current: Mapping[str, Decimal | None],
    paths: Mapping[str, Mapping[str, Sequence[PathPoint]]],
    floors: Mapping[str, Mapping[date, Decimal]],
) -> TriggerEvaluation:
    """Evaluate every trigger against today and every projected year."""
    results: list[TriggerResult] = []
    all_findings: list[TriggerFinding] = []

    for trigger in triggers:
        metric = resolve_metric(trigger.metric_code)
        if metric is None:
            finding = TriggerFinding(code="trigger_metric_unknown", metric_code=trigger.metric_code)
            all_findings.append(finding)
            results.append(
                TriggerResult(
                    metric_code=trigger.metric_code,
                    metric_key=None,
                    direction=None,
                    current_value=None,
                    current_status="not_evaluable",
                    points=(),
                    first_crossing=MappingProxyType({}),
                    findings=(finding,),
                )
            )
            continue

        metric_floors = floors.get(metric.key, {})
        metric_paths = paths.get(metric.key, {})
        years = {
            point.period_end: point.year
            for scenario_points in metric_paths.values()
            for point in scenario_points
        }
        findings = _definition_findings(trigger, metric, metric_floors, years)
        all_findings.extend(findings)

        points: list[TriggerPoint] = []
        crossings: dict[str, dict[str, int]] = {}
        for scenario in sorted(metric_paths):
            for point in sorted(metric_paths[scenario], key=lambda item: item.year):
                floor = metric_floors.get(point.period_end)
                status = classify(metric.direction, point.value, trigger, floor)
                points.append(
                    TriggerPoint(
                        scenario=scenario,
                        year=point.year,
                        period_end=point.period_end,
                        value=point.value,
                        status=status,
                        floor=floor,
                    )
                )
                if status in (LEVEL_EARLY_WARNING, LEVEL_ACTION, LEVEL_REGULATORY_BREACH):
                    crossed = crossings.setdefault(scenario, {})
                    crossed.setdefault(status, point.year)

        current_value = current.get(metric.key)
        current_status = classify(
            metric.direction, current_value, trigger, _latest_floor(metric_floors)
        )
        results.append(
            TriggerResult(
                metric_code=trigger.metric_code,
                metric_key=metric.key,
                direction=metric.direction,
                current_value=current_value,
                current_status=current_status,
                points=tuple(points),
                first_crossing=MappingProxyType(
                    {scenario: MappingProxyType(levels) for scenario, levels in crossings.items()}
                ),
                findings=tuple(findings),
            )
        )

    return TriggerEvaluation(results=tuple(results), findings=tuple(all_findings))


def _latest_floor(metric_floors: Mapping[date, Decimal]) -> Decimal | None:
    """The floor in force now: the earliest dated one the plan resolved."""
    if not metric_floors:
        return None
    return metric_floors[min(metric_floors)]


__all__ = [
    "LEVEL_ACTION",
    "LEVEL_EARLY_WARNING",
    "LEVEL_REGULATORY_BREACH",
    "TRIGGER_METRICS",
    "FindingCode",
    "PathPoint",
    "TriggerDef",
    "TriggerEvaluation",
    "TriggerFinding",
    "TriggerMetric",
    "TriggerPoint",
    "TriggerResult",
    "TriggerStatus",
    "classify",
    "evaluate",
    "resolve_metric",
]
