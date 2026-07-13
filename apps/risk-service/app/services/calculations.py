from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    CalculationForecastPeriod,
    CalculationRun,
    FinancialBalance,
    FinancialCashFlow,
    FinancialObligation,
    RiskScenario,
    ScenarioAssumption,
)
from app.schemas.calculations import (
    CalculationErrorRead,
    CalculationRerunCreate,
    CalculationRunCreate,
    CalculationRunListRead,
    CalculationRunRead,
    ForecastPeriodRead,
)
from app.services.audit import record_event
from app.services.cases import get_case_or_404

ENGINE_VERSION = "balance-sheet-v1.0.0"
INPUT_SCHEMA_VERSION = "calculation-input-v1"
OUTPUT_SCHEMA_VERSION = "balance-sheet-output-v1"
MONEY = Decimal("0.0001")
MAX_STORED_MONEY = Decimal("9999999999999999.9999")
LIABILITY_TYPES = {"liability", "liabilities", "debt", "payable", "payables", "loan"}
REQUIRED_ASSUMPTIONS = {
    "revenue_growth_rate",
    "expense_growth_rate",
    "cash_flow_delay_days",
    "credit_usage_rate",
    "repayment_rate",
}


class CalculationInputError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class ForecastValue:
    period_number: int
    period_end: date
    currency: str
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    cash: Decimal
    projected_inflows: Decimal
    projected_outflows: Decimal
    credit_draw: Decimal
    debt_repayment: Decimal
    components: dict[str, Any]


def start_run(
    db: Session, ctx: TenantContext, case_id: UUID, payload: CalculationRunCreate
) -> CalculationRunRead:
    _require_actor(ctx)
    get_case_or_404(db, ctx.organization_id, case_id)
    return _create_and_execute(
        db,
        ctx,
        case_id,
        payload.scenario_id,
        payload.forecast_periods,
        payload.as_of_date,
    )


def rerun(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    run_id: UUID,
    payload: CalculationRerunCreate,
) -> CalculationRunRead:
    _require_actor(ctx)
    prior = _run_or_404(db, ctx, case_id, run_id)
    return _create_and_execute(
        db,
        ctx,
        case_id,
        prior.scenario_id,
        payload.forecast_periods or prior.forecast_periods,
        payload.as_of_date,
        rerun_of_run_id=prior.id,
    )


def list_runs(
    db: Session, ctx: TenantContext, case_id: UUID, *, scenario_id: UUID | None = None
) -> CalculationRunListRead:
    get_case_or_404(db, ctx.organization_id, case_id)
    stmt = select(CalculationRun).where(
        CalculationRun.organization_id == ctx.organization_id,
        CalculationRun.case_id == case_id,
    )
    if scenario_id is not None:
        stmt = stmt.where(CalculationRun.scenario_id == scenario_id)
    rows = list(
        db.scalars(stmt.order_by(CalculationRun.created_at.desc(), CalculationRun.id.desc()))
    )
    latest = next((row.id for row in rows if row.status == "succeeded"), None)
    return CalculationRunListRead(
        case_id=case_id,
        runs=[_read_run(db, row) for row in rows],
        latest_successful_run_id=latest,
    )


def get_run(db: Session, ctx: TenantContext, case_id: UUID, run_id: UUID) -> CalculationRunRead:
    return _read_run(db, _run_or_404(db, ctx, case_id, run_id))


def _create_and_execute(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    forecast_periods: int,
    requested_as_of_date: date | None,
    *,
    rerun_of_run_id: UUID | None = None,
) -> CalculationRunRead:
    now = datetime.now(UTC)
    try:
        snapshot, as_of_date = build_input_snapshot(
            db,
            ctx,
            case_id,
            scenario_id,
            forecast_periods,
            requested_as_of_date,
        )
        input_hash = _snapshot_hash(snapshot)
        failure: CalculationInputError | None = None
    except CalculationInputError as exc:
        as_of_date = requested_as_of_date or now.date()
        snapshot = {
            "schema_version": INPUT_SCHEMA_VERSION,
            "case_id": str(case_id),
            "scenario_id": str(scenario_id),
            "forecast_periods": forecast_periods,
            "as_of_date": as_of_date.isoformat(),
            "validation_error": {"code": exc.code, "details": exc.details},
        }
        input_hash = _snapshot_hash(snapshot)
        failure = exc

    run = CalculationRun(
        organization_id=ctx.organization_id,
        case_id=case_id,
        scenario_id=scenario_id,
        rerun_of_run_id=rerun_of_run_id,
        status="queued",
        engine_version=ENGINE_VERSION,
        input_schema_version=INPUT_SCHEMA_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        input_hash=input_hash,
        inputs=snapshot,
        forecast_periods=forecast_periods,
        as_of_date=as_of_date,
        created_by=ctx.actor_user_id,
    )
    db.add(run)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="calculation_run.started",
        entity_type="calculation_run",
        entity_id=run.id,
        details={
            "case_id": str(case_id),
            "scenario_id": str(scenario_id),
            "input_hash": input_hash,
            "engine_version": ENGINE_VERSION,
            "rerun_of_run_id": str(rerun_of_run_id) if rerun_of_run_id else None,
        },
    )
    run.status = "running"
    run.started_at = now

    if failure is not None:
        _mark_failed(run, failure)
    else:
        try:
            for value in calculate_forecast(snapshot):
                db.add(
                    CalculationForecastPeriod(
                        organization_id=ctx.organization_id,
                        case_id=case_id,
                        run_id=run.id,
                        **value.__dict__,
                    )
                )
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
        except CalculationInputError as exc:
            _mark_failed(run, exc)
        except Exception as exc:  # persisted failure boundary for debuggable run lifecycle
            _mark_failed(
                run,
                CalculationInputError(
                    "calculation_error",
                    "The balance-sheet forecast could not be calculated.",
                    {"exception_type": type(exc).__name__, "reason": str(exc)},
                ),
            )

    event_type = (
        "calculation_run.succeeded" if run.status == "succeeded" else "calculation_run.failed"
    )
    record_event(
        db,
        ctx,
        event_type=event_type,
        entity_type="calculation_run",
        entity_id=run.id,
        details={
            "input_hash": run.input_hash,
            "output_periods": forecast_periods if run.status == "succeeded" else 0,
            "error_code": run.error_code,
        },
    )
    db.commit()
    return _read_run(db, run)


def build_input_snapshot(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    forecast_periods: int,
    requested_as_of_date: date | None,
) -> tuple[dict[str, Any], date]:
    scenario = db.scalar(
        select(RiskScenario).where(
            RiskScenario.id == scenario_id,
            RiskScenario.organization_id == ctx.organization_id,
            RiskScenario.case_id == case_id,
            RiskScenario.archived_at.is_(None),
        )
    )
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found.")

    assumptions = list(
        db.scalars(
            select(ScenarioAssumption)
            .where(
                ScenarioAssumption.organization_id == ctx.organization_id,
                ScenarioAssumption.case_id == case_id,
                ScenarioAssumption.scenario_id == scenario_id,
            )
            .order_by(ScenarioAssumption.key, ScenarioAssumption.id)
        )
    )
    by_key = {item.key: item for item in assumptions}
    missing = sorted(REQUIRED_ASSUMPTIONS - by_key.keys())
    unreviewed = sorted(
        key
        for key in REQUIRED_ASSUMPTIONS
        if key in by_key and by_key[key].review_status != "reviewed"
    )
    if missing or unreviewed:
        raise CalculationInputError(
            "scenario_not_ready",
            "The scenario must contain reviewed values for every required forecast assumption.",
            {
                "missing_assumptions": missing,
                "unreviewed_assumptions": unreviewed,
                "assumptions": [
                    {
                        "id": str(item.id),
                        "key": item.key,
                        "value": item.value,
                        "review_status": item.review_status,
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in assumptions
                ],
            },
        )

    balances = list(
        db.scalars(
            select(FinancialBalance)
            .where(
                FinancialBalance.organization_id == ctx.organization_id,
                FinancialBalance.case_id == case_id,
            )
            .order_by(FinancialBalance.id)
        )
    )
    if not balances:
        raise CalculationInputError(
            "financial_data_missing",
            "At least one canonical financial balance is required to run the forecast.",
        )
    cash_flows = list(
        db.scalars(
            select(FinancialCashFlow)
            .where(
                FinancialCashFlow.organization_id == ctx.organization_id,
                FinancialCashFlow.case_id == case_id,
            )
            .order_by(FinancialCashFlow.id)
        )
    )
    obligations = list(
        db.scalars(
            select(FinancialObligation)
            .where(
                FinancialObligation.organization_id == ctx.organization_id,
                FinancialObligation.case_id == case_id,
            )
            .order_by(FinancialObligation.id)
        )
    )
    derived_as_of = max((item.as_of_date for item in balances if item.as_of_date), default=None)
    as_of_date = requested_as_of_date or derived_as_of or scenario.created_at.date()
    financial_inputs = [
        *(("balance", item) for item in balances),
        *(("cash_flow", item) for item in cash_flows),
        *(("obligation", item) for item in obligations),
    ]
    missing_currency_inputs = [
        {"type": input_type, "id": str(item.id)}
        for input_type, item in financial_inputs
        if not item.currency
    ]
    if missing_currency_inputs:
        raise CalculationInputError(
            "missing_currency",
            "Every financial input must have a reporting currency.",
            {"inputs": missing_currency_inputs},
        )
    currencies = sorted({item.currency for _, item in financial_inputs})
    if len(currencies) > 1:
        raise CalculationInputError(
            "multiple_currencies",
            "The first forecast supports one reporting currency per case.",
            {"currencies": currencies},
        )
    currency = currencies[0]
    numeric_assumptions = {
        key: str(_decimal_assumption(by_key[key].value, key)) for key in REQUIRED_ASSUMPTIONS
    }

    snapshot: dict[str, Any] = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "case_id": str(case_id),
        "scenario": {
            "id": str(scenario.id),
            "name": scenario.name,
            "type": scenario.scenario_type,
            "assumptions": [
                {
                    "id": str(item.id),
                    "key": item.key,
                    "value": item.value,
                    "unit": item.unit,
                    "updated_at": item.updated_at.isoformat(),
                    "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
                }
                for item in assumptions
            ],
            "numeric_assumptions": numeric_assumptions,
        },
        "forecast_periods": forecast_periods,
        "as_of_date": as_of_date.isoformat(),
        "currency": currency,
        "balances": [
            {
                "id": str(item.id),
                "balance_type": item.balance_type,
                "amount": str(item.amount),
                "currency": item.currency,
                "as_of_date": item.as_of_date.isoformat() if item.as_of_date else None,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in balances
        ],
        "cash_flows": [
            {
                "id": str(item.id),
                "direction": item.direction,
                "amount": str(item.amount),
                "category": item.category,
                "currency": item.currency,
                "cash_flow_date": item.cash_flow_date.isoformat() if item.cash_flow_date else None,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in cash_flows
        ],
        "obligations": [
            {
                "id": str(item.id),
                "principal_amount": str(item.principal_amount or 0),
                "outstanding_amount": str(item.outstanding_amount or 0),
                "currency": item.currency,
                "status": item.status,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in obligations
        ],
    }
    return snapshot, as_of_date


def calculate_forecast(snapshot: dict[str, Any]) -> list[ForecastValue]:
    assumptions = snapshot["scenario"]["numeric_assumptions"]
    revenue_growth = Decimal(assumptions["revenue_growth_rate"])
    expense_growth = Decimal(assumptions["expense_growth_rate"])
    delay_days = Decimal(assumptions["cash_flow_delay_days"])
    credit_usage = Decimal(assumptions["credit_usage_rate"])
    repayment_rate = Decimal(assumptions["repayment_rate"])
    if delay_days < 0 or delay_days > 365:
        raise CalculationInputError(
            "invalid_assumption", "Cash-flow delay must be between 0 and 365 days."
        )
    if credit_usage < 0 or credit_usage > 1 or repayment_rate < 0 or repayment_rate > 1:
        raise CalculationInputError(
            "invalid_assumption", "Credit usage and repayment rates must be between 0 and 1."
        )

    asset_balances = [
        Decimal(item["amount"])
        for item in snapshot["balances"]
        if item["balance_type"].strip().lower() not in LIABILITY_TYPES
    ]
    liability_balances = [
        Decimal(item["amount"])
        for item in snapshot["balances"]
        if item["balance_type"].strip().lower() in LIABILITY_TYPES
    ]
    cash_balances = [
        Decimal(item["amount"])
        for item in snapshot["balances"]
        if item["balance_type"].strip().lower() in {"cash", "cash_and_equivalents"}
    ]
    base_assets = sum(asset_balances, Decimal(0))
    base_cash = sum(cash_balances, Decimal(0))
    non_cash_assets = base_assets - base_cash
    obligation_outstanding = sum(
        (Decimal(item["outstanding_amount"]) for item in snapshot["obligations"]), Decimal(0)
    )
    base_liabilities = sum(liability_balances, Decimal(0)) + obligation_outstanding
    total_principal = sum(
        (Decimal(item["principal_amount"]) for item in snapshot["obligations"]), Decimal(0)
    )
    available_credit = max(total_principal - obligation_outstanding, Decimal(0))
    desired_draw = max(total_principal * credit_usage - obligation_outstanding, Decimal(0))
    initial_draw = min(available_credit, desired_draw)
    base_inflows = sum(
        (
            Decimal(item["amount"])
            for item in snapshot["cash_flows"]
            if item["direction"] == "inflow"
        ),
        Decimal(0),
    )
    base_outflows = sum(
        (
            Decimal(item["amount"])
            for item in snapshot["cash_flows"]
            if item["direction"] == "outflow"
        ),
        Decimal(0),
    )
    periods = int(snapshot["forecast_periods"])
    scheduled_repayment = obligation_outstanding * repayment_rate / Decimal(periods)
    delay_factor = (Decimal(365) - delay_days) / Decimal(365)
    as_of_date = date.fromisoformat(snapshot["as_of_date"])
    cash = base_cash
    liabilities = base_liabilities
    results: list[ForecastValue] = []
    for number in range(1, periods + 1):
        inflows = base_inflows * ((Decimal(1) + revenue_growth) ** number) * delay_factor
        outflows = base_outflows * ((Decimal(1) + expense_growth) ** number)
        draw = initial_draw if number == 1 else Decimal(0)
        repayment = min(scheduled_repayment, max(liabilities + draw, Decimal(0)))
        cash = cash + inflows - outflows + draw - repayment
        liabilities = liabilities + draw - repayment
        assets = non_cash_assets + cash
        equity = assets - liabilities
        results.append(
            ForecastValue(
                period_number=number,
                period_end=_add_months(as_of_date, number * 12),
                currency=snapshot["currency"],
                total_assets=_money(assets),
                total_liabilities=_money(liabilities),
                total_equity=_money(equity),
                cash=_money(cash),
                projected_inflows=_money(inflows),
                projected_outflows=_money(outflows),
                credit_draw=_money(draw),
                debt_repayment=_money(repayment),
                components={
                    "non_cash_assets": str(_money(non_cash_assets)),
                    "opening_assets": str(_money(base_assets)),
                    "opening_liabilities": str(_money(base_liabilities)),
                },
            )
        )
    return results


def _decimal_assumption(value: Any, key: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise CalculationInputError(
            "invalid_assumption", f"{key} must be a numeric value.", {"assumption": key}
        )
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CalculationInputError(
            "invalid_assumption", f"{key} must be a numeric value.", {"assumption": key}
        ) from exc


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise CalculationInputError(
            "calculation_output_out_of_range",
            "A forecast value exceeds the supported monetary range.",
            {"maximum_absolute_value": str(MAX_STORED_MONEY)},
        )
    try:
        rounded = value.quantize(MONEY, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise CalculationInputError(
            "calculation_output_out_of_range",
            "A forecast value exceeds the supported monetary range.",
            {"maximum_absolute_value": str(MAX_STORED_MONEY)},
        ) from exc
    if abs(rounded) > MAX_STORED_MONEY:
        raise CalculationInputError(
            "calculation_output_out_of_range",
            "A forecast value exceeds the supported monetary range.",
            {"maximum_absolute_value": str(MAX_STORED_MONEY)},
        )
    return rounded


def _add_months(value: date, months: int) -> date:
    index = value.month - 1 + months
    year = value.year + index // 12
    month = index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _mark_failed(run: CalculationRun, error: CalculationInputError) -> None:
    run.status = "failed"
    run.completed_at = datetime.now(UTC)
    run.error_code = error.code
    run.error_message = error.message
    run.error_details = error.details


def _read_run(db: Session, run: CalculationRun) -> CalculationRunRead:
    outputs = list(
        db.scalars(
            select(CalculationForecastPeriod)
            .where(
                CalculationForecastPeriod.run_id == run.id,
                CalculationForecastPeriod.organization_id == run.organization_id,
                CalculationForecastPeriod.case_id == run.case_id,
            )
            .order_by(CalculationForecastPeriod.period_number)
        )
    )
    error = None
    if run.error_code and run.error_message:
        error = CalculationErrorRead(
            code=run.error_code, message=run.error_message, details=run.error_details
        )
    return CalculationRunRead(
        id=run.id,
        organization_id=run.organization_id,
        case_id=run.case_id,
        scenario_id=run.scenario_id,
        rerun_of_run_id=run.rerun_of_run_id,
        status=run.status,  # type: ignore[arg-type]
        engine_version=run.engine_version,
        input_schema_version=run.input_schema_version,
        output_schema_version=run.output_schema_version,
        input_hash=run.input_hash,
        inputs=run.inputs,
        forecast_periods=run.forecast_periods,
        as_of_date=run.as_of_date,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error=error,
        outputs=[ForecastPeriodRead.model_validate(item) for item in outputs],
        created_by=run.created_by,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _run_or_404(db: Session, ctx: TenantContext, case_id: UUID, run_id: UUID) -> CalculationRun:
    row = db.scalar(
        select(CalculationRun).where(
            CalculationRun.id == run_id,
            CalculationRun.organization_id == ctx.organization_id,
            CalculationRun.case_id == case_id,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calculation run not found."
        )
    return row


def _require_actor(ctx: TenantContext) -> None:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header is required."
        )
