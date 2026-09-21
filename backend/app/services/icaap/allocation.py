"""Allocating the internal capital requirement to business units (¶49(l)).

The allocation is derived, not typed: the bank supplies DRIVERS — an RWA share,
an exposure share, or an explicit percentage — and the requirement lines are
distributed across them. Distribution uses largest remainder, so the parts add
back to the total exactly rather than to the total plus or minus a rounding
crumb, which is the difference between a table that reconciles and one that
invites a question at a supervisory meeting.

It is a replace-all save with an optimistic digest. The digest covers the
drivers AND the requirement snapshot they were computed against, so recomputing
the requirement after somebody has allocated it is visible rather than silent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap import allocation as domain
from app.models.icaap import IcaapCycle
from app.models.icaap_risk_capital import IcaapCapitalAllocation
from app.schemas.icaap_risk_capital import (
    IcaapAllocationCellRead,
    IcaapAllocationPut,
    IcaapAllocationRead,
    IcaapAllocationUnitRead,
    IcaapRequirementLineRead,
)
from app.services import jurisdictions
from app.services.audit import record_event
from app.services.icaap import digests, guards, reconciliation


def _rows(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> list[IcaapCapitalAllocation]:
    return list(
        db.scalars(
            select(IcaapCapitalAllocation)
            .where(
                IcaapCapitalAllocation.organization_id == access.ctx.organization_id,
                IcaapCapitalAllocation.cycle_id == cycle.id,
            )
            .order_by(
                IcaapCapitalAllocation.unit_key.asc(),
                IcaapCapitalAllocation.risk_line_key.asc(),
            )
        )
    )


def _line_totals(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> tuple[dict[str, Decimal], list[IcaapRequirementLineRead], str | None]:
    lines = reconciliation.requirement_lines(db, access, cycle)
    totals = {
        line.line_key: line.internal_amount for line in lines if line.internal_amount is not None
    }
    reads = [
        IcaapRequirementLineRead(
            line_key=line.line_key,
            line_group=line.line_group,
            position=line.position,
            label=line.label,
            table5_row=line.table5_row,
            internal_amount=line.internal_amount,
            regulatory_amount=line.regulatory_amount,
            difference=line.difference,
            explanation_required=line.explanation_required,
        )
        for line in lines
    ]
    snapshot = lines[0].computed_digest if lines else None
    return totals, reads, snapshot


def _digest(payload_drivers: list[dict[str, Any]], snapshot: str | None) -> str:
    return digests.register_digest({"drivers": payload_drivers, "requirement_digest": snapshot})


def get_allocation(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapAllocationRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    rows = _rows(db, access, cycle)
    _totals, lines, _snapshot = _line_totals(db, access, cycle)
    units: dict[str, tuple[str, str]] = {}
    unit_totals: dict[str, Decimal] = {}
    for row in rows:
        units.setdefault(row.unit_key, (row.unit_label, row.unit_kind))
        if row.allocated_amount is not None:
            unit_totals[row.unit_key] = (
                unit_totals.get(row.unit_key, Decimal(0)) + row.allocated_amount
            )
    total = sum(unit_totals.values(), Decimal(0)) if unit_totals else None
    return IcaapAllocationRead(
        cycle_id=cycle.id,
        currency=jurisdictions.base_currency(access.bank),
        units=[
            IcaapAllocationUnitRead(
                unit_key=key,
                unit_label=label,
                unit_kind=kind,  # pyright: ignore[reportArgumentType]
                total_allocated=unit_totals.get(key),
            )
            for key, (label, kind) in sorted(units.items())
        ],
        lines=lines,
        cells=[
            IcaapAllocationCellRead(
                unit_key=row.unit_key,
                risk_line_key=row.risk_line_key,
                driver_kind=row.driver_kind,  # pyright: ignore[reportArgumentType]
                driver_value=row.driver_value,
                allocated_amount=row.allocated_amount,
            )
            for row in rows
        ],
        digest=rows[0].allocation_digest if rows else None,
        total_allocated=total,
        available=bool(lines),
        unavailable_reason=(
            None
            if lines
            else "Compute the capital requirement reconciliation before allocating it."
        ),
    )


def put_allocation(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapAllocationPut
) -> IcaapAllocationRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    totals, _lines, snapshot = _line_totals(db, access, cycle)
    if not totals:
        raise guards.conflict(
            "allocation_unavailable",
            "Compute the capital requirement reconciliation before allocating it.",
        )
    existing = _rows(db, access, cycle)
    if (
        existing
        and payload.base_digest is not None
        and payload.base_digest != existing[0].allocation_digest
    ):
        raise guards.conflict(
            "row_rev_conflict",
            "The allocation or the requirement changed while you were editing.",
            current_rev=0,
            current_digest=existing[0].allocation_digest,
        )
    unit_labels = {unit.unit_key: unit for unit in payload.units}
    unknown_units = sorted({driver.unit_key for driver in payload.drivers} - set(unit_labels))
    if unknown_units:
        raise guards.unprocessable(
            "unknown_metric",
            "A driver names an allocation unit that is not in the list.",
            unit_keys=unknown_units,
        )
    unknown_lines = sorted({driver.risk_line_key for driver in payload.drivers} - set(totals))
    if unknown_lines:
        raise guards.unprocessable(
            "unknown_risk_key",
            "A driver names a requirement line this ICAAP does not have.",
            line_keys=unknown_lines,
        )
    drivers = [
        domain.Driver(
            unit_key=driver.unit_key,
            risk_line_key=driver.risk_line_key,
            kind=driver.driver_kind,
            value=driver.driver_value,
        )
        for driver in payload.drivers
    ]
    try:
        result = domain.allocate(totals, drivers)
    except domain.AllocationError as exc:
        raise guards.unprocessable(
            _allocation_code(exc),
            "These drivers cannot be turned into an allocation.",
            code=exc.code,
            line_key=getattr(exc, "line_key", None),
        ) from exc

    driver_body = [
        {
            "unit_key": driver.unit_key,
            "risk_line_key": driver.risk_line_key,
            "driver_kind": driver.kind,
            "driver_value": str(driver.value),
        }
        for driver in sorted(drivers, key=lambda entry: (entry.unit_key, entry.risk_line_key))
    ]
    digest = _digest(driver_body, snapshot)
    for row in existing:
        db.delete(row)
    db.flush()
    for driver in drivers:
        unit = unit_labels[driver.unit_key]
        db.add(
            IcaapCapitalAllocation(
                organization_id=cycle.organization_id,
                bank_id=cycle.bank_id,
                cycle_id=cycle.id,
                unit_key=driver.unit_key,
                unit_label=unit.unit_label,
                unit_kind=unit.unit_kind,
                risk_line_key=driver.risk_line_key,
                driver_kind=driver.kind,
                driver_value=driver.value,
                allocated_amount=result.amounts.get((driver.unit_key, driver.risk_line_key)),
                allocation_digest=digest,
                created_by=guards.actor_id(access),
                updated_by=guards.actor_id(access),
            )
        )
    record_event(
        db,
        access.ctx,
        event_type="icaap.allocation.saved",
        entity_type="icaap_cycle",
        entity_id=cycle.id,
        details={
            "cycle_id": str(cycle.id),
            "units": len(payload.units),
            "drivers": len(drivers),
            "digest": digest,
            "reason": payload.reason,
        },
    )
    db.commit()
    return get_allocation(db, access, cycle_id)


_ALLOCATION_CODES = {
    "manual_pct_not_hundred": "allocation_pct_not_hundred",
    "drivers_all_zero": "allocation_drivers_all_zero",
}


def _allocation_code(exc: domain.AllocationError) -> str:
    return _ALLOCATION_CODES.get(exc.code, "allocation_drivers_all_zero")


def allocation_payload(read: IcaapAllocationRead) -> dict[str, Any]:
    return {
        "units": [
            {
                "unit_key": unit.unit_key,
                "unit_label": unit.unit_label,
                "unit_kind": unit.unit_kind,
                "total_allocated": None
                if unit.total_allocated is None
                else str(unit.total_allocated),
            }
            for unit in read.units
        ],
        "cells": [
            {
                "unit_key": cell.unit_key,
                "risk_line_key": cell.risk_line_key,
                "driver_kind": cell.driver_kind,
                "driver_value": str(cell.driver_value),
                "allocated_amount": None
                if cell.allocated_amount is None
                else str(cell.allocated_amount),
            }
            for cell in read.cells
        ],
    }


__all__ = ["allocation_payload", "get_allocation", "put_allocation"]
