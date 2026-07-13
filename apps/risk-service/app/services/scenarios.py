from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RiskScenario, ScenarioAssumption, ScenarioAssumptionHistory
from app.schemas.scenarios import (
    AssumptionCreate,
    AssumptionReview,
    AssumptionUpdate,
    ScenarioArchive,
    ScenarioCopy,
    ScenarioCreate,
    ScenarioInitialize,
    ScenarioMutationResponse,
    ScenarioRead,
    ScenarioReadinessRead,
    ScenarioUpdate,
    ScenarioValidationIssue,
    ScenarioValidationRead,
    ScenarioWorkspaceRead,
)
from app.services.audit import record_event
from app.services.cases import get_case_or_404

REQUIRED_CATEGORIES = (
    "growth",
    "expenses",
    "cash_flow_timing",
    "credit_usage",
    "repayment_behavior",
)

DEFAULT_ASSUMPTIONS: dict[str, tuple[dict[str, Any], ...]] = {
    "baseline": (
        {
            "category": "growth",
            "key": "revenue_growth_rate",
            "label": "Revenue growth",
            "value": 0.0,
            "unit": "ratio",
        },
        {
            "category": "expenses",
            "key": "expense_growth_rate",
            "label": "Expense growth",
            "value": 0.0,
            "unit": "ratio",
        },
        {
            "category": "cash_flow_timing",
            "key": "cash_flow_delay_days",
            "label": "Cash-flow delay",
            "value": 0,
            "unit": "days",
        },
        {
            "category": "credit_usage",
            "key": "credit_usage_rate",
            "label": "Credit usage",
            "value": 0.0,
            "unit": "ratio",
        },
        {
            "category": "repayment_behavior",
            "key": "repayment_rate",
            "label": "Repayment rate",
            "value": 1.0,
            "unit": "ratio",
        },
    ),
    "downside": (
        {
            "category": "growth",
            "key": "revenue_growth_rate",
            "label": "Revenue growth",
            "value": -0.1,
            "unit": "ratio",
        },
        {
            "category": "expenses",
            "key": "expense_growth_rate",
            "label": "Expense growth",
            "value": 0.1,
            "unit": "ratio",
        },
        {
            "category": "cash_flow_timing",
            "key": "cash_flow_delay_days",
            "label": "Cash-flow delay",
            "value": 30,
            "unit": "days",
        },
        {
            "category": "credit_usage",
            "key": "credit_usage_rate",
            "label": "Credit usage",
            "value": 0.8,
            "unit": "ratio",
        },
        {
            "category": "repayment_behavior",
            "key": "repayment_rate",
            "label": "Repayment rate",
            "value": 0.5,
            "unit": "ratio",
        },
    ),
}


def list_workspace(
    db: Session, ctx: TenantContext, case_id: UUID, *, include_archived: bool = False
) -> ScenarioWorkspaceRead:
    get_case_or_404(db, ctx.organization_id, case_id)
    scenarios = list(_scenario_rows(db, ctx, case_id, include_archived=include_archived))
    reads = [_read_scenario(db, scenario) for scenario in scenarios]
    return ScenarioWorkspaceRead(
        case_id=case_id,
        scenarios=reads,
        readiness=_readiness(case_id, reads),
    )


def get_scenario(db: Session, ctx: TenantContext, case_id: UUID, scenario_id: UUID) -> ScenarioRead:
    return _read_scenario(db, _scenario_or_404(db, ctx, case_id, scenario_id))


def initialize_defaults(
    db: Session, ctx: TenantContext, case_id: UUID, payload: ScenarioInitialize
) -> ScenarioWorkspaceRead:
    _require_actor(ctx)
    get_case_or_404(db, ctx.organization_id, case_id)
    existing_types = set(
        db.scalars(
            select(RiskScenario.scenario_type).where(
                RiskScenario.organization_id == ctx.organization_id,
                RiskScenario.case_id == case_id,
                RiskScenario.archived_at.is_(None),
            )
        )
    )
    for scenario_type, name in (("baseline", "Baseline"), ("downside", "Downside")):
        if scenario_type in existing_types:
            continue
        scenario = RiskScenario(
            organization_id=ctx.organization_id,
            case_id=case_id,
            name=name,
            description=f"Default {scenario_type} scenario",
            scenario_type=scenario_type,
            created_by=ctx.actor_user_id,
        )
        db.add(scenario)
        _flush(db, "Scenario defaults already exist.")
        for values in DEFAULT_ASSUMPTIONS[scenario_type]:
            assumption = ScenarioAssumption(
                organization_id=ctx.organization_id,
                case_id=case_id,
                scenario_id=scenario.id,
                provenance={"source": "system_default", "scenario_type": scenario_type},
                **values,
            )
            db.add(assumption)
            _flush(db, "Scenario defaults already exist.")
            _history(db, ctx, assumption, "initialized", payload.reason, values)
        record_event(
            db,
            ctx,
            event_type="scenario.initialized",
            entity_type="risk_scenario",
            entity_id=scenario.id,
            details={"scenario_type": scenario_type, "reason": payload.reason},
        )
    return _complete(
        db,
        "Scenario defaults already exist.",
        lambda: list_workspace(db, ctx, case_id),
    )


def create_scenario(
    db: Session, ctx: TenantContext, case_id: UUID, payload: ScenarioCreate
) -> ScenarioMutationResponse:
    _require_actor(ctx)
    get_case_or_404(db, ctx.organization_id, case_id)
    scenario = RiskScenario(
        organization_id=ctx.organization_id,
        case_id=case_id,
        name=payload.name.strip(),
        description=payload.description,
        scenario_type="custom",
        created_by=ctx.actor_user_id,
    )
    db.add(scenario)
    _flush(db, "Scenario could not be created.")
    record_event(
        db,
        ctx,
        event_type="scenario.created",
        entity_type="risk_scenario",
        entity_id=scenario.id,
        details={"reason": payload.reason},
    )
    return _complete(
        db,
        "Scenario could not be created.",
        lambda: _mutation_response(db, ctx, case_id, scenario.id),
    )


def update_scenario(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    payload: ScenarioUpdate,
) -> ScenarioMutationResponse:
    _require_actor(ctx)
    scenario = _active_scenario_or_404(db, ctx, case_id, scenario_id, for_update=True)
    changes = payload.model_dump(exclude={"reason"}, exclude_unset=True)
    if "name" in changes and changes["name"] is not None:
        changes["name"] = changes["name"].strip()
    previous = {field: getattr(scenario, field) for field in changes}
    for field, value in changes.items():
        setattr(scenario, field, value)
    record_event(
        db,
        ctx,
        event_type="scenario.updated",
        entity_type="risk_scenario",
        entity_id=scenario.id,
        details={
            "reason": payload.reason,
            "previous": jsonable_encoder(previous),
            "changes": changes,
        },
    )
    return _complete(
        db,
        "Scenario could not be updated.",
        lambda: _mutation_response(db, ctx, case_id, scenario.id),
    )


def copy_scenario(
    db: Session, ctx: TenantContext, case_id: UUID, scenario_id: UUID, payload: ScenarioCopy
) -> ScenarioMutationResponse:
    _require_actor(ctx)
    source = _active_scenario_or_404(db, ctx, case_id, scenario_id, for_update=True)
    copy = RiskScenario(
        organization_id=ctx.organization_id,
        case_id=case_id,
        name=payload.name.strip(),
        description=source.description,
        scenario_type="custom",
        copied_from_scenario_id=source.id,
        created_by=ctx.actor_user_id,
    )
    db.add(copy)
    _flush(db, "Scenario could not be copied.")
    for source_assumption in _assumption_rows(db, source.id):
        assumption = ScenarioAssumption(
            organization_id=ctx.organization_id,
            case_id=case_id,
            scenario_id=copy.id,
            category=source_assumption.category,
            key=source_assumption.key,
            label=source_assumption.label,
            value=source_assumption.value,
            unit=source_assumption.unit,
            provenance={
                **source_assumption.provenance,
                "source": "scenario_copy",
                "source_scenario_id": str(source.id),
                "source_assumption_id": str(source_assumption.id),
            },
            review_status="draft",
        )
        db.add(assumption)
        _flush(db, "Scenario could not be copied.")
        _history(
            db,
            ctx,
            assumption,
            "copied",
            payload.reason,
            {"source_assumption_id": str(source_assumption.id)},
        )
    record_event(
        db,
        ctx,
        event_type="scenario.copied",
        entity_type="risk_scenario",
        entity_id=copy.id,
        details={"source_scenario_id": str(source.id), "reason": payload.reason},
    )
    return _complete(
        db,
        "Scenario could not be copied.",
        lambda: _mutation_response(db, ctx, case_id, copy.id),
    )


def archive_scenario(
    db: Session, ctx: TenantContext, case_id: UUID, scenario_id: UUID, payload: ScenarioArchive
) -> ScenarioMutationResponse:
    _require_actor(ctx)
    scenario = _active_scenario_or_404(db, ctx, case_id, scenario_id, for_update=True)
    scenario.archived_at = datetime.now(UTC)
    record_event(
        db,
        ctx,
        event_type="scenario.archived",
        entity_type="risk_scenario",
        entity_id=scenario.id,
        details={"reason": payload.reason},
    )
    return _complete(
        db,
        "Scenario could not be archived.",
        lambda: _mutation_response(db, ctx, case_id, scenario.id),
    )


def create_assumption(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    payload: AssumptionCreate,
) -> ScenarioMutationResponse:
    _require_actor(ctx)
    _active_scenario_or_404(db, ctx, case_id, scenario_id, for_update=True)
    values = payload.model_dump(exclude={"reason"})
    values["provenance"] = {**values["provenance"], "source": "manual"}
    assumption = ScenarioAssumption(
        organization_id=ctx.organization_id,
        case_id=case_id,
        scenario_id=scenario_id,
        **values,
    )
    db.add(assumption)
    _flush(db, "An assumption with this key already exists in the scenario.")
    _history(db, ctx, assumption, "created", payload.reason, values)
    record_event(
        db,
        ctx,
        event_type="scenario_assumption.created",
        entity_type="scenario_assumption",
        entity_id=assumption.id,
        details={"scenario_id": str(scenario_id), "reason": payload.reason},
    )
    return _complete(
        db,
        "An assumption with this key already exists in the scenario.",
        lambda: _mutation_response(db, ctx, case_id, scenario_id),
    )


def update_assumption(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    assumption_id: UUID,
    payload: AssumptionUpdate,
) -> ScenarioMutationResponse:
    _require_actor(ctx)
    _active_scenario_or_404(db, ctx, case_id, scenario_id, for_update=True)
    assumption = _assumption_or_404(db, ctx, case_id, scenario_id, assumption_id, for_update=True)
    changes = payload.model_dump(exclude={"reason"}, exclude_unset=True)
    changes["provenance"] = {
        **changes.get("provenance", assumption.provenance),
        "source": "reviewer_edit",
    }
    previous = {field: getattr(assumption, field) for field in changes}
    for field, value in changes.items():
        setattr(assumption, field, value)
    assumption.review_status = "draft"
    assumption.reviewed_by = None
    assumption.reviewed_at = None
    _history(
        db,
        ctx,
        assumption,
        "updated",
        payload.reason,
        {
            field: {"old": jsonable_encoder(previous[field]), "new": jsonable_encoder(value)}
            for field, value in changes.items()
        },
    )
    record_event(
        db,
        ctx,
        event_type="scenario_assumption.updated",
        entity_type="scenario_assumption",
        entity_id=assumption.id,
        details={"scenario_id": str(scenario_id), "reason": payload.reason},
    )
    return _complete(
        db,
        "Assumption could not be updated.",
        lambda: _mutation_response(db, ctx, case_id, scenario_id),
    )


def review_assumption(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    assumption_id: UUID,
    payload: AssumptionReview,
) -> ScenarioMutationResponse:
    _require_actor(ctx)
    _active_scenario_or_404(db, ctx, case_id, scenario_id, for_update=True)
    assumption = _assumption_or_404(db, ctx, case_id, scenario_id, assumption_id, for_update=True)
    assumption.review_status = "reviewed"
    assumption.reviewed_by = ctx.actor_user_id
    assumption.reviewed_at = datetime.now(UTC)
    _history(db, ctx, assumption, "reviewed", payload.reason, {"review_status": "reviewed"})
    record_event(
        db,
        ctx,
        event_type="scenario_assumption.reviewed",
        entity_type="scenario_assumption",
        entity_id=assumption.id,
        details={"scenario_id": str(scenario_id), "reason": payload.reason},
    )
    return _complete(
        db,
        "Assumption could not be reviewed.",
        lambda: _mutation_response(db, ctx, case_id, scenario_id),
    )


def validation_for(
    db: Session, ctx: TenantContext, case_id: UUID, scenario_id: UUID
) -> ScenarioValidationRead:
    scenario = get_scenario(db, ctx, case_id, scenario_id)
    return _validation(scenario)


def readiness_for(db: Session, ctx: TenantContext, case_id: UUID) -> ScenarioReadinessRead:
    return list_workspace(db, ctx, case_id).readiness


def _validation(scenario: ScenarioRead) -> ScenarioValidationRead:
    issues: list[ScenarioValidationIssue] = []
    categories = {assumption.category for assumption in scenario.assumptions}
    for category in REQUIRED_CATEGORIES:
        if category not in categories:
            issues.append(
                ScenarioValidationIssue(
                    code="required_category_missing",
                    message=f"A {category.replace('_', ' ')} assumption is required.",
                    category=category,  # type: ignore[arg-type]
                )
            )
    for assumption in scenario.assumptions:
        if assumption.value is None:
            issues.append(
                ScenarioValidationIssue(
                    code="assumption_value_missing",
                    message=f"{assumption.label} requires a value.",
                    category=assumption.category,
                    assumption_id=assumption.id,
                )
            )
        if assumption.review_status != "reviewed":
            issues.append(
                ScenarioValidationIssue(
                    code="assumption_review_required",
                    message=f"{assumption.label} must be reviewed.",
                    category=assumption.category,
                    assumption_id=assumption.id,
                )
            )
    return ScenarioValidationRead(
        scenario_id=scenario.id,
        complete=not issues and scenario.archived_at is None,
        issue_count=len(issues),
        issues=issues,
    )


def _readiness(case_id: UUID, scenarios: list[ScenarioRead]) -> ScenarioReadinessRead:
    active = [scenario for scenario in scenarios if scenario.archived_at is None]
    validations = [_validation(scenario) for scenario in active]
    incomplete = [validation.scenario_id for validation in validations if not validation.complete]
    return ScenarioReadinessRead(
        case_id=case_id,
        ready=bool(active) and not incomplete,
        scenario_count=len(active),
        complete_scenario_count=len(active) - len(incomplete),
        incomplete_scenario_ids=incomplete,
    )


def _mutation_response(
    db: Session, ctx: TenantContext, case_id: UUID, scenario_id: UUID
) -> ScenarioMutationResponse:
    scenario = get_scenario(db, ctx, case_id, scenario_id)
    workspace = list_workspace(db, ctx, case_id)
    return ScenarioMutationResponse(
        scenario=scenario,
        validation=_validation(scenario),
        readiness=workspace.readiness,
    )


def _read_scenario(db: Session, scenario: RiskScenario) -> ScenarioRead:
    data = {column.name: getattr(scenario, column.key) for column in RiskScenario.__table__.columns}
    data["assumptions"] = list(_assumption_rows(db, scenario.id))
    return ScenarioRead.model_validate(data)


def _scenario_rows(
    db: Session, ctx: TenantContext, case_id: UUID, *, include_archived: bool
) -> list[RiskScenario]:
    stmt = select(RiskScenario).where(
        RiskScenario.organization_id == ctx.organization_id, RiskScenario.case_id == case_id
    )
    if not include_archived:
        stmt = stmt.where(RiskScenario.archived_at.is_(None))
    return list(db.scalars(stmt.order_by(RiskScenario.created_at, RiskScenario.id)))


def _assumption_rows(db: Session, scenario_id: UUID) -> list[ScenarioAssumption]:
    return list(
        db.scalars(
            select(ScenarioAssumption)
            .where(ScenarioAssumption.scenario_id == scenario_id)
            .order_by(ScenarioAssumption.category, ScenarioAssumption.key)
        )
    )


def _scenario_or_404(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    *,
    for_update: bool = False,
) -> RiskScenario:
    stmt = select(RiskScenario).where(
        RiskScenario.id == scenario_id,
        RiskScenario.organization_id == ctx.organization_id,
        RiskScenario.case_id == case_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    scenario = db.scalar(stmt)
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found.")
    return scenario


def _active_scenario_or_404(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    *,
    for_update: bool = False,
) -> RiskScenario:
    scenario = _scenario_or_404(db, ctx, case_id, scenario_id, for_update=for_update)
    if scenario.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Archived scenarios are read-only."
        )
    return scenario


def _assumption_or_404(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    assumption_id: UUID,
    *,
    for_update: bool = False,
) -> ScenarioAssumption:
    stmt = select(ScenarioAssumption).where(
        ScenarioAssumption.id == assumption_id,
        ScenarioAssumption.organization_id == ctx.organization_id,
        ScenarioAssumption.case_id == case_id,
        ScenarioAssumption.scenario_id == scenario_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    assumption = db.scalar(stmt)
    if assumption is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assumption not found.")
    return assumption


def _history(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    assumption: ScenarioAssumption,
    action: str,
    reason: str,
    changes: dict[str, Any],
) -> None:
    assert ctx.actor_user_id is not None
    db.add(
        ScenarioAssumptionHistory(
            organization_id=ctx.organization_id,
            case_id=assumption.case_id,
            scenario_id=assumption.scenario_id,
            assumption_id=assumption.id,
            action=action,
            changed_fields=jsonable_encoder(changes),
            reason=reason,
            changed_by=ctx.actor_user_id,
        )
    )


def _require_actor(ctx: TenantContext) -> None:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header is required."
        )


def _flush(db: Session, conflict_message: str) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict_message) from exc
    except Exception:
        db.rollback()
        raise


def _complete[T](db: Session, conflict_message: str, build_response: Callable[[], T]) -> T:
    try:
        db.flush()
        response = build_response()
        db.commit()
        return response
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict_message) from exc
    except Exception:
        db.rollback()
        raise
