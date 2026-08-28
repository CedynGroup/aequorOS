"""/operator/v1/regulatory-parameters — the regulatory-parameter control plane
(STAFF surface, docs/sdi.md §7 Phase C).

The operator-managed source of truth for class/type-keyed regulatory numbers
(CAR floors, exposure limits, paid-up floors, LMTD liquidity floors, provisioning
rates …). Reads are open to any authenticated operator; changes are four-eyes
(propose → approve by a different operator) and every mutation is recorded through
``record_operator_action`` — the audit trail behind "who changed the parameters,
when, and why" (regulatory defensibility). This router is mounted ONLY on the
operator app, never the tenant API (the route-isolation test pins that).
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.models import RegulatoryParameter
from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.operator.services import regulatory_parameters as service
from app.schemas.operator import (
    RegulatoryParameterApproveRequest,
    RegulatoryParameterListRead,
    RegulatoryParameterProposeRequest,
    RegulatoryParameterRead,
)
from app.services import live_refresh_triggers

#: Observability (docs/sdi.md §19) — a structured runtime-log line for every
#: control-plane mutation, alongside the persistent ``operator_audit_log`` record.
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/regulatory-parameters", tags=["regulatory-parameters"])


def _read(row: RegulatoryParameter) -> RegulatoryParameterRead:
    return RegulatoryParameterRead(
        id=row.id,
        scope_type=row.scope_type,  # type: ignore[arg-type]
        scope_key=row.scope_key,
        param_code=row.param_code,
        jurisdiction_code=row.jurisdiction_code,
        value_numeric=row.value_numeric,
        value_json=row.value_json,
        unit=row.unit,
        source_citation=row.source_citation,
        confirmation_status=row.confirmation_status,  # type: ignore[arg-type]
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        status=row.status,  # type: ignore[arg-type]
        proposed_by=row.proposed_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        change_rationale=row.change_rationale,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=RegulatoryParameterListRead, operation_id="listRegulatoryParameters")
def list_regulatory_parameters(  # noqa: PLR0913 - query filters, one per column
    db: OperatorDb,
    _operator: Operator,
    scope_type: Annotated[str | None, Query()] = None,
    scope_key: Annotated[str | None, Query()] = None,
    param_code: Annotated[str | None, Query()] = None,
    confirmation_status: Annotated[str | None, Query()] = None,
    include_drafts: Annotated[bool, Query()] = True,
) -> RegulatoryParameterListRead:
    rows = service.list_parameters(
        db,
        scope_type=scope_type,
        scope_key=scope_key,
        param_code=param_code,
        confirmation_status=confirmation_status,
        include_drafts=include_drafts,
    )
    return RegulatoryParameterListRead(parameters=[_read(r) for r in rows], total=len(rows))


@router.post(
    "",
    response_model=RegulatoryParameterRead,
    status_code=201,
    operation_id="proposeRegulatoryParameter",
)
def propose_regulatory_parameter(
    payload: RegulatoryParameterProposeRequest,
    db: OperatorDb,
    operator: Operator,
) -> RegulatoryParameterRead:
    row = service.propose(db, payload=payload, proposed_by=operator.email)
    logger.info(
        "regulatory_parameter.propose param_id=%s scope=%s/%s code=%s value=%s "
        "effective_from=%s proposed_by=%s",
        row.id,
        row.scope_type,
        row.scope_key,
        row.param_code,
        row.value_numeric,
        row.effective_from.isoformat(),
        operator.email,
    )
    record_operator_action(
        db,
        operator,
        action="regulatory_parameter.propose",
        detail={
            "param_id": str(row.id),
            "scope_type": row.scope_type,
            "scope_key": row.scope_key,
            "param_code": row.param_code,
            "effective_from": row.effective_from.isoformat(),
        },
    )
    db.commit()
    return _read(row)


@router.post(
    "/{param_id}/approve",
    response_model=RegulatoryParameterRead,
    operation_id="approveRegulatoryParameter",
)
def approve_regulatory_parameter(
    param_id: UUID,
    payload: RegulatoryParameterApproveRequest,
    db: OperatorDb,
    operator: Operator,
) -> RegulatoryParameterRead:
    row = service.approve(
        db,
        param_id=param_id,
        approved_by=operator.email,
        change_rationale=payload.change_rationale,
    )
    refresh_jobs = live_refresh_triggers.enqueue_regulatory_parameter_change(db, row)
    logger.info(
        "regulatory_parameter.approve param_id=%s scope=%s/%s code=%s value=%s "
        "effective_from=%s approved_by=%s",
        row.id,
        row.scope_type,
        row.scope_key,
        row.param_code,
        row.value_numeric,
        row.effective_from.isoformat(),
        operator.email,
    )
    record_operator_action(
        db,
        operator,
        action="regulatory_parameter.approve",
        detail={
            "param_id": str(row.id),
            "param_code": row.param_code,
            "scope_key": row.scope_key,
            "effective_from": row.effective_from.isoformat(),
            "live_refreshes_enqueued": len(refresh_jobs),
        },
    )
    db.commit()
    return _read(row)
