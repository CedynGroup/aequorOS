"""Reverse stress testing (Phase 2 item 4; FRM reading 9).

Searches over the existing scenario engines for the severity multipliers at
which the bank breaches its hard floors — LCR below its minimum on the
liquidity axis (scaling the ``combined`` scenario) and the four-quarter CET1
path below the CET1 minimum on the capital axis (scaling ``severe``). The
frontier is persisted as an immutable ``RegulatoryRun`` (module
``reverse_stress``) with the same value-based input-hash discipline as every
other official run, so a Board pack can cite exactly which book and which
parameter set produced the frontier.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryRun
from app.schemas.reverse_stress import ReverseStressRead, ReverseStressRunCreate
from app.services import regulatory_capital, regulatory_liquidity, regulatory_parameters
from app.services.audit import record_event

ENGINE_VERSION = "reverse-stress-v1.0.0"
INPUT_SCHEMA_VERSION = "reverse-stress-input-v1"
OUTPUT_SCHEMA_VERSION = "reverse-stress-frontier-v1"
MODULE_REVERSE_STRESS = "reverse_stress"
FRONTIER_SCENARIO = "frontier"

_LIQUIDITY_AXIS_SCENARIO = "combined"
_CAPITAL_AXIS_SCENARIO = "severe"


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_period_or_404(
    db: Session, ctx: TenantContext, bank: Bank, period_id: UUID
) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.id == period_id,
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    return period


def _hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()


def run_reverse_stress(
    db: Session, ctx: TenantContext, bank_id: str, payload: ReverseStressRunCreate
) -> ReverseStressRead:
    """Compute and persist the reverse-stress frontier for one period."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)

    # Anchor the frontier to the exact canonical state it searched over: the
    # baseline input hashes of both engines (same snapshots the official
    # runs use), plus the axis definitions.
    liquidity_hash = regulatory_liquidity.current_input_hash(db, ctx, bank, period)
    capital_hash = regulatory_capital.current_input_hash(db, ctx, bank, period)
    if liquidity_hash is None or capital_hash is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "financial_facts_missing",
                "message": "The reporting period has no financial facts to analyze.",
            },
        )
    inputs = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": MODULE_REVERSE_STRESS,
        "bank_id": str(bank.id),
        "reporting_period_id": str(period.id),
        "as_of_date": period.period_end.isoformat(),
        "axes": {
            "liquidity": {
                "scenario_code": _LIQUIDITY_AXIS_SCENARIO,
                "floor": "lcr_min_pct",
                "baseline_input_hash": liquidity_hash,
            },
            "capital": {
                "scenario_code": _CAPITAL_AXIS_SCENARIO,
                "floor": "cet1_min_pct",
                "baseline_input_hash": capital_hash,
            },
        },
        "search": {"k_max": "5", "precision": "0.05", "method": "bisection"},
    }

    try:
        liquidity_axis = regulatory_liquidity.liquidity_breach_multiplier(
            db, ctx, bank, period, _LIQUIDITY_AXIS_SCENARIO
        )
        capital_axis = regulatory_capital.capital_breach_multiplier(
            db, ctx, bank, period, _CAPITAL_AXIS_SCENARIO
        )
    except (
        regulatory_liquidity.LiquidityRunError,
        regulatory_capital.CapitalRunError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc

    narrative = _narrative(liquidity_axis, capital_axis)
    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=MODULE_REVERSE_STRESS,
        scenario_code=FRONTIER_SCENARIO,
        status="succeeded",
        engine_version=ENGINE_VERSION,
        input_schema_version=INPUT_SCHEMA_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        input_hash=_hash(inputs),
        inputs=inputs,
        metrics={
            "liquidity_axis": liquidity_axis,
            "capital_axis": capital_axis,
            "narrative": narrative,
        },
        # Audit D-18: WHICH governed control-plane rows produced the values in
        # ``inputs["parameters"]``. Beside the snapshot, never inside it — row
        # ids and timestamps are identity, not values, and the ``input_hash`` is
        # value-based by contract.
        parameter_provenance=regulatory_parameters.consume_parameter_provenance(db),
        created_by=ctx.actor_user_id,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="reverse_stress.completed",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={
            "bank_id": str(bank.id),
            "reporting_period_id": str(period.id),
            "input_hash": run.input_hash,
            "liquidity_breached": liquidity_axis["breached"],
            "capital_breached": capital_axis["breached"],
        },
    )
    db.commit()
    return _read(run)


def get_latest_reverse_stress(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID
) -> ReverseStressRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == reporting_period_id,
            RegulatoryRun.module == MODULE_REVERSE_STRESS,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No reverse-stress run exists for this reporting period.",
        )
    return _read(run)


def _narrative(liquidity: dict[str, Any], capital: dict[str, Any]) -> str:
    parts: list[str] = []
    if liquidity["breached"]:
        parts.append(
            f"Liquidity: scaling the '{liquidity['scenario_code']}' scenario to "
            f"{liquidity['breach_multiplier']}x its configured severity drives the "
            f"LCR to {liquidity['lcr_at_breach_pct']}%, below the "
            f"{liquidity['lcr_min_pct']}% floor."
        )
    else:
        parts.append(
            f"Liquidity: the LCR stays above its {liquidity['lcr_min_pct']}% floor "
            f"even at {liquidity['k_max']}x the '{liquidity['scenario_code']}' "
            "scenario's severity."
        )
    if capital["breached"]:
        parts.append(
            f"Capital: scaling the '{capital['scenario_code']}' scenario to "
            f"{capital['breach_multiplier']}x drives the worst quarterly CET1 "
            f"ratio to {capital['worst_cet1_at_breach_pct']}%, below the "
            f"{capital['cet1_min_pct']}% minimum."
        )
    else:
        parts.append(
            f"Capital: the CET1 path stays above its {capital['cet1_min_pct']}% "
            f"minimum even at {capital['k_max']}x the '{capital['scenario_code']}' "
            "scenario's severity."
        )
    return " ".join(parts)


def _read(run: RegulatoryRun) -> ReverseStressRead:
    return ReverseStressRead(
        run_id=run.id,
        bank_id=run.bank_id,
        reporting_period_id=run.reporting_period_id,
        input_hash=run.input_hash,
        engine_version=run.engine_version,
        liquidity_axis=run.metrics["liquidity_axis"],
        capital_axis=run.metrics["capital_axis"],
        narrative=run.metrics["narrative"],
        created_at=run.created_at,
    )
