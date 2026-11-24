"""Notification preferences, organisation policy and the delivery log.

* ``/notifications/preferences`` — the signed-in person's own email choices.
* ``/notifications/settings`` — the organisation's delivery settings and
  per-event policy; account administrators only.
* ``/notifications/deliveries`` — the delivery log and manual retry; account
  administrators only. A delivery's notice title is shown only when the
  administrator's own feed would show that notice.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    DbSession,
    ScopedMutationTenant,
    Tenant,
    TenantContext,
    require_account_administration,
)
from app.notifications.schemas import (
    DeliveryStatus,
    NotificationDeliveryListRead,
    NotificationDeliveryRead,
    NotificationOrgSettingsRead,
    NotificationOrgSettingsUpdate,
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
)
from app.notifications.service import views

router = APIRouter(tags=["notifications"])

AccountAdministrator = Annotated[TenantContext, Depends(require_account_administration)]


@router.get(
    "/notifications/preferences",
    response_model=NotificationPreferencesRead,
    operation_id="getNotificationPreferences",
)
def get_notification_preferences(db: DbSession, ctx: Tenant) -> NotificationPreferencesRead:
    return views.my_preferences(db, ctx)


@router.put(
    "/notifications/preferences",
    response_model=NotificationPreferencesRead,
    operation_id="updateNotificationPreferences",
)
def update_notification_preferences(
    payload: NotificationPreferencesUpdate, db: DbSession, ctx: ScopedMutationTenant
) -> NotificationPreferencesRead:
    return views.update_my_preferences(db, ctx, payload)


@router.get(
    "/notifications/settings",
    response_model=NotificationOrgSettingsRead,
    operation_id="getNotificationSettings",
)
def get_notification_settings(
    db: DbSession, ctx: AccountAdministrator
) -> NotificationOrgSettingsRead:
    return views.org_settings(db, ctx)


@router.put(
    "/notifications/settings",
    response_model=NotificationOrgSettingsRead,
    operation_id="updateNotificationSettings",
)
def update_notification_settings(
    payload: NotificationOrgSettingsUpdate, db: DbSession, ctx: AccountAdministrator
) -> NotificationOrgSettingsRead:
    return views.update_org_settings(db, ctx, payload)


@router.get(
    "/notifications/deliveries",
    response_model=NotificationDeliveryListRead,
    operation_id="listNotificationDeliveries",
)
def list_notification_deliveries(
    db: DbSession,
    ctx: AccountAdministrator,
    status: Annotated[DeliveryStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationDeliveryListRead:
    return views.delivery_log(db, ctx, status_filter=status, limit=limit, offset=offset)


@router.post(
    "/notifications/deliveries/{delivery_id}/retry",
    response_model=NotificationDeliveryRead,
    operation_id="retryNotificationDelivery",
)
def retry_notification_delivery(
    delivery_id: UUID, db: DbSession, ctx: AccountAdministrator
) -> NotificationDeliveryRead:
    return views.retry_delivery(db, ctx, delivery_id)
