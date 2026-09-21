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

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import ConditionCheck, ConditionKind, Permission
from app.db.base import utc_now
from app.domain.policy import PolicyUnresolvedError
from app.models import (
    Bank,
    CapitalPlan,
    IlaapSnapshot,
    ParamCapitalThreshold,
    RegulatoryMetricResult,
    RegulatoryRun,
)
from app.schemas.capital_plan import (
    CapitalFloorRead,
    CapitalPlanApprove,
    CapitalPlanContent,
    CapitalPlanProjectionRead,
    CapitalPlanProjectionScenario,
    CapitalPlanProjectionYear,
    CapitalPlanPut,
    CapitalPlanRead,
    CapitalPlanSummaryRead,
    ConservationBufferTreatment,
    IlaapRefreshCreate,
    IlaapSnapshotListRead,
    IlaapSnapshotRead,
    ProjectionUnavailableRead,
)
from app.services import institution_types, regulatory_forecasting, regulatory_parameters
from app.services.audit import record_event
from app.services.institution_types import InstitutionTypeUnresolved
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
# A calendar year-end anchor (the financial year for the jurisdictions served
# today). The ICAAP framework's own ``as_of_date`` replaces this test once the
# workspace cycle owns the anchor.
_DECEMBER = 12
_LAST_DAY_OF_DECEMBER = 31


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
    overdue = plan.approval_expires_at is not None and plan.approval_expires_at < utc_now().date()
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


def _planning_horizon() -> ColumnElement[bool]:
    """SQL predicate: the forecast run projects the regulatory 5-year horizon.

    The ONE rule, shared with the ICAAP data companion
    (``regulatory_forecasting.regulatory_horizon_clause``), so the two ICAAP
    consumers can never disagree about which run counts. A desk run at another
    horizon must never become the capital plan's projection.
    """
    return regulatory_forecasting.regulatory_horizon_clause()


def _forecast_runs(db: Session, ctx: TenantContext, bank: Bank) -> list[RegulatoryRun]:
    """Latest succeeded 5-year forecast run per scenario, all from the most
    recent reporting period that has any — mixing vintages across scenarios
    would make the projection lie."""
    horizon = _planning_horizon()
    latest_period_id = db.scalar(
        select(RegulatoryRun.reporting_period_id)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.module == "forecast",
            RegulatoryRun.status == "succeeded",
            horizon,
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
            horizon,
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
    ).all()
    latest: dict[str, RegulatoryRun] = {}
    for run in runs:
        latest.setdefault(run.scenario_code, run)
    return [latest[code] for code in sorted(latest)]


class _ProjectionUnavailable(Exception):  # noqa: N818 - a state, reported as data
    """The projection cannot be measured; the rest of the plan summary can.

    Raised inside :func:`_projection` and turned into
    ``CapitalPlanSummaryRead.projection_unavailable`` by :func:`get_capital_plan`,
    so the plan document, its approval state and the ILAAP evidence stay
    readable (QA P0-QA-004, architecture M1). The reason is written for the
    user and carries no resolver internals (security audit L-5).
    """

    def __init__(self, error_code: str, reason: str, param_code: str | None = None) -> None:
        super().__init__(reason)
        self.read = ProjectionUnavailableRead(
            error_code=error_code,  # pyright: ignore[reportArgumentType]
            reason=reason,
            param_code=param_code,
        )


def _missing_floor(code: str) -> _ProjectionUnavailable:
    return _ProjectionUnavailable(
        "missing_parameter",
        "No minimum capital adequacy ratio is configured for this institution, either in "
        "its own parameter register or in the regulatory parameter set, so the plan's "
        "projected headroom cannot be measured. The plan itself is unaffected.",
        code,
    )


def _unresolved_institution_type() -> _ProjectionUnavailable:
    return _ProjectionUnavailable(
        "institution_type_unresolved",
        "The institution's licence type is not recognised, so the regulatory capital "
        "minima that apply to it cannot be determined and the projection is not "
        "measured. The plan itself is unaffected.",
    )


def _unresolved_jurisdiction() -> _ProjectionUnavailable:
    return _ProjectionUnavailable(
        "jurisdiction_unresolved",
        "The institution's jurisdiction is not configured, so its regulatory capital "
        "minima cannot be determined and the projection is not measured. The plan "
        "itself is unaffected.",
    )


def _capital_floors(  # noqa: PLR0913 - the resolution key plus the regime
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    codes: tuple[str, ...],
    *,
    basel: bool = True,
) -> dict[str, CapitalFloorRead]:
    """The effective capital minima at ``as_of``, each with its authority.

    ICAAP P0 (regulatory audit §4.1 / M24). This used to read the board
    register's ``car_min`` RAW, at today's date: the default bank register holds
    10, so capital-plan headroom was measured against 10% while the filed
    capital return and the ICAAP stress measured against the governed 13%, and
    a tenant with no register row silently lost its projection. It now takes
    the same route as ``enterprise_stress._capital_params``: the whole register
    through ``regulatory_parameters.clamp_overrides`` (tighten-only), then the
    control-plane value where the register has none. Resolution is at the
    projection's as-of date, so a floor that changes later cannot rewrite it.
    """
    register = {
        row.threshold_code: _normalized(Decimal(str(row.value_pct)))
        for row in get_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
        )
    }
    try:
        effective = regulatory_parameters.clamp_overrides(db, bank, register, as_of=as_of).values
        governed = {
            code: regulatory_parameters.try_resolve(db, bank, code, as_of=as_of) for code in codes
        }
    except InstitutionTypeUnresolved as exc:
        raise _unresolved_institution_type() from exc
    except PolicyUnresolvedError as exc:
        # The policy scope fails on its jurisdiction link (the licence-type link
        # raises ``InstitutionTypeUnresolved`` above).
        raise _unresolved_jurisdiction() from exc
    floors: dict[str, CapitalFloorRead] = {}
    for code in codes:
        control = governed[code]
        regulatory = control.normalized_value if control is not None else None
        board = register.get(code)
        value = effective.get(code, regulatory)
        if value is None:
            continue
        floors[code] = CapitalFloorRead(
            param_code=code,
            value_pct=value,
            source=(
                "control_plane"
                if regulatory is not None and value == regulatory
                else "board_register"
            ),
            board_register_pct=board,
            raised_to_regulatory_floor=board is not None and value != board,
            regulatory_value_pct=regulatory,
            source_citation=control.source_citation if control is not None else None,
            confirmation_status=control.confirmation_status if control is not None else None,
            effective_from=control.effective_from if control is not None else None,
            conservation_buffer=_conservation_buffer(code, basel=basel),
        )
    return floors


def effective_capital_floors(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    codes: tuple[str, ...],
) -> dict[str, CapitalFloorRead]:
    """The effective capital minima at ``as_of``, for callers outside the plan.

    Additive public seam over :func:`_capital_floors`, added for the AI fact
    sheet's ``vs_limit`` descriptor. A narrative that says a ratio "meets the
    minimum" must be measured against exactly the floor the capital plan shows —
    clamped tighten-only, with its confirmation status — or the report's prose
    and its own capital table would disagree. Reimplementing the resolution here
    is precisely how that happens, so this wraps rather than repeats it.
    """
    return _capital_floors(db, ctx, bank, as_of, codes)


def capital_floors_by_date(  # noqa: PLR0913 - the resolution key plus the regime
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    dates: Sequence[date],
    codes: tuple[str, ...],
    *,
    basel: bool = True,
) -> dict[date, dict[str, CapitalFloorRead]]:
    """The effective capital minima at each of several dates (DV-005).

    A capital plan projects several years forward, and a floor that changes
    during the horizon applies to the years after it commences, not to all of
    them. Resolving once at the as-of date and reusing that answer would
    measure year three against a regime that had been replaced — headroom that
    reads as comfort and is not.

    Each date is resolved independently through :func:`_capital_floors`, which
    keeps one definition of "the effective minimum" (board register clamped
    tighten-only against the control plane) rather than a second one here.
    """
    return {as_of: _capital_floors(db, ctx, bank, as_of, codes, basel=basel) for as_of in dates}


def _conservation_buffer(code: str, *, basel: bool) -> ConservationBufferTreatment:
    """Whether a minimum includes the capital conservation buffer (CRD ¶71, ¶75):
    the bank total-capital minimum does; the CET1 / Tier 1 minima do not (the
    buffer is held in CET1 on top of them); an SDI has no buffer regime."""
    if not basel:
        return "not_applicable"
    return "included" if code == "car_min" else "excluded"


def _normalized(value: Decimal) -> Decimal:
    """Strip the scale a ``Numeric`` round-trip adds (``10.0000`` -> ``10``), the
    same presentation ``ResolvedParameter.normalized_value`` gives a governed
    value, so a board value and a regulatory value compare and print alike."""
    return value.quantize(Decimal(1)) if value == value.to_integral_value() else value.normalize()


def _anniversary(as_of: date, years: int) -> date:
    """``as_of`` moved forward by whole years (29 Feb lands on 28 Feb)."""
    try:
        return as_of.replace(year=as_of.year + years)
    except ValueError:
        return as_of.replace(year=as_of.year + years, day=28)


def _is_year_end(as_of: date) -> bool:
    return as_of.month == _DECEMBER and as_of.day == _LAST_DAY_OF_DECEMBER


def _year_label(year: int, period_end: date, *, year_end_aligned: bool) -> str:
    """FY-end labels when the projection is anchored on a year-end.

    The forecast engine labels projected years ``YYYY-MM`` of its own period
    end, which reads as a financial year only when that period ends in
    December. The capital plan is the ICAAP view, anchored on the financial
    year-end, so a year-end-anchored projection labels ``FY<year>`` with the
    exact date; any other anchor says plainly which date each year runs to.
    """
    if year == 0:
        prefix = f"FY{period_end.year} " if year_end_aligned else ""
        return f"{prefix}(as of {period_end.isoformat()})".strip()
    if year_end_aligned:
        return f"FY{period_end.year} ({period_end.isoformat()})"
    return f"Year {year} (to {period_end.isoformat()})"


def _ratio(entry: dict[str, Any], key: str) -> Decimal | None:
    raw = entry.get(key)
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _headroom(value: Decimal | None, floor: Decimal | None) -> Decimal | None:
    return None if value is None or floor is None else value - floor


def _projection(
    db: Session, ctx: TenantContext, bank: Bank, content: CapitalPlanContent | None
) -> CapitalPlanProjectionRead | None:
    runs = _forecast_runs(db, ctx, bank)
    if not runs:
        return None
    period = _get_period_or_404(db, ctx, bank, runs[0].reporting_period_id)
    as_of = period.period_end
    # The Basel CET1 / Tier 1 minima exist only under the bank (CRD) regime; an
    # SDI's s.29 solvency test has no sub-tier floor, so none is shown.
    try:
        basel = institution_types.institution_class(db, bank) == "bank"
    except InstitutionTypeUnresolved as exc:
        raise _unresolved_institution_type() from exc
    codes = ("car_min", "tier1_min", "cet1_min") if basel else ("car_min",)
    floors = _capital_floors(db, ctx, bank, as_of, codes, basel=basel)
    pillar1 = floors.get("car_min")
    if pillar1 is None:
        raise _missing_floor("car_min")
    tier1_min = floors.get("tier1_min")
    cet1_min = floors.get("cet1_min")
    pillar2 = sum(
        (entry.add_on_pct_rwa for entry in (content.pillar2_addons if content else [])),
        _ZERO,
    )
    requirement = pillar1.value_pct + pillar2
    aligned = _is_year_end(as_of)
    # DV-005: each projected year end is measured against the regime in force
    # AT THAT YEAR END. With today's open-ended generations every year resolves
    # to the as-of values, so nothing moves; when a floor is given a later
    # commencement date, the years after it switch on their own.
    projected_ends = sorted(
        {
            _anniversary(as_of, int(entry["year"]))
            for run in runs
            for entry in run.metrics.get("path", [])
        }
        | {as_of}
    )
    floors_by_date = capital_floors_by_date(db, ctx, bank, projected_ends, codes, basel=basel)
    scenarios: list[CapitalPlanProjectionScenario] = []
    for run in runs:
        years: list[CapitalPlanProjectionYear] = []
        for entry in run.metrics.get("path", []):
            year = int(entry["year"])
            period_end = _anniversary(as_of, year)
            year_floors = floors_by_date.get(period_end, floors)
            year_pillar1 = year_floors.get("car_min", pillar1)
            year_requirement = year_pillar1.value_pct + pillar2
            year_tier1_min = year_floors.get("tier1_min")
            year_cet1_min = year_floors.get("cet1_min")
            car = _ratio(entry, "car_pct")
            tier1 = _ratio(entry, "tier1_ratio_pct") if basel else None
            cet1 = _ratio(entry, "cet1_ratio_pct") if basel else None
            years.append(
                CapitalPlanProjectionYear(
                    year=year,
                    period_label=_year_label(year, period_end, year_end_aligned=aligned),
                    period_end=period_end,
                    car_pct=car,
                    headroom_pp=_headroom(car, year_requirement),
                    tier1_pct=tier1,
                    tier1_headroom_pp=_headroom(
                        tier1, year_tier1_min.value_pct if year_tier1_min is not None else None
                    ),
                    cet1_pct=cet1,
                    cet1_headroom_pp=_headroom(
                        cet1, year_cet1_min.value_pct if year_cet1_min is not None else None
                    ),
                    pillar1_min_pct=year_pillar1.value_pct,
                    total_requirement_pct=year_requirement,
                    tier1_min_pct=(None if year_tier1_min is None else year_tier1_min.value_pct),
                    cet1_min_pct=None if year_cet1_min is None else year_cet1_min.value_pct,
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
        as_of_date=as_of,
        year_end_aligned=aligned,
        basel_ratios_applicable=basel,
        pillar1_min_pct=pillar1.value_pct,
        pillar1_min=pillar1,
        pillar2_addon_pct=pillar2,
        total_requirement_pct=requirement,
        tier1_min=tier1_min,
        cet1_min=cet1_min,
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
    projection: CapitalPlanProjectionRead | None = None
    unavailable: ProjectionUnavailableRead | None = None
    try:
        projection = _projection(
            db,
            ctx,
            bank,
            CapitalPlanContent.model_validate(reference.content) if reference is not None else None,
        )
    except _ProjectionUnavailable as exc:
        unavailable = exc.read
    return CapitalPlanSummaryRead(
        current=_read(current) if current is not None else None,
        approved=_read(approved) if approved is not None else None,
        projection=projection,
        projection_unavailable=unavailable,
        latest_ilaap=_ilaap_read(latest_ilaap) if latest_ilaap is not None else None,
    )


def required_draft_permission(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
) -> Permission:
    """Choose create for a new plan version and edit for an existing draft."""

    current = _current_plan(db, ctx, bank)
    return (
        Permission.EDIT if current is not None and current.status == "draft" else Permission.CREATE
    )


def maker_checker_conditions(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
) -> tuple[ConditionCheck, ...]:
    """Resolve the plan's preparer before evaluating approval authority."""

    bank = _get_bank_or_404(db, ctx, bank_id)
    plan = _current_plan(db, ctx, bank)
    distinct = plan is None or plan.prepared_by is None or plan.prepared_by != ctx.actor_user_id
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "capital plan approver is distinct from preparer"
                if distinct
                else "capital plan preparer cannot approve the same plan"
            ),
        ),
    )


def put_capital_plan(
    db: Session, ctx: TenantContext, bank_id: str, payload: CapitalPlanPut, *, commit: bool = True
) -> CapitalPlanRead:
    """Save the draft plan. ``commit=False`` lets a caller add its own audit
    event to the same transaction — the ICAAP Pillar 2 proposal does, so the
    plan and the record of why it was proposed land together or not at all.
    The maker-checker is untouched: the caller still becomes ``prepared_by``,
    and a different person must approve."""
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
    if commit:
        db.commit()
    else:
        db.flush()
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
                    "A successful 5-year forecast run is required before the capital plan "
                    "can be approved: the plan projects the regulatory 5-year horizon, and "
                    "forecast runs at other horizons do not count. Run a 5-year forecast "
                    "first."
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
            select(RegulatoryMetricResult).where(RegulatoryMetricResult.run_id == baseline.id)
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
