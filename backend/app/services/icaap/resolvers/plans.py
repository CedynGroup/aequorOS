"""Capital plan and management actions, from the APPROVED plan and attested run.

A draft plan is a working document that anyone with the role can change; the
ICAAP quotes the approved version and records which one. If the approval has
expired, the block still binds and the fact says so — an expired approval is a
governance finding the report should show, not a figure to hide.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException

from app.domain.icaap.blocks import SourceProbe
from app.schemas.capital_plan import CapitalPlanRead, CapitalPlanSummaryRead
from app.services import capital_plan as capital_plan_service
from app.services import enterprise_stress_signoff
from app.services.attestation.digests import digest_of
from app.services.icaap import resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"
_MAX_PROJECTION_YEARS = 5
_NO_APPROVED_PLAN = (
    "No approved capital plan exists for this institution. Approve the plan in "
    "Basel > Capital planning, then link this figure again."
)


def _summary(rc: ResolveContext) -> CapitalPlanSummaryRead:
    return capital_plan_service.get_capital_plan(rc.db, rc.access.ctx, rc.access.bank.id)


def _plan_key(plan: CapitalPlanRead) -> str:
    return f"plan:{plan.id}:v{plan.version}:{digest_of(plan.content.model_dump(mode='json'))}"


class CapitalPlanResolver:
    block_type = "capital_plan"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        summary = _summary(rc)
        if summary.approved is None:
            return SourceProbe(current_key=None, reason="no approved capital plan")
        return SourceProbe(current_key=_plan_key(summary.approved))

    def resolve(self, rc: ResolveContext) -> Resolution:
        summary = _summary(rc)
        plan = summary.approved
        if plan is None:
            raise Unavailable(_NO_APPROVED_PLAN)
        spec = rc.spec
        projection = summary.projection
        facts: dict[str, dict[str, Any]] = {
            "plan_version": resolvers.fact(spec, "plan_version", plan.version),
            "approval_expires_on": resolvers.fact(
                spec, "approval_expires_on", plan.approval_expires_at
            ),
            "approval_overdue": resolvers.fact(spec, "approval_overdue", plan.approval_overdue),
            "pillar2_addon_total_pct": resolvers.fact(
                spec,
                "pillar2_addon_total_pct",
                sum(
                    (addon.add_on_pct_rwa for addon in plan.content.pillar2_addons),
                    Decimal("0"),
                ),
            ),
        }
        tables: list[dict[str, Any]] = []
        baseline = None
        if projection is not None and projection.scenarios:
            baseline = next(
                (
                    scenario
                    for scenario in projection.scenarios
                    if scenario.scenario_code == "baseline"
                ),
                projection.scenarios[0],
            )
            table = resolvers.TableBuilder("projection", "Projected capital ratios")
            table.column("period", "Period", "text")
            table.column("car_pct", "Total capital ratio", "ratio_pct")
            table.column("tier1_pct", "Tier 1 ratio", "ratio_pct")
            table.column("cet1_pct", "CET1 ratio", "ratio_pct")
            for year in baseline.years:
                table.row(
                    {
                        "period": year.period_label,
                        "car_pct": year.car_pct,
                        "tier1_pct": year.tier1_pct,
                        "cet1_pct": year.cet1_pct,
                    }
                )
            tables.append(table.build())
        for index in range(1, _MAX_PROJECTION_YEARS + 1):
            year = None
            if baseline is not None:
                year = next((entry for entry in baseline.years if entry.year == index), None)
            facts[f"projection_y{index}_car_pct"] = resolvers.fact(
                spec,
                f"projection_y{index}_car_pct",
                None if year is None else year.car_pct,
            )
        addons = resolvers.TableBuilder("pillar2_addons", "Pillar 2 add-ons in the plan")
        addons.column("risk_type", "Risk", "text")
        addons.column("add_on_pct_rwa", "Add-on", "ratio_pct")
        addons.column("rationale", "Rationale", "text")
        for addon in plan.content.pillar2_addons:
            addons.row(
                {
                    "risk_type": addon.risk_type,
                    "add_on_pct_rwa": addon.add_on_pct_rwa,
                    "rationale": addon.rationale,
                }
            )
        tables.append(addons.build())
        notes: list[str] = []
        if summary.projection_unavailable is not None:
            notes.append(summary.projection_unavailable.reason)
        body = resolvers.payload(
            title=spec.title,
            as_of=(None if projection is None else projection.as_of_date),
            source_label=f"Approved capital plan, version {plan.version}",
            currency=rc.currency,
            tables=tables,
            notes=notes,
        )
        return Resolution(
            source_kind="plan",
            source_ref={
                "plan_id": str(plan.id),
                "version": plan.version,
                "content_digest": digest_of(plan.content.model_dump(mode="json")),
                "approval_expires_at": (
                    None
                    if plan.approval_expires_at is None
                    else plan.approval_expires_at.isoformat()
                ),
            },
            source_key=_plan_key(plan),
            source_as_of=(None if projection is None else projection.as_of_date),
            payload=body,
            facts=facts,
        )


def _action_sources(rc: ResolveContext) -> tuple[Any, Any, CapitalPlanRead | None]:
    if rc.period is None:
        raise resolvers.no_period(rc)
    try:
        run = enterprise_stress_signoff.resolve_attested_run_for_period(
            rc.db, rc.access.ctx, rc.access.bank.id, rc.period.id
        )
    except HTTPException as exc:
        raise Unavailable(
            "No Board-attested stress run exists for this year end, so the management "
            "actions it tested cannot be quoted."
        ) from exc
    signoff = enterprise_stress_signoff.latest_signoff_for_run(rc.db, rc.access.ctx, run.id)
    return run, signoff, _summary(rc).approved


class ManagementActionsResolver:
    block_type = "management_actions"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        if rc.period is None:
            return SourceProbe(current_key=None, reason="no reporting period for the as-of date")
        try:
            run = enterprise_stress_signoff.resolve_attested_run_for_period(
                rc.db, rc.access.ctx, rc.access.bank.id, rc.period.id
            )
        except HTTPException:
            return SourceProbe(current_key=None, reason="no Board-attested stress run")
        plan = _summary(rc).approved
        plan_part = "capplan:none" if plan is None else f"capplan:{plan.id}:v{plan.version}"
        return SourceProbe(
            current_key=f"run:{run.id}|{plan_part}",
            withdrawn=resolvers.run_withdrawn(rc.db, run),
        )

    def resolve(self, rc: ResolveContext) -> Resolution:
        run, signoff, plan = _action_sources(rc)
        spec = rc.spec
        snapshot = (run.inputs or {}).get("management_action_plan") or {}
        actions = snapshot.get("actions") if isinstance(snapshot, dict) else None
        tested = actions if isinstance(actions, list) else []
        table = resolvers.TableBuilder("tested_actions", "Management actions tested under stress")
        table.column("action", "Action", "text")
        table.column("trigger", "Trigger", "text")
        table.column("owner", "Owner", "text")
        for entry in tested:
            if not isinstance(entry, dict):
                continue
            table.row(
                {
                    "action": entry.get("action") or entry.get("label"),
                    "trigger": entry.get("trigger"),
                    "owner": entry.get("owner"),
                }
            )
        tables = [table.build()]
        if plan is not None and plan.content.management_actions:
            planned = resolvers.TableBuilder("planned_actions", "Actions in the approved plan")
            planned.column("action", "Action", "text")
            planned.column("trigger", "Trigger", "text")
            planned.column("owner", "Owner", "text")
            planned.column("estimated_impact", "Estimated impact", "text")
            for action in plan.content.management_actions:
                planned.row(
                    {
                        "action": action.action,
                        "trigger": action.trigger,
                        "owner": action.owner,
                        "estimated_impact": action.estimated_impact,
                    }
                )
            tables.append(planned.build())
        facts = {
            "action_count": resolvers.fact(spec, "action_count", len(tested)),
            "with_actions_stays_above_all_minima": resolvers.fact(
                spec,
                "with_actions_stays_above_all_minima",
                None if signoff is None else signoff.with_actions_stays_above_all_minima,
            ),
        }
        plan_part = "capplan:none" if plan is None else f"capplan:{plan.id}:v{plan.version}"
        body = resolvers.payload(
            title=spec.title,
            as_of=rc.cycle.as_of_date,
            source_label="Board-attested stress run and the approved capital plan",
            currency=rc.currency,
            tables=tables,
        )
        return Resolution(
            source_kind="run",
            source_ref={
                "run_id": str(run.id),
                "input_hash": run.input_hash,
                "capital_plan_id": None if plan is None else str(plan.id),
                "capital_plan_version": None if plan is None else plan.version,
            },
            source_key=f"run:{run.id}|{plan_part}",
            source_as_of=rc.cycle.as_of_date,
            payload=body,
            facts=facts,
            source_run_ids=(str(run.id),),
        )


__all__ = ["CapitalPlanResolver", "ManagementActionsResolver"]
