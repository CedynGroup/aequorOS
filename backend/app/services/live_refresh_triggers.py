"""Authoritative non-ingestion triggers for the live calculation plane.

Ingestion and market-data writers already enqueue their target bank directly.
Governed methodology, parameter, entitlement, and reconciliation mutations fan
out one coalesced refresh per affected bank here, in the same transaction as
the mutation. Reads never heal or schedule the live plane.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Bank, CurrentFinancialFact, InstitutionType, Job, RegulatoryParameter
from app.services import job_queue


def _enqueue_bank(
    db: Session,
    bank: Bank,
    *,
    reason: str,
) -> Job | None:
    """Enqueue from the bank's current live input generation, if one exists."""
    as_of = db.scalar(
        select(func.max(CurrentFinancialFact.source_as_of_date)).where(
            CurrentFinancialFact.organization_id == bank.organization_id,
            CurrentFinancialFact.bank_id == bank.id,
        )
    )
    if as_of is None:
        return None
    return job_queue.enqueue(
        db,
        bank.organization_id,
        "pipeline_refresh",
        bank_id=bank.id,
        payload={"as_of_date": as_of.isoformat(), "reason": reason},
        coalesce_key=f"refresh:{bank.id}:{as_of.isoformat()}",
    )


def enqueue_entitlement_change(
    db: Session,
    *,
    organization_id: str,
    reason: str,
) -> list[Job]:
    """Reflow every live bank whose tenant entitlement digest changed."""
    jobs = [
        job
        for bank in db.scalars(select(Bank).where(Bank.organization_id == organization_id))
        if (job := _enqueue_bank(db, bank, reason=reason)) is not None
    ]
    db.flush()
    return jobs


def enqueue_bank_change(
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    reason: str,
) -> Job | None:
    bank = db.scalar(
        select(Bank).where(
            Bank.organization_id == organization_id,
            Bank.id == bank_id,
        )
    )
    if bank is None:
        return None
    job = _enqueue_bank(db, bank, reason=reason)
    db.flush()
    return job


def enqueue_organization_change(
    db: Session,
    *,
    organization_id: str,
    reason: str,
    jurisdiction_code: str | None = None,
) -> list[Job]:
    stmt = select(Bank).where(Bank.organization_id == organization_id)
    if jurisdiction_code is not None:
        stmt = stmt.where(Bank.jurisdiction_code == jurisdiction_code)
    jobs = [
        job
        for bank in db.scalars(stmt)
        if (job := _enqueue_bank(db, bank, reason=reason)) is not None
    ]
    db.flush()
    return jobs


def enqueue_methodology_change(
    db: Session,
    *,
    methodology_code: str,
    version: int,
) -> list[Job]:
    reason = f"desk methodology approved:{methodology_code}:v{version}"
    jobs = [
        job
        for bank in db.scalars(select(Bank))
        if (job := _enqueue_bank(db, bank, reason=reason)) is not None
    ]
    db.flush()
    return jobs


def enqueue_regulatory_parameter_change(
    db: Session,
    parameter: RegulatoryParameter,
) -> list[Job]:
    """Reflow banks addressed by one newly approved global parameter."""
    stmt = (
        select(Bank)
        .join(InstitutionType, InstitutionType.type_code == Bank.institution_type)
        .where(Bank.jurisdiction_code == parameter.jurisdiction_code)
    )
    if parameter.scope_type == "institution_type":
        stmt = stmt.where(Bank.institution_type == parameter.scope_key)
    else:
        stmt = stmt.where(InstitutionType.institution_class == parameter.scope_key)

    reason = f"governed-parameter approved:{parameter.param_code}:{parameter.id}"
    jobs = [
        job
        for bank in db.scalars(stmt)
        if (job := _enqueue_bank(db, bank, reason=reason)) is not None
    ]
    db.flush()
    return jobs
