from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    CalculationForecastPeriod,
    CalculationRun,
    CapitalIndicator,
    CapitalProjection,
    CapitalProjectionFinding,
    RiskFinding,
    RiskFindingEvidence,
    RiskScenario,
)
from app.schemas.capital import (
    CapitalComparisonBasisAttribute,
    CapitalComparisonBasisRead,
    CapitalComparisonDiagnosticRead,
    CapitalComparisonPeriodRead,
    CapitalComparisonRead,
    CapitalFindingRead,
    CapitalIndicatorRead,
    CapitalProjectionCreate,
    CapitalProjectionErrorRead,
    CapitalProjectionListRead,
    CapitalProjectionRead,
    CapitalProjectionSummaryRead,
    CapitalSummaryRead,
)
from app.schemas.findings import EvidenceRead, FindingRead
from app.services.audit import record_event
from app.services.cases import (
    ensure_case_is_not_archived,
    get_case_for_update_or_404,
    get_case_or_404,
)
from app.services.findings import list_finding_evidence

ENGINE_VERSION = "capital-projection-v1.0.0"
RATIO = Decimal("0.00000001")
MONEY = Decimal("0.0001")
RATIO_STORAGE = (RATIO, 12, 8)
MONEY_STORAGE = (MONEY, 20, 4)
HIGH_PRESSURE_EQUITY_RATIO = Decimal("0.10000000")
MEDIUM_PRESSURE_EQUITY_RATIO = Decimal("0.20000000")


class CapitalInputError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, object]) -> None:
        super().__init__(message)
        self.payload = {"code": code, "message": message, "details": details}


def create_projection(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    payload: CapitalProjectionCreate,
) -> CapitalProjectionRead:
    if ctx.actor_user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id required.")
    run = db.scalar(
        select(CalculationRun).where(
            CalculationRun.id == payload.calculation_run_id,
            CalculationRun.organization_id == ctx.organization_id,
            CalculationRun.case_id == case_id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calculation run not found."
        )
    if run.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Capital projection requires a successful calculation run.",
        )
    _lock_active_case_and_scenario(db, ctx, case_id, run.scenario_id)
    now = datetime.now(UTC)
    projection = CapitalProjection(
        organization_id=ctx.organization_id,
        case_id=case_id,
        scenario_id=run.scenario_id,
        calculation_run_id=run.id,
        status="running",
        engine_version=ENGINE_VERSION,
        input_hash=run.input_hash,
        reporting_currency=str(run.inputs["currency"]),
        started_at=now,
        created_by=ctx.actor_user_id,
    )
    db.add(projection)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="capital_projection.started",
        entity_type="capital_projection",
        entity_id=projection.id,
        details={"case_id": str(case_id), "calculation_run_id": str(run.id)},
    )
    try:
        periods = list(
            db.scalars(
                select(CalculationForecastPeriod)
                .where(
                    CalculationForecastPeriod.run_id == run.id,
                    CalculationForecastPeriod.organization_id == ctx.organization_id,
                    CalculationForecastPeriod.case_id == case_id,
                )
                .order_by(CalculationForecastPeriod.period_number)
            )
        )
        if not periods:
            raise CapitalInputError(
                "forecast_outputs_missing",
                "The calculation run has no forecast outputs.",
                {"calculation_run_id": str(run.id)},
            )
        invalid = [
            {"forecast_period_id": str(period.id), "period_number": period.period_number}
            for period in periods
            if period.total_assets <= 0
        ]
        if invalid:
            raise CapitalInputError(
                "non_positive_projected_assets",
                "Capital ratios require positive projected total assets.",
                {
                    "forecast_periods": invalid,
                    "corrective_action": (
                        "Correct the named forecast inputs and rerun the calculation."
                    ),
                },
            )
        opening_equity = _opening_equity(periods[0])
        indicators = [_indicator(projection, period, opening_equity) for period in periods]
        db.add_all(indicators)
        db.flush()
        _persist_findings(db, ctx, projection, run, indicators)
        projection.status = "succeeded"
        projection.completed_at = datetime.now(UTC)
        record_event(
            db,
            ctx,
            event_type="capital_projection.succeeded",
            entity_type="capital_projection",
            entity_id=projection.id,
            details={"input_hash": projection.input_hash, "indicator_count": len(indicators)},
        )
    except CapitalInputError as exc:
        projection.status = "failed"
        projection.error = exc.payload
        projection.completed_at = datetime.now(UTC)
        record_event(
            db,
            ctx,
            event_type="capital_projection.failed",
            entity_type="capital_projection",
            entity_id=projection.id,
            details=exc.payload,
        )
    db.commit()
    return get_projection(db, ctx, case_id, projection.id)


def get_projection(
    db: Session, ctx: TenantContext, case_id: UUID, projection_id: UUID
) -> CapitalProjectionRead:
    projection = db.scalar(
        select(CapitalProjection).where(
            CapitalProjection.id == projection_id,
            CapitalProjection.organization_id == ctx.organization_id,
            CapitalProjection.case_id == case_id,
        )
    )
    if projection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Capital projection not found."
        )
    return _read_projection(db, ctx, projection)


def list_projections(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    limit: int = 25,
    offset: int = 0,
) -> CapitalProjectionListRead:
    get_case_or_404(db, ctx.organization_id, case_id)
    conditions = (
        CapitalProjection.organization_id == ctx.organization_id,
        CapitalProjection.case_id == case_id,
    )
    total = db.scalar(select(func.count()).select_from(CapitalProjection).where(*conditions)) or 0
    projections = list(
        db.scalars(
            select(CapitalProjection)
            .where(*conditions)
            .order_by(CapitalProjection.created_at.desc(), CapitalProjection.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return CapitalProjectionListRead(
        case_id=case_id,
        projections=[
            CapitalProjectionSummaryRead.model_validate(projection, from_attributes=True)
            for projection in projections
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(projections) < total,
    )


def get_summary(
    db: Session, ctx: TenantContext, case_id: UUID, scenario_id: UUID | None
) -> CapitalSummaryRead:
    get_case_or_404(db, ctx.organization_id, case_id)
    conditions = [
        CapitalProjection.organization_id == ctx.organization_id,
        CapitalProjection.case_id == case_id,
        CapitalProjection.status == "succeeded",
    ]
    if scenario_id is not None:
        conditions.append(CapitalProjection.scenario_id == scenario_id)
    projection = db.scalar(
        select(CapitalProjection)
        .where(*conditions)
        .order_by(CapitalProjection.created_at.desc(), CapitalProjection.id.desc())
        .limit(1)
    )
    return CapitalSummaryRead(
        case_id=case_id,
        scenario_id=scenario_id,
        projection=_read_projection(db, ctx, projection) if projection else None,
    )


def get_comparison(db: Session, ctx: TenantContext, case_id: UUID) -> CapitalComparisonRead:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    ensure_case_is_not_archived(case)
    projections: dict[str, CapitalProjection | None] = {}
    for scenario_type in ("baseline", "downside"):
        projections[scenario_type] = db.scalar(
            select(CapitalProjection)
            .join(
                RiskScenario,
                (RiskScenario.id == CapitalProjection.scenario_id)
                & (RiskScenario.organization_id == ctx.organization_id),
            )
            .where(
                CapitalProjection.organization_id == ctx.organization_id,
                CapitalProjection.case_id == case_id,
                CapitalProjection.status == "succeeded",
                RiskScenario.scenario_type == scenario_type,
                RiskScenario.archived_at.is_(None),
            )
            .order_by(CapitalProjection.created_at.desc(), CapitalProjection.id.desc())
            .limit(1)
        )
    baseline = (
        _read_projection(db, ctx, projections["baseline"]) if projections["baseline"] else None
    )
    downside = (
        _read_projection(db, ctx, projections["downside"]) if projections["downside"] else None
    )
    periods: list[CapitalComparisonPeriodRead] = []
    diagnostic: CapitalComparisonDiagnosticRead | None = None
    if baseline and downside:
        runs = {
            run.id: run
            for run in db.scalars(
                select(CalculationRun).where(
                    CalculationRun.id.in_(
                        [baseline.calculation_run_id, downside.calculation_run_id]
                    ),
                    CalculationRun.organization_id == ctx.organization_id,
                    CalculationRun.case_id == case_id,
                )
            )
        }
        baseline_basis = _comparison_basis(
            runs[baseline.calculation_run_id], baseline.reporting_currency
        )
        downside_basis = _comparison_basis(
            runs[downside.calculation_run_id], downside.reporting_currency
        )
        differing_attributes: list[CapitalComparisonBasisAttribute] = []
        if baseline_basis.as_of_date != downside_basis.as_of_date:
            differing_attributes.append("as_of_date")
        if baseline_basis.reporting_currency != downside_basis.reporting_currency:
            differing_attributes.append("reporting_currency")
        if baseline_basis.forecast_horizon != downside_basis.forecast_horizon:
            differing_attributes.append("forecast_horizon")
        if differing_attributes:
            diagnostic = CapitalComparisonDiagnosticRead(
                code="comparison_basis_mismatch",
                message="Baseline and downside projections use incompatible forecast bases.",
                differing_attributes=differing_attributes,
                baseline_basis=baseline_basis,
                downside_basis=downside_basis,
                corrective_action=(
                    "Rerun the other scenario using the matching as-of date, reporting currency, "
                    "and forecast horizon, then generate a new capital projection."
                ),
            )
        else:
            downside_by_period = {item.period_number: item for item in downside.indicators}
            for base in baseline.indicators:
                down = downside_by_period[base.period_number]
                periods.append(
                    CapitalComparisonPeriodRead(
                        period_number=base.period_number,
                        baseline_equity=base.equity,
                        downside_equity=down.equity,
                        equity_delta=_money(down.equity - base.equity),
                        baseline_equity_to_assets_ratio=base.equity_to_assets_ratio,
                        downside_equity_to_assets_ratio=down.equity_to_assets_ratio,
                        equity_to_assets_ratio_delta=_ratio(
                            down.equity_to_assets_ratio - base.equity_to_assets_ratio
                        ),
                    )
                )
    return CapitalComparisonRead(
        case_id=case_id,
        baseline=baseline,
        downside=downside,
        periods=periods,
        diagnostic=diagnostic,
    )


def _comparison_basis(run: CalculationRun, reporting_currency: str) -> CapitalComparisonBasisRead:
    return CapitalComparisonBasisRead(
        as_of_date=run.as_of_date,
        reporting_currency=reporting_currency,
        forecast_horizon=run.forecast_periods,
    )


def _lock_active_case_and_scenario(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
) -> None:
    case = get_case_for_update_or_404(db, ctx.organization_id, case_id)
    ensure_case_is_not_archived(case)
    scenario = db.scalar(
        select(RiskScenario)
        .where(
            RiskScenario.id == scenario_id,
            RiskScenario.organization_id == ctx.organization_id,
            RiskScenario.case_id == case_id,
        )
        .with_for_update()
    )
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found.")
    if scenario.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Archived scenarios cannot be used for capital projections.",
        )


def _opening_equity(period: CalculationForecastPeriod) -> Decimal:
    try:
        opening_assets = Decimal(period.components["opening_assets"])
        opening_liabilities = Decimal(period.components["opening_liabilities"])
        opening_equity = opening_assets - opening_liabilities
        if not all(
            value.is_finite() for value in (opening_assets, opening_liabilities, opening_equity)
        ):
            raise InvalidOperation
        return opening_equity
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        raise CapitalInputError(
            "forecast_evidence_missing",
            "The forecast output is missing opening balance evidence.",
            {
                "forecast_period_id": str(period.id),
                "required_components": ["opening_assets", "opening_liabilities"],
            },
        ) from exc


def _indicator(
    projection: CapitalProjection,
    period: CalculationForecastPeriod,
    opening_equity: Decimal,
) -> CapitalIndicator:
    equity_ratio = period.total_equity / period.total_assets
    liabilities_ratio = period.total_liabilities / period.total_assets
    equity_change = period.total_equity - opening_equity
    evidence = {
        "calculation_run_id": str(projection.calculation_run_id),
        "forecast_period_id": str(period.id),
        "total_assets": str(period.total_assets),
        "total_liabilities": str(period.total_liabilities),
        "total_equity": str(period.total_equity),
        "opening_equity": str(opening_equity),
    }
    values = {
        "equity": _bounded_value(period.total_equity, MONEY_STORAGE, "equity", period),
        "equity_to_assets_ratio": _bounded_value(
            equity_ratio, RATIO_STORAGE, "equity_to_assets_ratio", period
        ),
        "liabilities_to_assets_ratio": _bounded_value(
            liabilities_ratio,
            RATIO_STORAGE,
            "liabilities_to_assets_ratio",
            period,
        ),
        "equity_change": _bounded_value(equity_change, MONEY_STORAGE, "equity_change", period),
    }
    pressure = _classify_persisted_pressure(
        values["equity"],
        values["equity_to_assets_ratio"],
        values["equity_change"],
    )
    return CapitalIndicator(
        organization_id=projection.organization_id,
        case_id=projection.case_id,
        projection_id=projection.id,
        forecast_period_id=period.id,
        period_number=period.period_number,
        **values,
        pressure_level=pressure,
        evidence=evidence,
    )


def _persist_findings(
    db: Session,
    ctx: TenantContext,
    projection: CapitalProjection,
    run: CalculationRun,
    indicators: list[CapitalIndicator],
) -> None:
    _supersede_prior_findings(db, ctx, projection)
    worst = min(indicators, key=lambda item: item.equity_to_assets_ratio)
    final = indicators[-1]
    candidates: list[tuple[str, str, str, str, CapitalIndicator]] = []
    if any(item.equity < 0 for item in indicators):
        item = min(indicators, key=lambda row: row.equity)
        candidates.append(
            (
                "capital_negative_equity",
                "Projected negative equity",
                "critical",
                f"Equity falls to {item.equity} in period {item.period_number}.",
                item,
            )
        )
    elif worst.equity_to_assets_ratio < HIGH_PRESSURE_EQUITY_RATIO:
        candidates.append(
            (
                "capital_thin_buffer",
                "Projected capital buffer is thin",
                "high",
                "The minimum equity-to-assets ratio is "
                f"{worst.equity_to_assets_ratio:.2%} in period {worst.period_number}.",
                worst,
            )
        )
    if final.equity_change < 0:
        candidates.append(
            (
                "capital_erosion",
                "Projected capital erosion",
                "medium",
                f"Equity declines by {abs(final.equity_change)} by period {final.period_number}.",
                final,
            )
        )
    for rule_id, title, severity, summary, indicator in candidates:
        details = {
            "capital_projection_id": str(projection.id),
            "calculation_run_id": str(run.id),
            "scenario_id": str(projection.scenario_id),
            "input_hash": run.input_hash,
            "indicator_id": str(indicator.id),
            "evidence": indicator.evidence,
        }
        finding = RiskFinding(
            organization_id=projection.organization_id,
            case_id=projection.case_id,
            risk_type="leverage_risk",
            title=title,
            summary=summary,
            rationale="Deterministic capital projection rule based on immutable forecast outputs.",
            severity=severity,
            status="needs_review",
            source="deterministic_rule",
            rule_id=rule_id,
            rule_version=ENGINE_VERSION,
            details=details,
        )
        db.add(finding)
        db.flush()
        db.add(
            CapitalProjectionFinding(
                organization_id=projection.organization_id,
                case_id=projection.case_id,
                projection_id=projection.id,
                finding_id=finding.id,
            )
        )
        db.add(
            RiskFindingEvidence(
                organization_id=projection.organization_id,
                finding_id=finding.id,
                quote=summary,
                locator={"source_type": "calculation_forecast_period", **details},
                relevance=Decimal("1"),
            )
        )
        record_event(
            db,
            ctx,
            event_type="capital_finding.generated",
            entity_type="risk_finding",
            entity_id=finding.id,
            details={"capital_projection_id": str(projection.id), "rule_id": rule_id},
        )


def _supersede_prior_findings(
    db: Session, ctx: TenantContext, projection: CapitalProjection
) -> None:
    prior_findings = list(
        db.scalars(
            select(RiskFinding)
            .join(
                CapitalProjectionFinding,
                CapitalProjectionFinding.finding_id == RiskFinding.id,
            )
            .join(
                CapitalProjection,
                CapitalProjection.id == CapitalProjectionFinding.projection_id,
            )
            .where(
                RiskFinding.organization_id == ctx.organization_id,
                RiskFinding.case_id == projection.case_id,
                RiskFinding.status == "needs_review",
                RiskFinding.disposition_reason.is_(None),
                CapitalProjectionFinding.organization_id == ctx.organization_id,
                CapitalProjection.organization_id == ctx.organization_id,
                CapitalProjection.scenario_id == projection.scenario_id,
                CapitalProjection.id != projection.id,
            )
            .with_for_update(of=RiskFinding)
        )
    )
    for finding in prior_findings:
        if finding.details.get("reviewed_by") or finding.details.get("reviewed_at"):
            continue
        finding.status = "superseded"
        finding.disposition_reason = f"Superseded by capital projection {projection.id}."
        finding.details = {
            **finding.details,
            "superseded_by_capital_projection_id": str(projection.id),
        }
        record_event(
            db,
            ctx,
            event_type="capital_finding.superseded",
            entity_type="risk_finding",
            entity_id=finding.id,
            details={"capital_projection_id": str(projection.id)},
        )


def _classify_persisted_pressure(
    equity: Decimal, equity_ratio: Decimal, equity_change: Decimal
) -> str:
    if equity < 0:
        return "critical"
    if equity_ratio < HIGH_PRESSURE_EQUITY_RATIO:
        return "high"
    if equity_ratio < MEDIUM_PRESSURE_EQUITY_RATIO or equity_change < 0:
        return "medium"
    return "low"


def _read_projection(
    db: Session, ctx: TenantContext, projection: CapitalProjection
) -> CapitalProjectionRead:
    indicators = list(
        db.scalars(
            select(CapitalIndicator)
            .where(
                CapitalIndicator.organization_id == ctx.organization_id,
                CapitalIndicator.projection_id == projection.id,
            )
            .order_by(CapitalIndicator.period_number)
        )
    )
    findings = list(
        db.scalars(
            select(RiskFinding)
            .join(CapitalProjectionFinding, CapitalProjectionFinding.finding_id == RiskFinding.id)
            .where(
                CapitalProjectionFinding.organization_id == ctx.organization_id,
                CapitalProjectionFinding.projection_id == projection.id,
            )
            .order_by(RiskFinding.created_at)
        )
    )
    return CapitalProjectionRead(
        id=projection.id,
        organization_id=projection.organization_id,
        case_id=projection.case_id,
        scenario_id=projection.scenario_id,
        calculation_run_id=projection.calculation_run_id,
        status=projection.status,  # type: ignore[arg-type]
        engine_version=projection.engine_version,
        input_hash=projection.input_hash,
        reporting_currency=projection.reporting_currency,
        started_at=projection.started_at,
        completed_at=projection.completed_at,
        error=(
            CapitalProjectionErrorRead.model_validate(projection.error)
            if projection.error
            else None
        ),
        indicators=[
            CapitalIndicatorRead(
                id=item.id,
                forecast_period_id=item.forecast_period_id,
                period_number=item.period_number,
                equity=item.equity,
                equity_to_assets_ratio=item.equity_to_assets_ratio,
                liabilities_to_assets_ratio=item.liabilities_to_assets_ratio,
                equity_change=item.equity_change,
                pressure_level=item.pressure_level,  # type: ignore[arg-type]
                evidence=item.evidence,
            )
            for item in indicators
        ],
        findings=[
            CapitalFindingRead(
                finding=FindingRead.model_validate(finding),
                evidence=[
                    EvidenceRead.model_validate(evidence, from_attributes=True)
                    for evidence in list_finding_evidence(db, ctx, finding.id)
                ],
            )
            for finding in findings
        ],
        created_by=projection.created_by,
        created_at=projection.created_at,
        updated_at=projection.updated_at,
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _ratio(value: Decimal) -> Decimal:
    return value.quantize(RATIO, rounding=ROUND_HALF_UP)


def _bounded_value(
    value: Decimal,
    storage: tuple[Decimal, int, int],
    field: str,
    period: CalculationForecastPeriod,
) -> Decimal:
    quantum, precision, scale = storage
    limit = Decimal(10) ** (precision - scale)
    try:
        if not value.is_finite() or abs(value) >= limit:
            raise InvalidOperation
        result = value.quantize(quantum, rounding=ROUND_HALF_UP)
        if abs(result) >= limit:
            raise InvalidOperation
        return result
    except InvalidOperation as exc:
        raise CapitalInputError(
            "capital_indicator_out_of_range",
            "A derived capital indicator exceeds its supported numeric range.",
            {
                "forecast_period_id": str(period.id),
                "period_number": period.period_number,
                "field": field,
                "value": str(value),
                "precision": precision,
                "scale": scale,
            },
        ) from exc
