"""In-app notification feed API (docs/submission_pipeline_plan.md §W3).

The feed a signed-in user sees is their own rows plus org-wide rows
(``recipient_user_id`` NULL); emission happens service-side inside the
transactions that cause it (reporting workflow, deadline scan) — there is no
create endpoint. Mark-read only ever touches rows visible to the actor.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.notifications import (
    NotificationListRead,
    NotificationRead,
    NotificationsMarkAllRead,
)
from app.services import notifications

router = APIRouter(tags=["notifications"])


@router.get(
    "/notifications",
    response_model=NotificationListRead,
    operation_id="listNotifications",
)
def list_notifications(
    db: DbSession,
    ctx: Tenant,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationListRead:
    rows, total, unread_count = notifications.list_notifications(
        db, ctx, unread_only=unread_only, limit=limit, offset=offset
    )
    return NotificationListRead(
        notifications=[NotificationRead.model_validate(row) for row in rows],
        total=total,
        unread_count=unread_count,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationRead,
    operation_id="markNotificationRead",
)
def mark_notification_read(
    notification_id: UUID, db: DbSession, ctx: MutationTenant
) -> NotificationRead:
    return NotificationRead.model_validate(notifications.mark_read(db, ctx, notification_id))


@router.post(
    "/notifications/read-all",
    response_model=NotificationsMarkAllRead,
    operation_id="markAllNotificationsRead",
)
def mark_all_notifications_read(db: DbSession, ctx: MutationTenant) -> NotificationsMarkAllRead:
    return NotificationsMarkAllRead(marked=notifications.mark_all_read(db, ctx))
