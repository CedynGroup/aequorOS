"""Operator-side management of the regulatory-parameter control plane.

Four-eyes, effective-dated maintenance of the global ``regulatory_parameter``
store (docs/sdi.md §7 Phase C): list the current generation + full history,
propose a new generation (lands as ``draft``), and approve it (approver MUST
differ from the proposer) — which supersedes the prior open generation. The
resolver (``app.services.regulatory_parameters``) only ever reads APPROVED rows,
so a draft never affects a live calculation.

Mirrors the desk methodology register (``app/services/market_desk/register.py``)
and the tenant-param supersession discipline (``inspector_fix._supersede_liquidity``):
close the open row's ``effective_to`` before the successor goes live. Every
mutation is recorded by the caller through ``record_operator_action``.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import RegulatoryParameter
from app.schemas.operator import RegulatoryParameterProposeRequest


def list_parameters(  # noqa: PLR0913 - query filters, one per column
    db: Session,
    *,
    scope_type: str | None = None,
    scope_key: str | None = None,
    param_code: str | None = None,
    confirmation_status: str | None = None,
    include_drafts: bool = True,
) -> list[RegulatoryParameter]:
    """Every generation matching the filters, newest-effective first."""
    conditions = []
    if scope_type is not None:
        conditions.append(RegulatoryParameter.scope_type == scope_type)
    if scope_key is not None:
        conditions.append(RegulatoryParameter.scope_key == scope_key)
    if param_code is not None:
        conditions.append(RegulatoryParameter.param_code == param_code)
    if confirmation_status is not None:
        conditions.append(RegulatoryParameter.confirmation_status == confirmation_status)
    if not include_drafts:
        conditions.append(RegulatoryParameter.status == "approved")
    stmt = (
        select(RegulatoryParameter)
        .where(*conditions)
        .order_by(
            RegulatoryParameter.param_code,
            RegulatoryParameter.scope_type,
            RegulatoryParameter.scope_key,
            RegulatoryParameter.effective_from.desc(),
        )
    )
    return list(db.scalars(stmt))


def get_parameter(db: Session, param_id: UUID) -> RegulatoryParameter:
    row = db.get(RegulatoryParameter, param_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Regulatory parameter {param_id} not found.",
        )
    return row


def propose(
    db: Session,
    *,
    payload: RegulatoryParameterProposeRequest,
    proposed_by: str,
) -> RegulatoryParameter:
    """Maker step: create a ``draft`` generation. Not yet visible to the resolver."""
    if payload.effective_from < date.today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "effective_from cannot be in the past. A regulatory parameter change "
                "takes effect from today onward; back-dating would rewrite the value "
                "that historical official runs already resolved (reproducibility)."
            ),
        )
    existing = db.scalar(
        select(RegulatoryParameter).where(
            RegulatoryParameter.scope_type == payload.scope_type,
            RegulatoryParameter.scope_key == payload.scope_key,
            RegulatoryParameter.param_code == payload.param_code,
            RegulatoryParameter.jurisdiction_code == payload.jurisdiction_code,
            RegulatoryParameter.effective_from == payload.effective_from,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A generation of {payload.param_code!r} for "
                f"{payload.scope_type}={payload.scope_key!r} already exists effective "
                f"{payload.effective_from.isoformat()} (status {existing.status}). "
                "Choose a later effective date."
            ),
        )
    row = RegulatoryParameter(
        scope_type=payload.scope_type,
        scope_key=payload.scope_key,
        param_code=payload.param_code,
        jurisdiction_code=payload.jurisdiction_code,
        value_numeric=payload.value_numeric,
        value_json=payload.value_json,
        unit=payload.unit,
        source_citation=payload.source_citation,
        confirmation_status=payload.confirmation_status,
        effective_from=payload.effective_from,
        effective_to=None,
        status="draft",
        proposed_by=proposed_by,
        change_rationale=payload.change_rationale,
    )
    db.add(row)
    db.flush()
    return row


def approve(
    db: Session,
    *,
    param_id: UUID,
    approved_by: str,
    change_rationale: str | None = None,
) -> RegulatoryParameter:
    """Checker step: approve a draft (four-eyes) and supersede the prior open row."""
    row = get_parameter(db, param_id)
    if row.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Parameter {param_id} is already {row.status}; it is immutable.",
        )
    if approved_by.strip().lower() == row.proposed_by.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "A regulatory parameter cannot be approved by its proposer "
                "(four-eyes / dual control, docs/sdi.md §7 Phase C)."
            ),
        )
    # The prior open (or overlapping) approved generation for the same key.
    prior = db.scalar(
        select(RegulatoryParameter)
        .where(
            RegulatoryParameter.scope_type == row.scope_type,
            RegulatoryParameter.scope_key == row.scope_key,
            RegulatoryParameter.param_code == row.param_code,
            RegulatoryParameter.jurisdiction_code == row.jurisdiction_code,
            RegulatoryParameter.status == "approved",
            RegulatoryParameter.effective_from < row.effective_from,
            or_(
                RegulatoryParameter.effective_to.is_(None),
                RegulatoryParameter.effective_to > row.effective_from,
            ),
        )
        .order_by(RegulatoryParameter.effective_from.desc())
        .limit(1)
    )
    if prior is not None:
        prior.effective_to = row.effective_from
    row.status = "approved"
    row.approved_by = approved_by
    row.approved_at = utc_now()
    if change_rationale:
        row.change_rationale = change_rationale
    db.flush()
    return row
