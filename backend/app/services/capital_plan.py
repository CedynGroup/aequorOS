"""ICAAP capital-planning workflow (product.md §Phase 2 item 10).

The plan document carries what management decides — the Pillar-2 add-on
register, management actions, the trigger framework — under the same
maker-checker + annual-approval discipline as the CFP. What the numbers say
is never stored in the plan: the multi-year ratio projection assembles at
read time from stored forecast runs (with the Pillar-1 + Pillar-2
requirement overlay), and the ILAAP component refreshes quarterly as an
append-only snapshot of the liquidity-adequacy evidence (LRMD ¶12/¶24/¶26:
a refreshable component, not an annual monolith).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    Bank,
    CapitalPlan,
    IlaapSnapshot,
    ParamCapitalThreshold,
    RegulatoryMetricResult,
    RegulatoryRun,
)
from app.schemas.capital_plan import (
    CapitalPlanApprove,
    CapitalPlanContent,
    CapitalPlanProjectionRead,
    CapitalPlanProjectionScenario,
    CapitalPlanProjectionYear,
    CapitalPlanPut,
    CapitalPlanRead,
    CapitalPlanSummaryRead,
    IlaapRefreshCreate,
    IlaapSnapshotListRead,
    IlaapSnapshotRead,
)
from app.services.audit import record_event
from app.services.liquidity_cfp import get_cfp
from app.services.liquidity_ewi import (
    _get_bank_or_404,  # noqa: PLC2701 - shared tenant guards, one definition
    _get_period_or_404,  # noqa: PLC2701
    escalation_state,
    evaluate_ewis,
)
from app.services.params import get_active_params

# ICAAP is an annual Board submission (BoG ICAAP Guideline ¶72).
_APPROVAL_VALIDITY_DAYS = 365
_ZERO = Decimal("0")


def _current_plan(db: Session, ctx: TenantContext, bank: Bank) -> CapitalPlan | None:
    return db.scalar(
        select(CapitalPlan)
        .where(
            CapitalPlan.organization_id == ctx.organization_id,
            CapitalPlan.bank_id == bank.id,
        )
        .order_by(CapitalPlan.version.desc())
        .limit(1)
    )


def _approved_plan(db: Session, ctx: TenantContext, bank: Bank) -> CapitalPlan | None:
    return db.scalar(
        select(CapitalPlan)
        .where(
            CapitalPlan.organization_id == ctx.organization_id,
            CapitalPlan.bank_id == bank.id,
            CapitalPlan.status == "approved",
        )
        .order_by(CapitalPlan.version.desc())
        .limit(1)
    )


def _read(plan: CapitalPlan) -> CapitalPlanRead:
    overdue = (
        plan.approval_expires_at is not None and plan.approval_expires_at < utc_now().date()
    )
    return CapitalPlanRead(
        id=plan.id,
        bank_id=plan.bank_id,
        version=plan.version,
        status=plan.status,  # pyright: ignore[reportArgumentType]
        content=CapitalPlanContent.model_validate(plan.content),
        prepared_by=plan.prepared_by,
        approved_by_user_id=plan.approved_by_user_id,
        approval_reference=plan.approval_reference,
        approval_timestamp=plan.approval_timestamp,
        approval_expires_at=plan.approval_expires_at,
        approval_overdue=overdue,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _forecast_runs(db: Session, ctx: TenantContext, bank: Bank) -> list[RegulatoryRun]:
    """Latest succeeded forecast run per scenario, all from the most recent
    reporting period that has any — mixing vintages across scenarios would
    make the projection lie."""
    latest_period_id = db.scalar(
        select(RegulatoryRun.reporting_period_id)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.module == "forecast",
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )
    if latest_period_id is None:
        return []
    runs = db.scalars(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == latest_period_id,
            RegulatoryRun.module == "forecast",
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
    ).all()
    latest: dict[str, RegulatoryRun] = {}
    for run in runs:
        latest.setdefault(run.scenario_code, run)
    return [latest[code] for code in sorted(latest)]


def _pillar1_min(db: Session, ctx: TenantContext, bank: Bank) -> Decimal | None:
    rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, utc_now().date()
    )
    for row in rows:
        if row.threshold_code == "car_min":
            return Decimal(str(row.value_pct))
    return None


def _projection(
    db: Session, ctx: TenantContext, bank: Bank, content: CapitalPlanContent | None
) -> CapitalPlanProjectionRead | None:
    runs = _forecast_runs(db, ctx, bank)
    pillar1 = _pillar1_min(db, ctx, bank)
    if not runs or pillar1 is None:
        return None
    pillar2 = sum(
        (entry.add_on_pct_rwa for entry in (content.pillar2_addons if content else [])),
        _ZERO,
    )
    requirement = pillar1 + pillar2
    scenarios: list[CapitalPlanProjectionScenario] = []
    for run in runs:
        years: list[CapitalPlanProjectionYear] = []
        for entry in run.metrics.get("path", []):
            car_raw = entry.get("car_pct")
            car = Decimal(str(car_raw)) if car_raw is not None else None
            years.append(
                CapitalPlanProjectionYear(
                    year=int(entry["year"]),
                    period_label=str(entry["period_label"]),
                    car_pct=car,
                    headroom_pp=(car - requirement) if car is not None else None,
                )
            )
        min_car_raw = run.metrics.get("min_car_pct")
        scenarios.append(
            CapitalPlanProjectionScenario(
                scenario_code=run.scenario_code,
                run_id=run.id,
                input_hash=run.input_hash,
                years=years,
                min_car_pct=Decimal(str(min_car_raw)) if min_car_raw is not None else None,
            )
        )
    return CapitalPlanProjectionRead(
        pillar1_min_pct=pillar1,
        pillar2_addon_pct=pillar2,
        total_requirement_pct=requirement,
        scenarios=scenarios,
    )


def _ilaap_read(snapshot: IlaapSnapshot) -> IlaapSnapshotRead:
    content = snapshot.content

    def dec(key: str) -> Decimal | None:
        value = content.get(key)
        return Decimal(str(value)) if value is not None else None

    return IlaapSnapshotRead(
        id=snapshot.id,
        reporting_period_id=snapshot.reporting_period_id,
        as_of_date=snapshot.as_of_date,
        adequate=snapshot.adequate,
        lcr_pct=dec("lcr_pct"),
        nsfr_pct=dec("nsfr_pct"),
        lcr_status=content.get("lcr_status"),
        nsfr_status=content.get("nsfr_status"),
        worst_stressed_lcr_pct=dec("worst_stressed_lcr_pct"),
        cfp_approved=bool(content.get("cfp_approved", False)),
        cfp_active=bool(content.get("cfp_active", False)),
        ewi_escalation_state=content.get("ewi_escalation_state"),
        notes=content.get("notes"),
        created_at=snapshot.created_at,
    )


def get_capital_plan(db: Session, ctx: TenantContext, bank_id: str) -> CapitalPlanSummaryRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    current = _current_plan(db, ctx, bank)
    approved = _approved_plan(db, ctx, bank)
    reference = approved or current
    latest_ilaap = db.scalar(
        select(IlaapSnapshot)
        .where(
            IlaapSnapshot.organization_id == ctx.organization_id,
            IlaapSnapshot.bank_id == bank.id,
        )
        .order_by(IlaapSnapshot.created_at.desc())
        .limit(1)
    )
    return CapitalPlanSummaryRead(
        current=_read(current) if current is not None else None,
        approved=_read(approved) if approved is not None else None,
        projection=_projection(
            db,
            ctx,
            bank,
            CapitalPlanContent.model_validate(reference.content)
            if reference is not None
            else None,
        ),
        latest_ilaap=_ilaap_read(latest_ilaap) if latest_ilaap is not None else None,
    )


def put_capital_plan(
    db: Session, ctx: TenantContext, bank_id: str, payload: CapitalPlanPut
) -> CapitalPlanRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    current = _current_plan(db, ctx, bank)
    if current is not None and current.status == "draft":
        plan = current
        plan.content = payload.content.model_dump(mode="json")
        plan.prepared_by = ctx.actor_user_id
    else:
        plan = CapitalPlan(
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            version=(current.version + 1) if current is not None else 1,
            status="draft",
            content=payload.content.model_dump(mode="json"),
            prepared_by=ctx.actor_user_id,
        )
        db.add(plan)
        db.flush()
    record_event(
        db,
        ctx,
        event_type="capital_plan.draft_saved",
        entity_type="capital_plan",
        entity_id=plan.id,
        details={"version": plan.version, "reason": payload.reason},
    )
    db.commit()
    return _read(plan)


def approve_capital_plan(
    db: Session, ctx: TenantContext, bank_id: str, payload: CapitalPlanApprove
) -> CapitalPlanRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    plan = _current_plan(db, ctx, bank)
    if plan is None or plan.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_draft_capital_plan",
                "message": "There is no draft capital plan awaiting approval.",
            },
        )
    if plan.prepared_by is not None and plan.prepared_by == ctx.actor_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "self_approval",
                "message": "The preparer of a plan cannot approve it (maker-checker).",
            },
        )
    content = CapitalPlanContent.model_validate(plan.content)
    if not content.trigger_framework or not content.management_actions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "capital_plan_incomplete",
                "message": (
                    "A capital plan needs at least one trigger and one management "
                    "action before Board approval."
                ),
            },
        )
    # The ICAAP submission is a multi-year plan: without stored forecast runs
    # there are no projected ratios to approve against.
    if not _forecast_runs(db, ctx, bank):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_forecast_run",
                "message": (
                    "No successful forecast run exists; run the forecast module "
                    "before approving the capital plan."
                ),
            },
        )
    now = utc_now()
    prior = _approved_plan(db, ctx, bank)
    if prior is not None:
        prior.status = "superseded"
    plan.status = "approved"
    plan.approved_by_user_id = ctx.actor_user_id
    plan.approval_reference = payload.approval_reference
    plan.approval_timestamp = now
    plan.approval_expires_at = (now + timedelta(days=_APPROVAL_VALIDITY_DAYS)).date()
    record_event(
        db,
        ctx,
        event_type="capital_plan.approved",
        entity_type="capital_plan",
        entity_id=plan.id,
        details={
            "version": plan.version,
            "approval_reference": payload.approval_reference,
            "reason": payload.reason,
        },
    )
    db.commit()
    return _read(plan)


def refresh_ilaap(
    db: Session, ctx: TenantContext, bank_id: str, payload: IlaapRefreshCreate
) -> IlaapSnapshotRead:
    """Mint the quarterly ILAAP component from stored liquidity state."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    baseline = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == "liquidity",
            RegulatoryRun.scenario_code == "baseline",
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )
    if baseline is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_baseline_run",
                "message": (
                    "A successful baseline liquidity run is required before the "
                    "ILAAP component can be refreshed for this reporting period."
                ),
            },
        )
    statuses = {
        row.metric_code: row.status
        for row in db.scalars(
            select(RegulatoryMetricResult).where(
                RegulatoryMetricResult.run_id == baseline.id
            )
        )
    }
    stressed = db.scalars(
        select(RegulatoryRun).where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == "liquidity",
            RegulatoryRun.scenario_code != "baseline",
            RegulatoryRun.status == "succeeded",
        )
    ).all()
    stressed_lcrs = [
        Decimal(str(run.metrics["lcr_pct"])) for run in stressed if "lcr_pct" in run.metrics
    ]
    cfp = get_cfp(db, ctx, bank_id)
    evaluations = evaluate_ewis(db, ctx, bank, period)
    cfp_active = bool(cfp.approved and cfp.approved.active)
    content = {
        "lcr_pct": str(baseline.metrics.get("lcr_pct")),
        "nsfr_pct": str(baseline.metrics.get("nsfr_pct")),
        "lcr_status": statuses.get("lcr_pct"),
        "nsfr_status": statuses.get("nsfr_pct"),
        "worst_stressed_lcr_pct": str(min(stressed_lcrs)) if stressed_lcrs else None,
        "baseline_run_id": str(baseline.id),
        "baseline_input_hash": baseline.input_hash,
        "cfp_approved": cfp.approved is not None,
        "cfp_active": cfp_active,
        "ewi_escalation_state": escalation_state(evaluations, cfp_active=cfp_active),
        "notes": payload.notes,
    }
    adequate = statuses.get("lcr_pct") == "green" and statuses.get("nsfr_pct") == "green"
    snapshot = IlaapSnapshot(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        as_of_date=period.period_end,
        content=content,
        adequate=adequate,
        created_by=ctx.actor_user_id,
    )
    db.add(snapshot)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="capital_plan.ilaap_refreshed",
        entity_type="ilaap_snapshot",
        entity_id=snapshot.id,
        details={
            "reporting_period_id": str(period.id),
            "adequate": adequate,
            "baseline_input_hash": baseline.input_hash,
        },
    )
    db.commit()
    return _ilaap_read(snapshot)


def list_ilaap_snapshots(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID | None = None
) -> IlaapSnapshotListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    conditions = [
        IlaapSnapshot.organization_id == ctx.organization_id,
        IlaapSnapshot.bank_id == bank.id,
    ]
    if reporting_period_id is not None:
        conditions.append(IlaapSnapshot.reporting_period_id == reporting_period_id)
    rows = db.scalars(
        select(IlaapSnapshot).where(*conditions).order_by(IlaapSnapshot.created_at.desc())
    ).all()
    return IlaapSnapshotListRead(snapshots=[_ilaap_read(row) for row in rows])
