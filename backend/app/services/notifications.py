"""In-app notification service (docs/submission_pipeline_plan.md §W3).

``emit`` writes rows inside the caller's transaction — a notification is
atomic with the state change that caused it (the caller commits). Role
fan-out targets every active user holding the role *or higher* per the
``app.core.security.ROLES`` hierarchy; ``recipient_user_id=None`` with no
role means one org-wide row every user in the tenant can see.

Read-side visibility: a user sees rows addressed to them plus org-wide rows.
``mark_read``/``mark_all_read`` only ever touch rows visible to the actor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.security import ROLES
from app.models import Notification, User


def _roles_at_or_above(role: str) -> tuple[str, ...]:
    """Roles at least as privileged as ``role`` (ROLES is most- to least-)."""
    if role not in ROLES:
        msg = f"Unknown recipient role {role!r}; expected one of {ROLES}."
        raise ValueError(msg)
    return ROLES[: ROLES.index(role) + 1]


def emit(  # noqa: PLR0913 - a notification carries the full display envelope
    db: Session,
    ctx: TenantContext,
    *,
    type: str,  # noqa: A002 - mirrors the column name
    severity: str,
    title: str,
    body: str,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    recipient_user_id: UUID | None = None,
    recipient_role: str | None = None,
) -> list[Notification]:
    """Add notification rows to the caller's transaction (no commit).

    - ``recipient_role``: one row per active user in the org holding that role
      or higher (maker-checker queues, regulator outcomes).
    - ``recipient_user_id``: one row for that user.
    - neither: one org-wide row (``recipient_user_id`` NULL).
    """
    if recipient_role is not None:
        recipient_ids: list[UUID | None] = list(
            db.scalars(
                select(User.id).where(
                    User.organization_id == ctx.organization_id,
                    User.is_active.is_(True),
                    User.role.in_(_roles_at_or_above(recipient_role)),
                )
            )
        )
    else:
        recipient_ids = [recipient_user_id]
    rows = [
        Notification(
            organization_id=ctx.organization_id,
            recipient_user_id=recipient,
            type=type,
            severity=severity,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        for recipient in recipient_ids
    ]
    db.add_all(rows)
    db.flush()
    return rows


def _visibility_conditions(ctx: TenantContext) -> tuple[ColumnElement[bool], ...]:
    """Rows the acting user may see: their own plus org-wide, tenant-scoped."""
    if ctx.actor_user_id is None:
        recipient = Notification.recipient_user_id.is_(None)
    else:
        recipient = or_(
            Notification.recipient_user_id.is_(None),
            Notification.recipient_user_id == ctx.actor_user_id,
        )
    return (Notification.organization_id == ctx.organization_id, recipient)


def list_notifications(
    db: Session,
    ctx: TenantContext,
    *,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int, int]:
    """Visible notifications, newest first: ``(rows, total, unread_count)``.

    ``total`` counts the listed set (honoring ``unread_only``) for pagination;
    ``unread_count`` always counts every visible unread row (badge value).
    """
    visible = _visibility_conditions(ctx)
    listed = (*visible, Notification.read_at.is_(None)) if unread_only else visible
    rows = list(
        db.scalars(
            select(Notification)
            .where(*listed)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    total = db.scalar(select(func.count()).select_from(Notification).where(*listed)) or 0
    unread_count = (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(*visible, Notification.read_at.is_(None))
        )
        or 0
    )
    return rows, total, unread_count


def mark_read(db: Session, ctx: TenantContext, notification_id: UUID) -> Notification:
    """Set ``read_at`` on one visible notification (idempotent) and commit."""
    row = db.scalar(
        select(Notification).where(Notification.id == notification_id, *_visibility_conditions(ctx))
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
    db.commit()
    return row


def mark_all_read(db: Session, ctx: TenantContext) -> int:
    """Set ``read_at`` on every visible unread notification; returns the count."""
    rows = list(
        db.scalars(
            select(Notification).where(*_visibility_conditions(ctx), Notification.read_at.is_(None))
        )
    )
    now = datetime.now(UTC)
    for row in rows:
        row.read_at = now
    db.commit()
    return len(rows)
