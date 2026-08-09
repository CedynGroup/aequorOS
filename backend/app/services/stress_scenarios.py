"""Scenario library: merged catalogue + custom-scenario CRUD.

System (regulatory) scenarios come from ``scenario_catalog`` with their shock
levels resolved from the effective-dated ``ParamStressShock`` register —
locked, because those levels are the governed parameter set. Custom scenarios
are the desk's sandbox rows: editable, archivable, never consumed by official
runs or report generation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, StressScenario
from app.schemas.scenario_workbench import (
    ScenarioCatalogueEntryRead,
    ScenarioCatalogueRead,
    StressScenarioArchive,
    StressScenarioCreate,
    StressScenarioRead,
    StressScenarioUpdate,
    WorkbenchModule,
)
from app.services.audit import record_event
from app.services.scenario_catalog import (
    SHOCK_VOCABULARY,
    SYSTEM_SCENARIOS,
    system_shocks,
    validate_shocks,
)


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_scenario_or_404(
    db: Session, ctx: TenantContext, bank: Bank, module: str, scenario_id: UUID
) -> StressScenario:
    scenario = db.scalar(
        select(StressScenario).where(
            StressScenario.id == scenario_id,
            StressScenario.organization_id == ctx.organization_id,
            StressScenario.bank_id == bank.id,
            StressScenario.module == module,
        )
    )
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found.")
    return scenario


def _stringified(shocks: dict[str, Decimal]) -> dict[str, str]:
    return {key: str(value) for key, value in sorted(shocks.items())}


def _read(scenario: StressScenario) -> StressScenarioRead:
    return StressScenarioRead(
        id=scenario.id,
        module=scenario.module,  # pyright: ignore[reportArgumentType]
        code=scenario.code,
        name=scenario.name,
        description=scenario.description,
        shocks={key: str(value) for key, value in sorted(scenario.shocks.items())},
        created_by=scenario.created_by,
        is_archived=scenario.is_archived,
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
    )


def list_catalogue(  # noqa: PLR0913 - one read carries its full scoping
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    module: WorkbenchModule,
    reporting_period_id: UUID | None = None,
    include_archived: bool = False,
) -> ScenarioCatalogueRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    as_of = date.today()  # noqa: DTZ011 - date-only business resolution
    if reporting_period_id is not None:
        period = db.scalar(
            select(BankReportingPeriod).where(
                BankReportingPeriod.id == reporting_period_id,
                BankReportingPeriod.organization_id == ctx.organization_id,
                BankReportingPeriod.bank_id == bank.id,
            )
        )
        if period is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
            )
        as_of = period.period_end

    entries: list[ScenarioCatalogueEntryRead] = []
    for definition in SYSTEM_SCENARIOS[module]:
        shocks = system_shocks(db, ctx, bank, module, definition.code, as_of)
        entries.append(
            ScenarioCatalogueEntryRead(
                kind="system",
                code=definition.code,
                name=definition.label,
                description=definition.description or None,
                shocks=_stringified(shocks),
                locked=True,
                configured=definition.code == "baseline" or bool(shocks),
            )
        )
    conditions = [
        StressScenario.organization_id == ctx.organization_id,
        StressScenario.bank_id == bank.id,
        StressScenario.module == module,
    ]
    if not include_archived:
        conditions.append(StressScenario.is_archived.is_(False))
    custom_rows = db.scalars(
        select(StressScenario).where(*conditions).order_by(StressScenario.name)
    ).all()
    entries.extend(
        ScenarioCatalogueEntryRead(
            kind="custom",
            code=row.code,
            scenario_id=row.id,
            name=row.name,
            description=row.description,
            shocks={key: str(value) for key, value in sorted(row.shocks.items())},
            locked=False,
            configured=True,
            is_archived=row.is_archived,
        )
        for row in custom_rows
    )
    vocabulary = SHOCK_VOCABULARY[module]
    return ScenarioCatalogueRead(
        bank_id=bank.id,
        module=module,
        as_of_date=as_of,
        allowed_keys=sorted(vocabulary.exact_keys),
        allowed_prefixes=list(vocabulary.prefixes),
        scenarios=entries,
    )


def create_scenario(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    module: WorkbenchModule,
    payload: StressScenarioCreate,
) -> StressScenarioRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    system_codes = {definition.code for definition in SYSTEM_SCENARIOS[module]}
    if payload.code in system_codes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "code_reserved",
                "message": f"'{payload.code}' is a system scenario code for {module}.",
            },
        )
    validate_shocks(module, payload.shocks)
    scenario = StressScenario(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        module=module,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        shocks={key: str(value) for key, value in payload.shocks.items()},
        created_by=ctx.actor_user_id,
    )
    db.add(scenario)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "scenario_code_exists",
                "message": f"A {module} scenario with code '{payload.code}' already exists.",
            },
        ) from exc
    record_event(
        db,
        ctx,
        event_type="stress_scenario.created",
        entity_type="stress_scenario",
        entity_id=scenario.id,
        details={"module": module, "code": payload.code},
    )
    db.commit()
    return _read(scenario)


def update_scenario(  # noqa: PLR0913 - one write carries its full scoping
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    module: WorkbenchModule,
    scenario_id: UUID,
    payload: StressScenarioUpdate,
) -> StressScenarioRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    scenario = _get_scenario_or_404(db, ctx, bank, module, scenario_id)
    if payload.name is not None:
        scenario.name = payload.name
    if payload.description is not None:
        scenario.description = payload.description
    if payload.shocks is not None:
        validate_shocks(module, payload.shocks)
        scenario.shocks = {key: str(value) for key, value in payload.shocks.items()}
    record_event(
        db,
        ctx,
        event_type="stress_scenario.updated",
        entity_type="stress_scenario",
        entity_id=scenario.id,
        details={"module": module, "code": scenario.code},
    )
    db.commit()
    return _read(scenario)


def set_archived(  # noqa: PLR0913 - one write carries its full scoping
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    module: WorkbenchModule,
    scenario_id: UUID,
    payload: StressScenarioArchive,
) -> StressScenarioRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    scenario = _get_scenario_or_404(db, ctx, bank, module, scenario_id)
    scenario.is_archived = payload.is_archived
    record_event(
        db,
        ctx,
        event_type="stress_scenario.archived"
        if payload.is_archived
        else "stress_scenario.unarchived",
        entity_type="stress_scenario",
        entity_id=scenario.id,
        details={"module": module, "code": scenario.code},
    )
    db.commit()
    return _read(scenario)
