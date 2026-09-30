"""Session-gated BI mart backfill (Tenant Inspector, the BI write side).

One route: start a bank's mart history walk. It (1) resolves the caller's
active inspection session via ``require_active_inspection`` (403
``inspection_required`` otherwise), (2) enqueues the chain on the operator's
cross-tenant BYPASSRLS session — NEVER a tenant impersonation token — and
(3) writes a ``bi.backfill`` row to ``operator_audit_log`` before the single
commit, so the enqueue and its audit land (or roll back) atomically. ``note``
is required and carried into the audit detail. Org-scoped throughout: a
sibling tenant's bank is a 404.

Mounted ONLY on the operator app (``app.operator.main``); the tenant API never
exposes it — ``tests/operator/test_route_isolation.py`` pins the path.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.operator.inspection import require_active_inspection
from app.operator.services import bi_backfill
from app.schemas.operator import BiBackfillRead, BiBackfillRequest
from app.services.public_ids import normalize_public_id

router = APIRouter(prefix="/tenants", tags=["operator-bi"])


@router.post("/{org_id}/bi/backfill", response_model=BiBackfillRead)
def start_bi_backfill(
    org_id: str, payload: BiBackfillRequest, db: OperatorDb, operator: Operator
) -> BiBackfillRead:
    """Walk one bank's mart history newest-first (enqueues ONE ``bi_mart_backfill``
    that re-enqueues itself hop by hop). 409 if a chain for the bank is already
    queued or running. Session-gated + audited as ``bi.backfill``."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = bi_backfill.start_backfill(db, organization_id, payload)
    record_operator_action(
        db,
        operator,
        action="bi.backfill",
        target_org=organization_id,
        detail={
            "session_id": str(session.id),
            "note": payload.note,
            "bank_id": result.bank_id,
            "cursor_date": result.cursor_date.isoformat(),
            "until_date": result.until_date.isoformat(),
            "builder_version": result.builder_version,
            "job_id": str(result.job_id),
        },
    )
    db.commit()
    return result
