"""The capital plan's trigger framework, evaluated against this ICAAP's figures.

Every approved capital plan carries triggers — an early-warning level and an
action level per ratio — and until now nothing evaluated them. This does: the
current position comes from the ICAAP's bound capital position, the projected
path from the bound capital plan's projection, and the regulatory floor from
the control plane AT EACH PROJECTED YEAR END (DV-005), because a trigger set
above today's floor may sit below the floor that applies in year three.

A trigger weaker than the regulatory floor is reported, never corrected: the
plan is the Board's, and a platform that quietly tightened it would be
reporting something the Board never agreed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap import triggers as domain
from app.models.icaap import IcaapCycle
from app.schemas.icaap_risk_capital import (
    IcaapParameterUseRead,
    IcaapTriggerEvaluationRead,
    IcaapTriggerPointRead,
    IcaapTriggerResultRead,
)
from app.services.icaap import blocks as blocks_service
from app.services.icaap import floors as floors_service
from app.services.icaap import guards, pillar2_inputs

_LEVELS = (
    domain.LEVEL_EARLY_WARNING,
    domain.LEVEL_ACTION,
    domain.LEVEL_REGULATORY_BREACH,
)
_BREACHING = frozenset({domain.LEVEL_ACTION, domain.LEVEL_REGULATORY_BREACH})


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _plan_triggers(payload: Mapping[str, Any]) -> list[domain.TriggerDef]:
    for table in payload.get("tables", []):
        if not isinstance(table, dict) or table.get("key") != "trigger_framework":
            continue
        out: list[domain.TriggerDef] = []
        for entry in table.get("rows", []):
            cells = entry.get("cells", {}) if isinstance(entry, dict) else {}
            early = _decimal(cells.get("early_warning_level"))
            action = _decimal(cells.get("action_level"))
            code = cells.get("metric_code") or cells.get("metric")
            if code is None or early is None or action is None:
                continue
            out.append(
                domain.TriggerDef(
                    metric_code=str(code), early_warning_level=early, action_level=action
                )
            )
        return out
    return []


def _plan_paths(
    payload: Mapping[str, Any],
) -> dict[str, dict[str, list[domain.PathPoint]]]:
    """Projected ratios by metric and scenario, from the bound plan block."""
    paths: dict[str, dict[str, list[domain.PathPoint]]] = {}
    for table in payload.get("tables", []):
        if not isinstance(table, dict) or table.get("key") != "projection":
            continue
        for entry in table.get("rows", []):
            cells = entry.get("cells", {}) if isinstance(entry, dict) else {}
            scenario = str(cells.get("scenario_code") or "baseline")
            year = _decimal(cells.get("year"))
            period_end = _date(cells.get("period_end"))
            if year is None or period_end is None:
                continue
            for metric in domain.TRIGGER_METRICS.values():
                if metric.projection_field is None:
                    continue
                value = _decimal(cells.get(metric.projection_field))
                paths.setdefault(metric.key, {}).setdefault(scenario, []).append(
                    domain.PathPoint(year=int(year), period_end=period_end, value=value)
                )
    return paths


def _date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _floors_by_metric(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, dates: Sequence[date]
) -> tuple[dict[str, dict[date, Decimal]], list[dict[str, Any]]]:
    resolved = floors_service.capital_floors_by_date(db, access, dates=list(dates))
    by_metric: dict[str, dict[date, Decimal]] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for as_of, floors in resolved.items():
        for metric in domain.TRIGGER_METRICS.values():
            if metric.floor_code is None:
                continue
            floor = floors.get(metric.floor_code)
            if floor is None:
                continue
            by_metric.setdefault(metric.key, {})[as_of] = floor.value_pct
            provenance.setdefault(
                metric.floor_code,
                {
                    "param_code": metric.floor_code,
                    "value": str(floor.value_pct),
                    "confirmation_status": floor.confirmation_status,
                    "source_citation": floor.source_citation,
                    "effective_from": floor.effective_from,
                    "representative": bool(
                        floor.source_citation
                        and floor.source_citation.startswith("REPRESENTATIVE:")
                    ),
                },
            )
    return by_metric, list(provenance.values())


def evaluate(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapTriggerEvaluationRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    plan = bound.get("capital_plan")
    capital = bound.get(pillar2_inputs.BLOCK_CAPITAL)
    if plan is None:
        return IcaapTriggerEvaluationRead(
            cycle_id=cycle.id,
            trigger_count=0,
            triggers_breached_now=0,
            results=[],
            findings=[],
            floors=[],
            unavailable={
                "error_code": "capital_plan_not_linked",
                "message": (
                    "Link this ICAAP's capital plan to see how its triggers behave over "
                    "the projection."
                ),
            },
        )
    payload = plan.payload or {}
    definitions = _plan_triggers(payload)
    paths = _plan_paths(payload)
    period_ends = sorted(
        {
            point.period_end
            for by_scenario in paths.values()
            for points in by_scenario.values()
            for point in points
        }
        | {cycle.as_of_date}
    )
    try:
        floors, provenance = _floors_by_metric(db, access, cycle, period_ends)
    except HTTPException as exc:
        return IcaapTriggerEvaluationRead(
            cycle_id=cycle.id,
            trigger_count=len(definitions),
            triggers_breached_now=0,
            results=[],
            findings=[],
            floors=[],
            unavailable=floors_service.unavailable_detail(exc),
        )
    current = {
        metric.key: _decimal(blocks_service.fact_value(capital, metric.fact_key))
        for metric in domain.TRIGGER_METRICS.values()
    }
    result = domain.evaluate(
        definitions,
        current=current,
        paths={
            key: {scenario: tuple(points) for scenario, points in value.items()}
            for key, value in paths.items()
        },
        floors=floors,
    )
    plan_version = _decimal(blocks_service.fact_value(plan, "plan_version"))
    first_action = _first_action_year(result)
    return IcaapTriggerEvaluationRead(
        cycle_id=cycle.id,
        plan_version=None if plan_version is None else int(plan_version),
        trigger_count=len(result.results),
        triggers_breached_now=len(
            [entry for entry in result.results if entry.current_status in _BREACHING]
        ),
        first_action_year=first_action,
        results=[
            IcaapTriggerResultRead(
                metric_code=entry.metric_code,
                metric_key=entry.metric_key,
                direction=None if entry.direction is None else entry.direction.value,  # pyright: ignore[reportArgumentType]
                early_warning_level=_level(definitions, entry.metric_code, early=True),
                action_level=_level(definitions, entry.metric_code, early=False),
                current_value=entry.current_value,
                current_status=entry.current_status,
                points=[
                    IcaapTriggerPointRead(
                        scenario_code=point.scenario,
                        year=point.year,
                        period_end=point.period_end,
                        value=point.value,
                        status=point.status,
                        floor_pct=point.floor,
                    )
                    for point in entry.points
                ],
                first_crossing={
                    scenario: dict(levels) for scenario, levels in entry.first_crossing.items()
                },
                findings=[finding.code for finding in entry.findings],
            )
            for entry in result.results
        ],
        findings=[finding.code for finding in result.findings],
        floors=[IcaapParameterUseRead(**entry) for entry in provenance],
    )


def _level(
    definitions: Sequence[domain.TriggerDef], metric_code: str, *, early: bool
) -> Decimal | None:
    for definition in definitions:
        if definition.metric_code == metric_code:
            return definition.early_warning_level if early else definition.action_level
    return None


def _first_action_year(result: domain.TriggerEvaluation) -> int | None:
    years = [
        year
        for entry in result.results
        for levels in entry.first_crossing.values()
        for level, year in levels.items()
        if level in _BREACHING
    ]
    return min(years) if years else None


def trigger_payload(read: IcaapTriggerEvaluationRead) -> dict[str, Any]:
    return {
        "triggers": [
            {
                "metric_code": entry.metric_code,
                "metric_key": entry.metric_key,
                "current_status": entry.current_status,
                "current_value": None if entry.current_value is None else str(entry.current_value),
                "first_crossing": entry.first_crossing,
            }
            for entry in read.results
        ]
    }


__all__ = ["evaluate", "trigger_payload"]
