"""Governed macro-scenario library (docs/stress.md §3.1, Phase 1).

The authoritative CRUD + maker-checker lifecycle for ``MacroScenario`` — the
governed spine every later stress phase consumes. Effective governance mirrors
the return/desk approval model already in the codebase:

- ``draft`` scenarios are freely editable by an analyst (maker);
- ``submit`` moves a draft to ``pending_approval``;
- ``approve`` requires a DIFFERENT user with the approver role (maker ≠ checker),
  stamps ``approved_by``/``approval_timestamp`` and bumps the version;
- an ``approved`` scenario is immutable (edits are refused);
- ``archive`` retires a scenario from any live state.

Only an ``approved`` scenario may feed an official run — ``resolve_for_official_run``
is the guard the Phase 2 enterprise orchestrator calls. Every mutation carries a
non-empty reason and is audit-logged. All reads/writes are org-scoped in code
(defense-in-depth on top of the migration's RLS FORCE policies).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.stress.translation import MacroPathPoint, translate
from app.models import Bank, MacroScenario, MacroScenarioPath
from app.schemas.stress import (
    MacroModule,
    MacroPathRead,
    MacroScenarioApproval,
    MacroScenarioCreate,
    MacroScenarioListRead,
    MacroScenarioRead,
    MacroScenarioSummaryRead,
    MacroScenarioTransition,
    MacroScenarioUpdate,
    ScenarioTranslationRead,
)
from app.services.audit import record_event


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_scenario_or_404(
    db: Session, ctx: TenantContext, scenario_id: UUID
) -> MacroScenario:
    scenario = db.scalar(
        select(MacroScenario).where(
            MacroScenario.id == scenario_id,
            MacroScenario.organization_id == ctx.organization_id,
        )
    )
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Macro scenario not found."
        )
    return scenario


def _load_paths(db: Session, scenario_id: UUID) -> list[MacroScenarioPath]:
    return list(
        db.scalars(
            select(MacroScenarioPath)
            .where(MacroScenarioPath.scenario_id == scenario_id)
            .order_by(MacroScenarioPath.variable, MacroScenarioPath.year_index)
        )
    )


def _read(scenario: MacroScenario, paths: list[MacroScenarioPath]) -> MacroScenarioRead:
    return MacroScenarioRead(
        id=scenario.id,
        organization_id=scenario.organization_id,
        bank_id=scenario.bank_id,
        code=scenario.code,
        name=scenario.name,
        description=scenario.description,
        scenario_type=scenario.scenario_type,  # pyright: ignore[reportArgumentType]
        severity=scenario.severity,  # pyright: ignore[reportArgumentType]
        horizon_years=scenario.horizon_years,
        narrative=scenario.narrative,
        source=scenario.source,
        status=scenario.status,  # pyright: ignore[reportArgumentType]
        version=scenario.version,
        created_by=scenario.created_by,
        approved_by=scenario.approved_by,
        approval_timestamp=scenario.approval_timestamp,
        institution_type_applicability=scenario.institution_type_applicability,
        paths=[
            MacroPathRead(
                variable=path.variable,
                year_index=path.year_index,
                base_value=path.base_value,
                stress_value=path.stress_value,
            )
            for path in paths
        ],
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
    )


def _to_points(paths: list[MacroScenarioPath]) -> list[MacroPathPoint]:
    return [
        MacroPathPoint(
            variable=path.variable,
            year_index=path.year_index,
            base_value=path.base_value,
            stress_value=path.stress_value,
        )
        for path in paths
    ]


def _duplicate_code_exists(
    db: Session, ctx: TenantContext, bank_id: str | None, code: str
) -> bool:
    condition = MacroScenario.bank_id.is_(None) if bank_id is None else (
        MacroScenario.bank_id == bank_id
    )
    return (
        db.scalar(
            select(MacroScenario.id).where(
                MacroScenario.organization_id == ctx.organization_id,
                condition,
                MacroScenario.code == code,
            )
        )
        is not None
    )


def create_scenario(
    db: Session, ctx: TenantContext, payload: MacroScenarioCreate
) -> MacroScenarioRead:
    if payload.bank_id is not None:
        _get_bank_or_404(db, ctx, payload.bank_id)
    if _duplicate_code_exists(db, ctx, payload.bank_id, payload.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "scenario_code_exists",
                "message": f"A macro scenario with code '{payload.code}' already exists.",
            },
        )
    scenario = MacroScenario(
        organization_id=ctx.organization_id,
        bank_id=payload.bank_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        scenario_type=payload.scenario_type,
        severity=payload.severity,
        horizon_years=payload.horizon_years,
        narrative=payload.narrative,
        source=payload.source,
        status="draft",
        version=1,
        created_by=ctx.actor_user_id,
        institution_type_applicability=payload.institution_type_applicability,
    )
    db.add(scenario)
    db.flush()
    for path in payload.paths:
        db.add(
            MacroScenarioPath(
                organization_id=ctx.organization_id,
                scenario_id=scenario.id,
                variable=path.variable,
                year_index=path.year_index,
                base_value=path.base_value,
                stress_value=path.stress_value,
            )
        )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "scenario_code_exists",
                "message": f"A macro scenario with code '{payload.code}' already exists.",
            },
        ) from exc
    record_event(
        db,
        ctx,
        event_type="macro_scenario.created",
        entity_type="macro_scenario",
        entity_id=scenario.id,
        details={
            "code": payload.code,
            "scenario_type": payload.scenario_type,
            "bank_id": payload.bank_id,
            "reason": payload.reason,
        },
    )
    db.commit()
    return _read(scenario, _load_paths(db, scenario.id))


def list_scenarios(  # noqa: PLR0913 - the filter surface of one read
    db: Session,
    ctx: TenantContext,
    bank_id: str | None = None,
    scenario_type: str | None = None,
    status_filter: str | None = None,
    include_archived: bool = False,
) -> MacroScenarioListRead:
    conditions = [MacroScenario.organization_id == ctx.organization_id]
    if bank_id is not None:
        conditions.append(MacroScenario.bank_id == bank_id)
    if scenario_type is not None:
        conditions.append(MacroScenario.scenario_type == scenario_type)
    if status_filter is not None:
        conditions.append(MacroScenario.status == status_filter)
    elif not include_archived:
        conditions.append(MacroScenario.status != "archived")

    scenarios = list(
        db.scalars(
            select(MacroScenario).where(*conditions).order_by(MacroScenario.code)
        )
    )
    counts: dict[UUID, int] = {}
    if scenarios:
        rows = db.execute(
            select(MacroScenarioPath.scenario_id, func.count())
            .where(MacroScenarioPath.scenario_id.in_([s.id for s in scenarios]))
            .group_by(MacroScenarioPath.scenario_id)
        ).all()
        counts = {row[0]: int(row[1]) for row in rows}
    summaries = [
        MacroScenarioSummaryRead(
            id=scenario.id,
            bank_id=scenario.bank_id,
            code=scenario.code,
            name=scenario.name,
            scenario_type=scenario.scenario_type,  # pyright: ignore[reportArgumentType]
            severity=scenario.severity,  # pyright: ignore[reportArgumentType]
            horizon_years=scenario.horizon_years,
            status=scenario.status,  # pyright: ignore[reportArgumentType]
            version=scenario.version,
            path_count=int(counts.get(scenario.id, 0)),
            created_by=scenario.created_by,
            approved_by=scenario.approved_by,
            created_at=scenario.created_at,
            updated_at=scenario.updated_at,
        )
        for scenario in scenarios
    ]
    return MacroScenarioListRead(scenarios=summaries, total=len(summaries))


def get_scenario(
    db: Session, ctx: TenantContext, scenario_id: UUID
) -> MacroScenarioRead:
    scenario = _get_scenario_or_404(db, ctx, scenario_id)
    return _read(scenario, _load_paths(db, scenario.id))


def update_scenario(
    db: Session, ctx: TenantContext, scenario_id: UUID, payload: MacroScenarioUpdate
) -> MacroScenarioRead:
    scenario = _get_scenario_or_404(db, ctx, scenario_id)
    if scenario.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "not_editable",
                "message": (
                    f"Only draft scenarios can be edited; this scenario is "
                    f"'{scenario.status}'."
                ),
            },
        )
    scalar_fields = (
        "name",
        "description",
        "scenario_type",
        "severity",
        "horizon_years",
        "narrative",
        "source",
        "institution_type_applicability",
    )
    for field in scalar_fields:
        value = getattr(payload, field)
        if value is not None:
            setattr(scenario, field, value)

    if payload.paths is not None:
        for existing in _load_paths(db, scenario.id):
            db.delete(existing)
        db.flush()
        for path in payload.paths:
            db.add(
                MacroScenarioPath(
                    organization_id=ctx.organization_id,
                    scenario_id=scenario.id,
                    variable=path.variable,
                    year_index=path.year_index,
                    base_value=path.base_value,
                    stress_value=path.stress_value,
                )
            )
    # Whether the horizon or the paths changed, every path must stay within the
    # horizon (the create-time invariant, re-checked here).
    db.flush()
    paths = _load_paths(db, scenario.id)
    over_horizon = [p for p in paths if p.year_index > scenario.horizon_years]
    if over_horizon:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "path_exceeds_horizon",
                "message": (
                    f"{len(over_horizon)} path point(s) exceed horizon_years "
                    f"{scenario.horizon_years}."
                ),
            },
        )
    record_event(
        db,
        ctx,
        event_type="macro_scenario.updated",
        entity_type="macro_scenario",
        entity_id=scenario.id,
        details={"code": scenario.code, "reason": payload.reason},
    )
    db.commit()
    return _read(scenario, paths)


def submit_scenario(
    db: Session, ctx: TenantContext, scenario_id: UUID, payload: MacroScenarioTransition
) -> MacroScenarioRead:
    scenario = _get_scenario_or_404(db, ctx, scenario_id)
    if scenario.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "not_submittable",
                "message": (
                    f"Only a draft can be submitted for approval; this scenario is "
                    f"'{scenario.status}'."
                ),
            },
        )
    scenario.status = "pending_approval"
    record_event(
        db,
        ctx,
        event_type="macro_scenario.submitted",
        entity_type="macro_scenario",
        entity_id=scenario.id,
        details={"code": scenario.code, "reason": payload.reason},
    )
    db.commit()
    return _read(scenario, _load_paths(db, scenario.id))


def approve_scenario(
    db: Session, ctx: TenantContext, scenario_id: UUID, payload: MacroScenarioApproval
) -> MacroScenarioRead:
    scenario = _get_scenario_or_404(db, ctx, scenario_id)
    if scenario.status != "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "not_pending_approval",
                "message": (
                    f"Only a scenario pending approval can be approved; this scenario "
                    f"is '{scenario.status}'."
                ),
            },
        )
    # Maker ≠ checker (¶16): the approver must differ from the creator.
    if scenario.created_by is not None and scenario.created_by == ctx.actor_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "maker_is_checker",
                "message": "The scenario's creator cannot approve it (maker ≠ checker).",
            },
        )
    scenario.status = "approved"
    scenario.approved_by = ctx.actor_user_id
    scenario.approval_timestamp = utc_now()
    scenario.version = scenario.version + 1
    record_event(
        db,
        ctx,
        event_type="macro_scenario.approved",
        entity_type="macro_scenario",
        entity_id=scenario.id,
        details={
            "code": scenario.code,
            "version": scenario.version,
            "reason": payload.reason,
        },
    )
    db.commit()
    return _read(scenario, _load_paths(db, scenario.id))


def archive_scenario(
    db: Session, ctx: TenantContext, scenario_id: UUID, payload: MacroScenarioTransition
) -> MacroScenarioRead:
    scenario = _get_scenario_or_404(db, ctx, scenario_id)
    if scenario.status == "archived":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "already_archived",
                "message": "This scenario is already archived.",
            },
        )
    scenario.status = "archived"
    record_event(
        db,
        ctx,
        event_type="macro_scenario.archived",
        entity_type="macro_scenario",
        entity_id=scenario.id,
        details={"code": scenario.code, "reason": payload.reason},
    )
    db.commit()
    return _read(scenario, _load_paths(db, scenario.id))


def translate_scenario(
    db: Session, ctx: TenantContext, scenario_id: UUID, module: MacroModule
) -> ScenarioTranslationRead:
    """Preview the module's translated shock set for a scenario (any status).

    A read-only preview — it does NOT gate on approval. Consumption by an
    official run goes through ``resolve_for_official_run``.
    """
    scenario = _get_scenario_or_404(db, ctx, scenario_id)
    shocks = translate(_to_points(_load_paths(db, scenario.id)), module)
    return ScenarioTranslationRead(
        scenario_id=scenario.id,
        code=scenario.code,
        status=scenario.status,  # pyright: ignore[reportArgumentType]
        module=module,
        shocks={key: str(value) for key, value in sorted(shocks.items())},
    )


def resolve_for_official_run(
    db: Session, ctx: TenantContext, scenario_id: UUID
) -> MacroScenario:
    """Return an APPROVED scenario, or refuse (the official-run consumption guard).

    The Phase 2 enterprise orchestrator calls this before driving the engines, so
    a draft / pending / archived scenario can never mint an immutable run.
    """
    scenario = _get_scenario_or_404(db, ctx, scenario_id)
    if scenario.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "scenario_not_approved",
                "message": (
                    f"Only an approved scenario may be consumed by an official run; "
                    f"this scenario is '{scenario.status}'."
                ),
            },
        )
    return scenario
