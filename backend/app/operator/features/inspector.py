"""/operator/v1/inspector — tenant-inspector sessions + act-as-examiner handoff.

Opening a session records WHO is viewing WHICH tenant, WHY, and under which mode
(``consent`` routine, ``break_glass`` emergency). The session row + its audit
entries + the UI banner ARE the control; the console renders tenant data through
the operator read endpoints under that session.

``POST /sessions/{id}/act-token`` additionally mints a short-lived, READ-ONLY
"act-as-examiner" impersonation token so the operator can view the tenant IN THE
BANK APP, seeing exactly what the tenant sees, mapped to the read-only
``examiner`` role. The token is signed with the DEDICATED
``IMPERSONATION_JWT_SECRET`` (fail-closed: 503 when unset), is scoped to the one
tenant + the examiner role, never outlives its session (min(session, +15m)), and
grants no mutation (the tenant API refuses every write under impersonation). This
is the ONLY operator path that touches tenant auth, and it does so only through
the published token contract in ``app/core/security.py`` — never by importing
``app/api/deps.py``.

Gate: any authenticated operator, EXCEPT ``break_glass`` which additionally
requires ``operator_admin`` (the emergency path is the more powerful one).
"""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.core import security
from app.core.config import get_operator_settings, get_settings
from app.db.base import utc_now
from app.models.operator import OPERATOR_ROLE_RANK
from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.operator.services import inspector
from app.schemas.operator import (
    InspectorActTokenRead,
    InspectorSessionCreate,
    InspectorSessionListRead,
    InspectorSessionRead,
)
from app.services.public_ids import normalize_public_id

#: An act-as-examiner token never outlives this window, and never outlives its
#: originating inspector session — whichever is sooner (spec: min(session, +15m)).
ACT_TOKEN_MAX_TTL = timedelta(minutes=15)

router = APIRouter(prefix="/inspector", tags=["operator-inspector"])


@router.post("/sessions", response_model=InspectorSessionRead, status_code=201)
def start_inspector_session(
    payload: InspectorSessionCreate, db: OperatorDb, operator: Operator
) -> InspectorSessionRead:
    """Open a READ-ONLY inspection window (404 if the org is unknown).

    ``break_glass`` is the admin-gated emergency path; enforce the stricter role
    here rather than in the shared dependency so ``consent`` stays open to any
    operator."""
    if payload.mode == "break_glass" and (
        OPERATOR_ROLE_RANK[operator.role] < OPERATOR_ROLE_RANK["operator_admin"]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Break-glass inspector sessions require the operator_admin role or above.",
        )
    organization_id = normalize_public_id(payload.organization_id)
    row = inspector.start_session(
        db,
        organization_id=organization_id,
        started_by=operator.email,
        mode=payload.mode,
        reason=payload.reason,
        ttl_minutes=payload.ttl_minutes,
    )
    record_operator_action(
        db,
        operator,
        action="inspector.session.start",
        target_org=organization_id,
        detail={
            "session_id": str(row.id),
            "mode": row.mode,
            "read_only": row.read_only,
            "ttl_minutes": payload.ttl_minutes,
            "expires_at": row.expires_at.isoformat(),
            "reason": row.reason,
        },
    )
    db.commit()
    return inspector.session_read(row)


@router.get("/sessions", response_model=InspectorSessionListRead)
def list_inspector_sessions(
    db: OperatorDb,
    _operator: Operator,
    active: bool | None = None,
    organization_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> InspectorSessionListRead:
    """Audit surface, newest-first. ``active=true`` selects sessions that are
    neither ended nor expired."""
    return inspector.list_sessions(
        db,
        active=active,
        organization_id=(
            normalize_public_id(organization_id) if organization_id is not None else None
        ),
        limit=limit,
    )


@router.post("/sessions/{session_id}/end", response_model=InspectorSessionRead)
def end_inspector_session(
    session_id: UUID, db: OperatorDb, operator: Operator
) -> InspectorSessionRead:
    """Close a session (idempotent). 404 if the session is unknown."""
    row = inspector.end_session(db, session_id, ended_by=operator.email)
    record_operator_action(
        db,
        operator,
        action="inspector.session.end",
        target_org=row.organization_id,
        detail={"session_id": str(row.id)},
    )
    db.commit()
    return inspector.session_read(row)


@router.post("/sessions/{session_id}/act-token", response_model=InspectorActTokenRead)
def mint_inspector_act_token(
    session_id: UUID, db: OperatorDb, operator: Operator
) -> InspectorActTokenRead:
    """Mint a READ-ONLY act-as-examiner token for a caller-owned ACTIVE session.

    Requirements (all enforced here): the ``IMPERSONATION_JWT_SECRET`` is
    configured (else 503 — fail closed), the session exists (else 404), the
    CALLER opened it (else 403), and it is neither ended nor expired (else 409).
    The token is scoped to the session's tenant + the ``examiner`` role and
    expires at ``min(session.expires_at, now + 15m)``. Audited as
    ``inspector.act_token.mint`` — the token itself is NEVER logged.
    """
    impersonation_secret = get_settings().auth.impersonation_jwt_secret
    if not impersonation_secret:
        # Fail closed: no secret → no impersonation is possible anywhere.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Act-as-examiner is not configured: set IMPERSONATION_JWT_SECRET "
                "on the platform to enable operator impersonation."
            ),
        )

    row = inspector.get_session(db, session_id)  # 404 if unknown
    if row.started_by != operator.email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the operator who opened this session may act as examiner in it.",
        )
    now = utc_now()
    # SQLite hands back naive datetimes; normalize to aware UTC so the window
    # arithmetic below never trips over a naive/aware comparison (Postgres rows
    # are already aware — the guard is a no-op there).
    session_expires_at = (
        row.expires_at
        if row.expires_at.tzinfo is not None
        else row.expires_at.replace(tzinfo=UTC)
    )
    if row.ended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This inspector session has ended.",
        )
    if session_expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This inspector session has expired.",
        )

    expires_at = min(session_expires_at, now + ACT_TOKEN_MAX_TTL)
    act_token = security.mint_impersonation_token(
        organization_id=row.organization_id,
        act_operator=operator.email,
        session_id=str(row.id),
        secret=impersonation_secret,
        issued_at=now,
        expires_at=expires_at,
    )
    record_operator_action(
        db,
        operator,
        action="inspector.act_token.mint",
        target_org=row.organization_id,
        # Never the token — only its provenance/expiry.
        detail={
            "session_id": str(row.id),
            "role": "examiner",
            "expires_at": expires_at.isoformat(),
        },
    )
    db.commit()
    return InspectorActTokenRead(
        act_token=act_token,
        expires_at=expires_at,
        dashboard_url=get_operator_settings().bank_app_base_url,
    )
