from __future__ import annotations

import calendar
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    CalculationForecastPeriod,
    CalculationRun,
    FinancialBalance,
    FinancialCashFlow,
    FinancialObligation,
    FinancialReportingPeriod,
    RiskScenario,
    ScenarioAssumption,
)
from app.schemas.calculations import (
    CalculationErrorRead,
    CalculationRerunCreate,
    CalculationRunCreate,
    CalculationRunListRead,
    CalculationRunRead,
    CalculationRunSummaryRead,
    ForecastPeriodRead,
)
from app.services.audit import record_event
from app.services.cases import get_case_or_404
from app.services.liquidity import calculate_metrics as calculate_liquidity_metrics
from app.services.liquidity import generate_findings as generate_liquidity_findings
from app.services.liquidity import serialize_finding_publication
from app.services.scenario_semantics import resolve_engine_assumptions

ENGINE_VERSION = "balance-sheet-v1.0.0"
INPUT_SCHEMA_VERSION = "calculation-input-v1"
OUTPUT_SCHEMA_VERSION = "balance-sheet-output-v1"
MONEY = Decimal("0.0001")
MAX_STORED_MONEY = Decimal("9999999999999999.9999")
ASSET_TYPES = {
    "asset",
    "assets",
    "accounts_receivable",
    "cash",
    "cash_and_equivalents",
    "equipment",
    "goodwill",
    "intangible_assets",
    "inventory",
    "investment",
    "investments",
    "prepaid_expenses",
    "property",
    "property_plant_and_equipment",
    "receivable",
    "receivables",
}
LIABILITY_TYPES = {
    "accounts_payable",
    "accrued_liabilities",
    "debt",
    "lease_liability",
    "liabilities",
    "liability",
    "loan",
    "long_term_debt",
    "notes_payable",
    "payable",
    "payables",
    "short_term_debt",
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


def list_runs(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    scenario_id: UUID | None = None,
    active_scenarios_only: bool = False,
    limit: int = 25,
    offset: int = 0,
) -> CalculationRunListRead:
    get_case_or_404(db, ctx.organization_id, case_id)
    conditions = (
        CalculationRun.organization_id == ctx.organization_id,
        CalculationRun.case_id == case_id,
    )
    if scenario_id is not None:
        conditions += (CalculationRun.scenario_id == scenario_id,)
    if active_scenarios_only:
        active_scenario_ids = select(RiskScenario.id).where(
            RiskScenario.organization_id == ctx.organization_id,
            RiskScenario.case_id == case_id,
            RiskScenario.archived_at.is_(None),
        )
        conditions += (CalculationRun.scenario_id.in_(active_scenario_ids),)
    total = db.scalar(select(func.count()).select_from(CalculationRun).where(*conditions)) or 0
    latest = db.scalar(
        select(CalculationRun.id)
        .where(*conditions)
        .where(CalculationRun.status == "succeeded")
        .order_by(CalculationRun.created_at.desc(), CalculationRun.id.desc())
        .limit(1)
    )
    summary_columns = (
        CalculationRun.id,
        CalculationRun.scenario_id,
        CalculationRun.rerun_of_run_id,
        CalculationRun.status,
        CalculationRun.engine_version,
        CalculationRun.input_hash,
        CalculationRun.forecast_periods,
        CalculationRun.as_of_date,
        CalculationRun.started_at,
        CalculationRun.completed_at,
        CalculationRun.error_code,
        CalculationRun.error_message,
        CalculationRun.error_details,
        CalculationRun.created_at,
    )
    rows = list(
        db.execute(
            select(*summary_columns)
            .where(*conditions)
            .order_by(CalculationRun.created_at.desc(), CalculationRun.id.desc())
            .limit(limit)
            .offset(offset)
        ).mappings()
    )
    latest_by_scenario: list[CalculationRunSummaryRead] = []
    if active_scenarios_only:
        ranked = (
            select(
                *summary_columns,
                func.row_number()
                .over(
                    partition_by=CalculationRun.scenario_id,
                    order_by=(CalculationRun.created_at.desc(), CalculationRun.id.desc()),
                )
                .label("rank"),
            )
            .where(*conditions)
            .where(CalculationRun.status == "succeeded")
            .subquery()
        )
        latest_rows = db.execute(
            select(*(ranked.c[column.key] for column in summary_columns))
            .where(ranked.c.rank == 1)
            .order_by(ranked.c.created_at.desc(), ranked.c.id.desc())
            .limit(limit)
            .offset(offset)
        ).mappings()
        latest_by_scenario = [_read_summary(row) for row in latest_rows]
    return CalculationRunListRead(
        case_id=case_id,
        runs=[_read_summary(row) for row in rows],
        latest_successful_run_id=latest,
        latest_successful_runs_by_scenario=latest_by_scenario,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
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
    _scenario_or_404(db, ctx, case_id, scenario_id)
    now = datetime.now(UTC)
    as_of_date = requested_as_of_date or now.date()
    snapshot = _pending_snapshot(case_id, scenario_id, forecast_periods, as_of_date)
    input_hash = _snapshot_hash(snapshot)

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
            "input_hash_status": "pending",
            "engine_version": ENGINE_VERSION,
            "rerun_of_run_id": str(rerun_of_run_id) if rerun_of_run_id else None,
        },
    )
    db.commit()

    run.status = "running"
    run.started_at = now
    db.commit()

    run_id = run.id
    assembled_snapshot: dict[str, Any] | None = None
    with serialize_finding_publication(db, ctx, case_id, scenario_id) as publication_db:
        try:
            _begin_repeatable_read(publication_db)
            run = _run_or_404(publication_db, ctx, case_id, run_id)
            snapshot, as_of_date = build_input_snapshot(
                publication_db,
                ctx,
                case_id,
                scenario_id,
                forecast_periods,
                as_of_date,
            )
            assembled_snapshot = snapshot
            run.inputs = snapshot
            run.input_hash = _snapshot_hash(snapshot)
            run.as_of_date = as_of_date
            record_event(
                publication_db,
                ctx,
                event_type="calculation_run.input_snapshot_established",
                entity_type="calculation_run",
                entity_id=run.id,
                details={
                    "input_hash": run.input_hash,
                    "input_hash_status": "established",
                    "as_of_date": as_of_date.isoformat(),
                    "input_schema_version": INPUT_SCHEMA_VERSION,
                },
            )
            forecast_rows: list[CalculationForecastPeriod] = []
            for value in calculate_forecast(snapshot):
                forecast_row = CalculationForecastPeriod(
                    organization_id=ctx.organization_id,
                    case_id=case_id,
                    run_id=run.id,
                    **value.__dict__,
                )
                publication_db.add(forecast_row)
                forecast_rows.append(forecast_row)
            publication_db.flush()
            try:
                calculate_liquidity_metrics(forecast_rows)
            except ValueError as exc:
                raise CalculationInputError(
                    "liquidity_output_invalid",
                    "Liquidity metrics could not be calculated from the forecast outputs.",
                    {
                        "forecast_outputs": [
                            {
                                "id": str(item.id),
                                "period_number": item.period_number,
                                "currency": item.currency,
                            }
                            for item in forecast_rows
                        ],
                        "diagnostic": str(exc),
                        "corrective_action": (
                            "Review the named forecast outputs and rerun the calculation."
                        ),
                    },
                ) from exc
            generate_liquidity_findings(
                publication_db,
                ctx,
                run,
                forecast_rows,
                publication_locked=True,
            )
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            record_event(
                publication_db,
                ctx,
                event_type="calculation_run.succeeded",
                entity_type="calculation_run",
                entity_id=run.id,
                details={"input_hash": run.input_hash, "output_periods": forecast_periods},
            )
            publication_db.commit()
        except CalculationInputError as exc:
            _persist_failure(
                publication_db,
                ctx,
                case_id,
                run_id,
                exc,
                assembled_snapshot,
            )
        except Exception:
            error = CalculationInputError(
                "calculation_error",
                "The balance-sheet forecast could not be calculated.",
                {
                    "corrective_action": (
                        "Review the run inputs and retry. Contact support if it fails again."
                    )
                },
            )
            _persist_failure(
                publication_db,
                ctx,
                case_id,
                run_id,
                error,
                assembled_snapshot,
            )
    db.expire_all()
    return get_run(db, ctx, case_id, run_id)


def build_input_snapshot(  # noqa: PLR0913, PLR0915
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    scenario_id: UUID,
    forecast_periods: int,
    as_of_date: date,
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
    engine_assumptions, missing_categories, ambiguous_categories = resolve_engine_assumptions(
        assumptions
    )
    unreviewed = sorted(item.key for item in assumptions if item.review_status != "reviewed")
    missing_values = [
        {"id": str(item.id), "key": item.key} for item in assumptions if item.value is None
    ]
    if missing_categories or ambiguous_categories or unreviewed or missing_values:
        raise CalculationInputError(
            "scenario_not_ready",
            "The scenario needs one reviewed assumption for every forecast category.",
            {
                "missing_categories": missing_categories,
                "ambiguous_categories": ambiguous_categories,
                "unreviewed_assumptions": unreviewed,
                "missing_values": missing_values,
                "corrective_action": (
                    "Add or review the listed scenario assumptions, then run the forecast again."
                ),
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

    reporting_periods = list(
        db.scalars(
            select(FinancialReportingPeriod)
            .where(
                FinancialReportingPeriod.organization_id == ctx.organization_id,
                FinancialReportingPeriod.case_id == case_id,
            )
            .order_by(FinancialReportingPeriod.id)
        )
    )
    periods_by_id = {item.id: item for item in reporting_periods}
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
            {"corrective_action": "Add a dated canonical balance and run the forecast again."},
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
    all_obligations = list(
        db.scalars(
            select(FinancialObligation)
            .where(
                FinancialObligation.organization_id == ctx.organization_id,
                FinancialObligation.case_id == case_id,
            )
            .order_by(FinancialObligation.id)
        )
    )
    balance_dates = {
        item.id: _record_effective_date(
            item.as_of_date,
            periods_by_id.get(item.reporting_period_id) if item.reporting_period_id else None,
        )
        for item in balances
    }
    undated_balances = [
        {"id": str(item.id), "balance_type": item.balance_type}
        for item in balances
        if balance_dates[item.id] is None
    ]
    if undated_balances:
        raise CalculationInputError(
            "financial_period_missing",
            "Every balance must have an as-of date or a dated reporting period.",
            {
                "balances": undated_balances,
                "corrective_action": (
                    "Set an as-of date or assign a dated reporting period for each listed balance."
                ),
            },
        )
    eligible_balance_dates = [
        value for value in balance_dates.values() if value is not None and value <= as_of_date
    ]
    if not eligible_balance_dates:
        raise CalculationInputError(
            "financial_period_missing",
            "No balance reporting period exists on or before the requested as-of date.",
            {
                "as_of_date": as_of_date.isoformat(),
                "corrective_action": "Choose a later as-of date or add balances for this period.",
            },
        )
    effective_balance_date = max(eligible_balance_dates)
    balances = [item for item in balances if balance_dates[item.id] == effective_balance_date]
    selected_period_ids = {
        item.reporting_period_id for item in balances if item.reporting_period_id
    }
    if len(selected_period_ids) > 1:
        raise CalculationInputError(
            "financial_period_ambiguous",
            "Balances resolve to multiple reporting periods for the same effective date.",
            {
                "reporting_period_ids": sorted(str(value) for value in selected_period_ids),
                "effective_date": effective_balance_date.isoformat(),
                "corrective_action": (
                    "Assign the selected balances to one reporting period and run the "
                    "forecast again."
                ),
            },
        )
    selected_period_id = next(iter(selected_period_ids)) if len(selected_period_ids) == 1 else None

    selected_period = periods_by_id.get(selected_period_id) if selected_period_id else None
    selected_period_start = selected_period.start_date if selected_period else None
    selected_period_end = (
        selected_period.end_date or selected_period.as_of_date if selected_period else None
    )
    cash_flows_outside_period = [
        {
            "id": str(item.id),
            "category": item.category,
            "cash_flow_date": item.cash_flow_date.isoformat(),
            "reporting_period_id": str(selected_period_id),
            "period_start_date": (
                selected_period_start.isoformat() if selected_period_start else None
            ),
            "period_end_date": (selected_period_end.isoformat() if selected_period_end else None),
        }
        for item in cash_flows
        if selected_period is not None
        and item.reporting_period_id == selected_period_id
        and item.cash_flow_date is not None
        and not _date_in_period(item.cash_flow_date, selected_period)
    ]
    if cash_flows_outside_period:
        raise CalculationInputError(
            "cash_flow_date_outside_reporting_period",
            "Cash-flow dates must fall within their linked reporting period.",
            {
                "cash_flows": cash_flows_outside_period,
                "corrective_action": (
                    "Correct each listed cash-flow date or reporting period in the review "
                    "workspace, then run the forecast again."
                ),
            },
        )

    undated_cash_flows = [
        {"id": str(item.id), "category": item.category}
        for item in cash_flows
        if _record_effective_date(
            item.cash_flow_date,
            periods_by_id.get(item.reporting_period_id) if item.reporting_period_id else None,
        )
        is None
    ]
    if undated_cash_flows:
        raise CalculationInputError(
            "financial_period_missing",
            "Every cash flow must have a date or a dated reporting period.",
            {
                "cash_flows": undated_cash_flows,
                "corrective_action": (
                    "Set a cash-flow date or assign a dated reporting period for each listed "
                    "record."
                ),
            },
        )
    cash_flows = _select_period_records(
        cash_flows,
        periods_by_id,
        as_of_date,
        selected_period_id,
        lambda item: item.cash_flow_date,
    )
    active_obligations = [
        item
        for item in all_obligations
        if item.status == "active" and (item.start_date is None or item.start_date <= as_of_date)
    ]
    obligations = _select_period_records(
        active_obligations,
        periods_by_id,
        as_of_date,
        selected_period_id,
        lambda _item: None,
    )
    incomplete_obligations = [
        {
            "id": str(item.id),
            "dedupe_key": item.dedupe_key,
            "obligation_type": item.obligation_type,
            "missing_fields": [
                field
                for field, value in (
                    ("principal_amount", item.principal_amount),
                    ("outstanding_amount", item.outstanding_amount),
                )
                if value is None
            ],
        }
        for item in obligations
        if item.principal_amount is None or item.outstanding_amount is None
    ]
    if incomplete_obligations:
        raise CalculationInputError(
            "active_obligation_amounts_missing",
            "Active obligations require principal and outstanding amounts.",
            {
                "obligations": incomplete_obligations,
                "corrective_action": (
                    "Enter every missing principal and outstanding amount, or mark an obligation "
                    "inactive if it should not participate."
                ),
            },
        )
    unknown_balances = [
        {"id": str(item.id), "balance_type": item.balance_type}
        for item in balances
        if _normalized_balance_type(item.balance_type) not in ASSET_TYPES | LIABILITY_TYPES
    ]
    if unknown_balances:
        raise CalculationInputError(
            "unknown_balance_type",
            "Some balances cannot be classified as assets or liabilities.",
            {
                "balances": unknown_balances,
                "corrective_action": "Change each listed balance to a supported canonical type.",
                "supported_asset_types": sorted(ASSET_TYPES),
                "supported_liability_types": sorted(LIABILITY_TYPES),
            },
        )
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
            {
                "inputs": missing_currency_inputs,
                "corrective_action": "Set a currency on every listed financial record.",
            },
        )
    currencies = sorted({item.currency for _, item in financial_inputs})
    if len(currencies) > 1:
        raise CalculationInputError(
            "multiple_currencies",
            "The first forecast supports one reporting currency per case.",
            {
                "currencies": currencies,
                "inputs": [
                    {"type": input_type, "id": str(item.id), "currency": item.currency}
                    for input_type, item in financial_inputs
                ],
                "corrective_action": (
                    "Convert the selected-period inputs to one reporting currency."
                ),
            },
        )
    currency = currencies[0]
    numeric_assumptions = {
        key: _decimal_text(_decimal_assumption(item.value, key))
        for key, item in engine_assumptions.items()
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
        "effective_balance_date": effective_balance_date.isoformat(),
        "reporting_period": _period_snapshot(
            periods_by_id.get(selected_period_id) if selected_period_id else None
        ),
        "currency": currency,
        "balances": [
            {
                "id": str(item.id),
                "balance_type": item.balance_type,
                "amount": str(item.amount),
                "currency": item.currency,
                "as_of_date": item.as_of_date.isoformat() if item.as_of_date else None,
                "reporting_period_id": (
                    str(item.reporting_period_id) if item.reporting_period_id else None
                ),
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
                "reporting_period_id": (
                    str(item.reporting_period_id) if item.reporting_period_id else None
                ),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in cash_flows
        ],
        "obligations": [
            {
                "id": str(item.id),
                "principal_amount": str(item.principal_amount),
                "outstanding_amount": str(item.outstanding_amount),
                "currency": item.currency,
                "status": item.status,
                "reporting_period_id": (
                    str(item.reporting_period_id) if item.reporting_period_id else None
                ),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in obligations
        ],
    }
    return snapshot, as_of_date


def _record_effective_date(
    record_date: date | None, reporting_period: FinancialReportingPeriod | None
) -> date | None:
    if record_date is not None:
        return record_date
    if reporting_period is None:
        return None
    return reporting_period.as_of_date or reporting_period.end_date or reporting_period.start_date


def _select_period_records(
    records: list[Any],
    periods_by_id: dict[UUID, FinancialReportingPeriod],
    as_of_date: date,
    selected_period_id: UUID | None,
    record_date: Callable[[Any], date | None],
) -> list[Any]:
    selected_period = periods_by_id.get(selected_period_id) if selected_period_id else None
    current: list[Any] = []
    dated: list[tuple[Any, date]] = []
    for item in records:
        explicit_date = record_date(item)
        period = periods_by_id.get(item.reporting_period_id) if item.reporting_period_id else None
        effective_date = _record_effective_date(explicit_date, period)
        if effective_date is not None and effective_date > as_of_date:
            continue
        if item.reporting_period_id is not None:
            if selected_period_id is None:
                if effective_date is not None:
                    dated.append((item, effective_date))
            elif item.reporting_period_id == selected_period_id:
                current.append(item)
            continue
        if explicit_date is None or (
            selected_period is not None and _date_in_period(explicit_date, selected_period)
        ):
            current.append(item)
        elif selected_period is None:
            dated.append((item, explicit_date))
    if selected_period_id is not None or not dated:
        return current
    latest_date = max(value for _, value in dated)
    return [*current, *(item for item, value in dated if value == latest_date)]


def _date_in_period(value: date, period: FinancialReportingPeriod) -> bool:
    if period.start_date is not None and value < period.start_date:
        return False
    boundary = period.end_date or period.as_of_date
    return boundary is None or value <= boundary


def _period_snapshot(period: FinancialReportingPeriod | None) -> dict[str, Any] | None:
    if period is None:
        return None
    return {
        "id": str(period.id),
        "period_type": period.period_type,
        "start_date": period.start_date.isoformat() if period.start_date else None,
        "end_date": period.end_date.isoformat() if period.end_date else None,
        "as_of_date": period.as_of_date.isoformat() if period.as_of_date else None,
        "label": period.label,
    }


def _normalized_balance_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def calculate_forecast(snapshot: dict[str, Any]) -> list[ForecastValue]:
    assumptions = snapshot["scenario"]["numeric_assumptions"]
    revenue_growth = Decimal(assumptions["revenue_growth_rate"])
    expense_growth = Decimal(assumptions["expense_growth_rate"])
    delay_days = Decimal(assumptions["cash_flow_delay_days"])
    credit_usage = Decimal(assumptions["credit_usage_rate"])
    repayment_rate = Decimal(assumptions["repayment_rate"])
    if delay_days < 0 or delay_days > 365:
        raise CalculationInputError(
            "invalid_assumption",
            "Cash-flow delay must be between 0 and 365 days.",
            {"corrective_action": "Update and review the cash-flow timing assumption."},
        )
    if credit_usage < 0 or credit_usage > 1 or repayment_rate < 0 or repayment_rate > 1:
        raise CalculationInputError(
            "invalid_assumption",
            "Credit usage and repayment rates must be between 0 and 1.",
            {"corrective_action": "Update and review the listed rate assumptions."},
        )

    asset_balances = [
        Decimal(item["amount"])
        for item in snapshot["balances"]
        if _normalized_balance_type(item["balance_type"]) in ASSET_TYPES
    ]
    liability_balances = [
        Decimal(item["amount"])
        for item in snapshot["balances"]
        if _normalized_balance_type(item["balance_type"]) in LIABILITY_TYPES
    ]
    cash_balances = [
        Decimal(item["amount"])
        for item in snapshot["balances"]
        if _normalized_balance_type(item["balance_type"]) in {"cash", "cash_and_equivalents"}
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
            "invalid_assumption",
            f"{key} must be a numeric value.",
            {
                "assumption": key,
                "corrective_action": "Enter a numeric value and review the assumption again.",
            },
        )
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CalculationInputError(
            "invalid_assumption",
            f"{key} must be a numeric value.",
            {
                "assumption": key,
                "corrective_action": "Enter a numeric value and review the assumption again.",
            },
        ) from exc
    if not parsed.is_finite():
        raise CalculationInputError(
            "invalid_assumption",
            f"{key} must be a finite numeric value.",
            {
                "assumption": key,
                "corrective_action": (
                    "Enter a finite numeric value and review the assumption again."
                ),
            },
        )
    return parsed


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise CalculationInputError(
            "calculation_output_out_of_range",
            "A forecast value exceeds the supported monetary range.",
            {
                "maximum_absolute_value": str(MAX_STORED_MONEY),
                "corrective_action": (
                    "Correct oversized financial inputs and run the forecast again."
                ),
            },
        )
    try:
        rounded = value.quantize(MONEY, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise CalculationInputError(
            "calculation_output_out_of_range",
            "A forecast value exceeds the supported monetary range.",
            {
                "maximum_absolute_value": str(MAX_STORED_MONEY),
                "corrective_action": (
                    "Correct oversized financial inputs and run the forecast again."
                ),
            },
        ) from exc
    if abs(rounded) > MAX_STORED_MONEY:
        raise CalculationInputError(
            "calculation_output_out_of_range",
            "A forecast value exceeds the supported monetary range.",
            {
                "maximum_absolute_value": str(MAX_STORED_MONEY),
                "corrective_action": (
                    "Correct oversized financial inputs and run the forecast again."
                ),
            },
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


def _pending_snapshot(
    case_id: UUID, scenario_id: UUID, forecast_periods: int, as_of_date: date
) -> dict[str, Any]:
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "case_id": str(case_id),
        "scenario_id": str(scenario_id),
        "forecast_periods": forecast_periods,
        "as_of_date": as_of_date.isoformat(),
        "snapshot_status": "pending",
    }


def _begin_repeatable_read(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})


def _persist_failure(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    run_id: UUID,
    error: CalculationInputError,
    canonical_snapshot: dict[str, Any] | None = None,
) -> None:
    db.rollback()
    run = _run_or_404(db, ctx, case_id, run_id)
    db.execute(
        delete(CalculationForecastPeriod).where(
            CalculationForecastPeriod.organization_id == ctx.organization_id,
            CalculationForecastPeriod.case_id == case_id,
            CalculationForecastPeriod.run_id == run.id,
        )
    )
    if canonical_snapshot is not None:
        run.inputs = canonical_snapshot
        run.input_hash = _snapshot_hash(canonical_snapshot)
        run.as_of_date = date.fromisoformat(canonical_snapshot["as_of_date"])
        record_event(
            db,
            ctx,
            event_type="calculation_run.input_snapshot_established",
            entity_type="calculation_run",
            entity_id=run.id,
            details={
                "input_hash": run.input_hash,
                "input_hash_status": "established",
                "as_of_date": run.as_of_date.isoformat(),
                "input_schema_version": INPUT_SCHEMA_VERSION,
            },
        )
        input_hash_status = "established"
    else:
        record_event(
            db,
            ctx,
            event_type="calculation_run.input_snapshot_rejected",
            entity_type="calculation_run",
            entity_id=run.id,
            details={
                "input_hash": run.input_hash,
                "input_hash_status": "rejected",
                "as_of_date": run.as_of_date.isoformat(),
                "input_schema_version": INPUT_SCHEMA_VERSION,
                "error_code": error.code,
            },
        )
        input_hash_status = "rejected"
    _mark_failed(run, error)
    record_event(
        db,
        ctx,
        event_type="calculation_run.failed",
        entity_type="calculation_run",
        entity_id=run.id,
        details={
            "input_hash": run.input_hash,
            "input_hash_status": input_hash_status,
            "output_periods": 0,
            "error_code": error.code,
        },
    )
    db.commit()


def _error_read(run: CalculationRun) -> CalculationErrorRead | None:
    if not run.error_code or not run.error_message:
        return None
    return CalculationErrorRead(
        code=run.error_code, message=run.error_message, details=run.error_details
    )


def _read_summary(run: Mapping[Any, Any]) -> CalculationRunSummaryRead:
    error = None
    if run["error_code"] and run["error_message"]:
        error = CalculationErrorRead(
            code=run["error_code"],
            message=run["error_message"],
            details=run["error_details"],
        )
    return CalculationRunSummaryRead(
        id=run["id"],
        scenario_id=run["scenario_id"],
        rerun_of_run_id=run["rerun_of_run_id"],
        status=run["status"],
        engine_version=run["engine_version"],
        input_hash=run["input_hash"],
        forecast_periods=run["forecast_periods"],
        as_of_date=run["as_of_date"],
        started_at=run["started_at"],
        completed_at=run["completed_at"],
        error=error,
        created_at=run["created_at"],
    )


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
        error=_error_read(run),
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


def _scenario_or_404(
    db: Session, ctx: TenantContext, case_id: UUID, scenario_id: UUID
) -> RiskScenario:
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
    return scenario


def _require_actor(ctx: TenantContext) -> None:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header is required."
        )
