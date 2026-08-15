"""Tenant-inspector session tracking (staff_UI.md tenant inspector).

READ-ONLY session TRACKING, not an access grant. Starting a session mints NO
tenant token and NO act-as-user claim — the console renders tenant data through
the operator read views, and the ``operator_inspector_sessions`` row (plus its
``operator_audit_log`` entries and the UI banner) is the diligence control that
records WHO viewed WHICH tenant, WHEN, WHY, and under which mode. True
act-as-user sign-in is deliberately out of scope (separate security review), so
``read_only`` is always true this wave.

Every query here runs on the operator's BYPASSRLS session and is scoped
explicitly by ``organization_id`` where a tenant is named — the cross-tenant
read discipline the whole operator model rests on.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import OperatorInspectorSession, Organization
from app.schemas.operator import InspectorSessionListRead, InspectorSessionRead


def _require_organization(db: Session, organization_id: str) -> Organization:
    organization = db.scalar(select(Organization).where(Organization.id == organization_id))
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found."
        )
    return organization


def session_read(row: OperatorInspectorSession) -> InspectorSessionRead:
    return InspectorSessionRead(
        session_id=str(row.id),
        organization_id=row.organization_id,
        mode=row.mode,  # type: ignore[arg-type]  # DB-constrained to the literal set
        read_only=row.read_only,
        started_by=row.started_by,
        started_at=row.started_at,
        expires_at=row.expires_at,
        reason=row.reason,
        ended_at=row.ended_at,
        ended_by=row.ended_by,
    )


def start_session(  # noqa: PLR0913 - one argument per session attribute
    db: Session,
    *,
    organization_id: str,
    started_by: str,
    mode: str,
    reason: str,
    ttl_minutes: int,
) -> OperatorInspectorSession:
    """Open a READ-ONLY inspection window against a tenant (404 if unknown org).

    Caller (the feature layer) owns the commit and the audit row; this only
    stages the session. ``read_only`` is forced true — no path this wave sets
    it otherwise."""
    _require_organization(db, organization_id)
    now = utc_now()
    row = OperatorInspectorSession(
        organization_id=organization_id,
        started_by=started_by,
        mode=mode,
        reason=reason,
        read_only=True,
        started_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    db.add(row)
    db.flush()
    return row


def list_sessions(
    db: Session,
    *,
    active: bool | None = None,
    organization_id: str | None = None,
    limit: int = 100,
) -> InspectorSessionListRead:
    """Audit surface: sessions newest-first. ``active`` selects sessions that are
    neither ended nor expired (or, when False, those that are)."""
    filters = []
    if organization_id is not None:
        filters.append(OperatorInspectorSession.organization_id == organization_id)
    if active is not None:
        now = utc_now()
        is_active = (OperatorInspectorSession.ended_at.is_(None)) & (
            OperatorInspectorSession.expires_at > now
        )
        filters.append(is_active if active else ~is_active)
    total = (
        db.scalar(
            select(func.count()).select_from(OperatorInspectorSession).where(*filters)
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(OperatorInspectorSession)
            .where(*filters)
            .order_by(
                OperatorInspectorSession.started_at.desc(),
                OperatorInspectorSession.id.desc(),
            )
            .limit(limit)
        )
    )
    return InspectorSessionListRead(
        sessions=[session_read(row) for row in rows], total=total
    )


def get_session(db: Session, session_id: Any) -> OperatorInspectorSession:
    row = db.get(OperatorInspectorSession, session_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Inspector session not found."
        )
    return row


def end_session(
    db: Session, session_id: Any, *, ended_by: str
) -> OperatorInspectorSession:
    """Close a session (idempotent: a first close stamps ``ended_at``/``ended_by``;
    re-closing leaves the original stamp untouched). Caller owns commit/audit."""
    row = get_session(db, session_id)
    if row.ended_at is None:
        row.ended_at = utc_now()
        row.ended_by = ended_by
        db.flush()
    return row
