"""Session-gated deep tenant FIXES (Tenant Inspector, the write side).

Each route REMEDIATES a bank issue from inside an ACTIVE inspection session:
it (1) resolves the caller's active session via ``require_active_inspection``
(403 ``inspection_required`` otherwise), (2) performs the fix on the operator's
cross-tenant BYPASSRLS session — NEVER a tenant impersonation token — and
(3) writes an ``inspector.fix.*`` row to ``operator_audit_log`` before the single
commit, so the action and its audit land (or roll back) atomically. ``note`` is
required on every action and is carried into the audit detail. All work is
org-scoped; a fix never touches another tenant.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.operator.inspection import require_active_inspection
from app.operator.services import inspector_fix
from app.schemas.operator import (
    FixConfigChangeRead,
    FixConfigRequest,
    FixJobEnqueuedRead,
    FixOfficialRunRequest,
    FixRecomputeRequest,
    FixRedriveDedupRead,
    FixRedriveDedupRequest,
    FixRerunIngestionRead,
    FixRerunIngestionRequest,
)
from app.services import live_refresh_triggers
from app.services.public_ids import normalize_public_id

router = APIRouter(prefix="/tenants", tags=["operator-inspector-fix"])


@router.post("/{org_id}/fix/recompute", response_model=FixJobEnqueuedRead)
def fix_recompute(
    org_id: str, payload: FixRecomputeRequest, db: OperatorDb, operator: Operator
) -> FixJobEnqueuedRead:
    """Recompute the bank's live metrics (enqueues a ``pipeline_refresh``).
    Session-gated + audited as ``inspector.fix.recompute``."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_fix.recompute(db, organization_id, payload)
    record_operator_action(
        db,
        operator,
        action="inspector.fix.recompute",
        target_org=organization_id,
        detail={
            "session_id": str(session.id),
            "note": payload.note,
            "bank_id": result.bank_id,
            "as_of_date": result.as_of_date,
            "job_id": str(result.read.job_id),
        },
    )
    db.commit()
    return result.read


@router.post("/{org_id}/fix/official-run", response_model=FixJobEnqueuedRead)
def fix_official_run(
    org_id: str, payload: FixOfficialRunRequest, db: OperatorDb, operator: Operator
) -> FixJobEnqueuedRead:
    """Mint an immutable official filing run (enqueues an ``official_run``).
    Session-gated + audited as ``inspector.fix.official_run``."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_fix.official_run(db, organization_id, payload)
    record_operator_action(
        db,
        operator,
        action="inspector.fix.official_run",
        target_org=organization_id,
        detail={
            "session_id": str(session.id),
            "note": payload.note,
            "bank_id": result.bank_id,
            "as_of_date": result.as_of_date,
            "job_id": str(result.read.job_id),
        },
    )
    db.commit()
    return result.read


@router.post("/{org_id}/fix/rerun-ingestion", response_model=FixRerunIngestionRead)
def fix_rerun_ingestion(
    org_id: str, payload: FixRerunIngestionRequest, db: OperatorDb, operator: Operator
) -> FixRerunIngestionRead:
    """Re-process an ingestion batch (404 if the batch is not this org's).
    Session-gated + audited as ``inspector.fix.rerun_ingestion``."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_fix.rerun_ingestion(db, organization_id, payload)
    record_operator_action(
        db,
        operator,
        action="inspector.fix.rerun_ingestion",
        target_org=organization_id,
        detail={
            "session_id": str(session.id),
            "note": payload.note,
            "batch_id": str(payload.batch_id),
            "job_id": str(result.job_id),
        },
    )
    db.commit()
    return result


@router.post("/{org_id}/fix/redrive-dedup", response_model=FixRedriveDedupRead)
def fix_redrive_dedup(
    org_id: str, payload: FixRedriveDedupRequest, db: OperatorDb, operator: Operator
) -> FixRedriveDedupRead:
    """Re-drive a stranded ML-ETL dedup pass (re-enqueues ``etl_dedup``).

    The only way back for a batch whose dedup job exhausted its attempts — the
    queue will never retry it and nothing else re-enqueues it. Manual by design
    (see ``inspector_fix.redrive_dedup``). Session-gated + audited as
    ``inspector.fix.redrive_dedup``, carrying the recorded failure it recovers
    from so the audit says what was being fixed."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_fix.redrive_dedup(db, organization_id, payload)
    record_operator_action(
        db,
        operator,
        action="inspector.fix.redrive_dedup",
        target_org=organization_id,
        detail={
            "session_id": str(session.id),
            "note": payload.note,
            "batch_id": str(payload.batch_id),
            "job_id": str(result.job_id),
            "previous_dedup_status": result.previous_dedup_status,
            "previous_job_id": (
                str(result.previous_job_id) if result.previous_job_id is not None else None
            ),
            "previous_job_error": result.previous_job_error,
        },
    )
    db.commit()
    return result


@router.post("/{org_id}/fix/config", response_model=FixConfigChangeRead)
def fix_config(
    org_id: str, payload: FixConfigRequest, db: OperatorDb, operator: Operator
) -> FixConfigChangeRead:
    """Apply a scoped, reversible config change — ``mapping_active`` toggle or
    ``threshold_value`` supersession (404 if the target is not this org's).
    Session-gated + audited as ``inspector.fix.config`` with before + after."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_fix.apply_config(
        db, organization_id, payload, approved_by=operator.email
    )
    refresh_jobs = (
        live_refresh_triggers.enqueue_organization_change(
            db,
            organization_id=organization_id,
            reason=f"operator threshold superseded:{result.target_id}",
        )
        if result.kind == "threshold_value"
        else []
    )
    record_operator_action(
        db,
        operator,
        action="inspector.fix.config",
        target_org=organization_id,
        detail={
            "session_id": str(session.id),
            "note": payload.note,
            "kind": result.kind,
            "target_id": result.target_id,
            "before": result.before,
            "after": result.after,
            "live_refreshes_enqueued": len(refresh_jobs),
        },
    )
    db.commit()
    return result
