"""Read models and writes behind the notification settings screens.

The routes in ``app/notifications/api/manage_preferences.py`` are thin: each
calls one function here and commits. Every view is built from the catalogue,
so a new event appears on both screens the moment it is declared.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.identity.public import User
from app.notifications import catalog
from app.notifications.catalog import CATEGORY_LABELS, Category, EmailMode
from app.notifications.models import Notification, NotificationDelivery
from app.notifications.schemas import (
    DeliveryStatus,
    NotificationCategoryRead,
    NotificationDeliveryListRead,
    NotificationDeliveryRead,
    NotificationEventPreferenceRead,
    NotificationOrgSettingsRead,
    NotificationOrgSettingsUpdate,
    NotificationPolicyRead,
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
)
from app.notifications.service import dispatch, notifications, preferences
from app.services import mailer


def _actor(ctx: TenantContext) -> UUID:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Notification settings belong to a signed-in person.",
        )
    return ctx.actor_user_id


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


# ---------------------------------------------------------------------------
# A person's own preferences
# ---------------------------------------------------------------------------


def my_preferences(db: Session, ctx: TenantContext) -> NotificationPreferencesRead:
    user_id = _actor(ctx)
    org = preferences.org_delivery(db, ctx.organization_id)
    policy_rows = preferences.policies(db, ctx.organization_id)
    choices = preferences.user_choices(db, ctx.organization_id, [user_id])
    categories: list[NotificationCategoryRead] = []
    for category in Category:
        events: list[NotificationEventPreferenceRead] = []
        for event in catalog.EVENTS.values():
            if event.category is not category:
                continue
            policy = policy_rows.get(event.key)
            choice = choices.get((user_id, event.key))
            locked_by = (
                "platform"
                if event.email_locked
                else "organisation"
                if policy is not None and policy.locked
                else None
            )
            events.append(
                NotificationEventPreferenceRead(
                    key=event.key,
                    label=event.label,
                    description=event.description,
                    default_email=(
                        EmailMode(policy.email_mode) if policy is not None else event.default_email
                    ).value,
                    email_mode=preferences.resolve_email_mode(
                        event, org=org, policy=policy, choice=choice
                    ).value,
                    choice=None if choice is None or locked_by else choice.value,
                    locked_by=locked_by,
                )
            )
        if events:
            categories.append(
                NotificationCategoryRead(
                    key=category.value, label=CATEGORY_LABELS[category], events=events
                )
            )
    return NotificationPreferencesRead(
        email_configured=mailer.relay_configured(),
        email_enabled=org.email_enabled,
        digest_hour=org.digest_hour,
        time_zone=org.time_zone,
        categories=categories,
    )


def update_my_preferences(
    db: Session, ctx: TenantContext, payload: NotificationPreferencesUpdate
) -> NotificationPreferencesRead:
    user_id = _actor(ctx)
    try:
        preferences.set_user_choices(
            db,
            ctx.organization_id,
            user_id,
            {
                key: None if mode is None else EmailMode(mode)
                for key, mode in payload.choices.items()
            },
        )
    except catalog.UnknownEvent as exc:
        raise _bad_request(ValueError(f"Unknown notification “{exc.args[0]}”.")) from exc
    except preferences.PreferenceError as exc:
        raise _bad_request(exc) from exc
    db.commit()
    return my_preferences(db, ctx)


# ---------------------------------------------------------------------------
# The organisation's settings and policy (account administrators)
# ---------------------------------------------------------------------------


def org_settings(db: Session, ctx: TenantContext) -> NotificationOrgSettingsRead:
    org = preferences.org_delivery(db, ctx.organization_id)
    policy_rows = preferences.policies(db, ctx.organization_id)
    policies: list[NotificationPolicyRead] = []
    for event in catalog.EVENTS.values():
        row = policy_rows.get(event.key)
        policies.append(
            NotificationPolicyRead(
                key=event.key,
                label=event.label,
                description=event.description,
                category=CATEGORY_LABELS[event.category],
                default_email=event.default_email.value,
                platform_locked=event.email_locked,
                email_mode=None if row is None else EmailMode(row.email_mode).value,
                locked=False if row is None else row.locked,
            )
        )
    return NotificationOrgSettingsRead(
        email_configured=mailer.relay_configured(),
        email_enabled=org.email_enabled,
        time_zone=org.time_zone,
        digest_hour=org.digest_hour,
        reminders_enabled=org.reminders_enabled,
        reminder_after_hours=org.reminder_after_hours,
        escalate_after_hours=org.escalate_after_hours,
        policies=policies,
    )


def update_org_settings(
    db: Session, ctx: TenantContext, payload: NotificationOrgSettingsUpdate
) -> NotificationOrgSettingsRead:
    actor = _actor(ctx)
    try:
        preferences.save_org_delivery(
            db,
            ctx.organization_id,
            actor,
            preferences.OrgDelivery(
                email_enabled=payload.email_enabled,
                time_zone=payload.time_zone.strip(),
                digest_hour=payload.digest_hour,
                reminders_enabled=payload.reminders_enabled,
                reminder_after_hours=payload.reminder_after_hours,
                escalate_after_hours=payload.escalate_after_hours,
            ),
        )
        preferences.set_policies(
            db,
            ctx.organization_id,
            actor,
            {
                key: None
                if change is None
                else preferences.PolicyChange(EmailMode(change.email_mode), change.locked)
                for key, change in payload.policies.items()
            },
        )
    except catalog.UnknownEvent as exc:
        raise _bad_request(ValueError(f"Unknown notification “{exc.args[0]}”.")) from exc
    except preferences.PreferenceError as exc:
        raise _bad_request(exc) from exc
    db.commit()
    return org_settings(db, ctx)


# ---------------------------------------------------------------------------
# The delivery log (account administrators)
# ---------------------------------------------------------------------------


def delivery_log(
    db: Session,
    ctx: TenantContext,
    *,
    status_filter: DeliveryStatus | None,
    limit: int,
    offset: int,
) -> NotificationDeliveryListRead:
    conditions = [NotificationDelivery.organization_id == ctx.organization_id]
    if status_filter is not None:
        conditions.append(NotificationDelivery.status == status_filter)
    rows = list(
        db.execute(
            select(NotificationDelivery, Notification, User)
            .join(
                Notification,
                (Notification.id == NotificationDelivery.notification_id)
                & (Notification.organization_id == NotificationDelivery.organization_id),
            )
            .join(
                User,
                (User.id == NotificationDelivery.recipient_user_id)
                & (User.organization_id == NotificationDelivery.organization_id),
            )
            .where(*conditions)
            .order_by(NotificationDelivery.created_at.desc(), NotificationDelivery.id.desc())
            .limit(limit)
            .offset(offset)
        ).tuples()
    )
    total = (
        db.scalar(select(func.count()).select_from(NotificationDelivery).where(*conditions)) or 0
    )
    counts = {
        row_status: count
        for row_status, count in db.execute(
            select(NotificationDelivery.status, func.count())
            .where(NotificationDelivery.organization_id == ctx.organization_id)
            .group_by(NotificationDelivery.status)
        ).tuples()
    }
    # A title discloses what the notice is about; the administrator sees it only
    # for notices their own feed would show them.
    visible_ids = set(
        db.scalars(
            select(Notification.id).where(
                Notification.id.in_([notification.id for _d, notification, _u in rows]),
                *_visible_to_admin(db, ctx),
            )
        )
    )
    return NotificationDeliveryListRead(
        deliveries=[
            _delivery_read(delivery, notification, user, visible=notification.id in visible_ids)
            for delivery, notification, user in rows
        ],
        total=total,
        counts=counts,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


def _visible_to_admin(db: Session, ctx: TenantContext) -> tuple[ColumnElement[bool], ...]:
    # The feed's own rule, minus the recipient factor: an administrator reviews
    # deliveries addressed to other people.
    conditions = notifications.visibility_conditions(db, ctx)
    return (conditions[0], *conditions[2:])


def _event(notification: Notification) -> catalog.EventDefinition:
    if notification.event_key and notification.event_key in catalog.EVENTS:
        return catalog.EVENTS[notification.event_key]
    return catalog.for_type(notification.type)


def _status(value: str) -> DeliveryStatus:
    if value == "sent":
        return "sent"
    if value == "dead":
        return "dead"
    if value == "suppressed":
        return "suppressed"
    return "pending"


def _delivery_read(
    delivery: NotificationDelivery, notification: Notification, user: User, *, visible: bool
) -> NotificationDeliveryRead:
    event = _event(notification)
    return NotificationDeliveryRead(
        id=delivery.id,
        event_key=event.key,
        event_label=event.label,
        title=notification.title if visible else None,
        recipient_user_id=delivery.recipient_user_id,
        recipient_name=user.display_name or user.email,
        address=delivery.address,
        mode="digest" if delivery.mode == "digest" else "immediate",
        status=_status(delivery.status),
        attempts=delivery.attempts,
        last_error=delivery.last_error,
        next_attempt_at=delivery.next_attempt_at,
        sent_at=delivery.sent_at,
        created_at=delivery.created_at,
    )


def retry_delivery(db: Session, ctx: TenantContext, delivery_id: UUID) -> NotificationDeliveryRead:
    """Send a dead or suppressed delivery again, now."""
    row = dispatch.retry_now(db, ctx.organization_id, delivery_id, now=datetime.now(UTC))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found.")
    db.commit()
    notification = db.get(Notification, row.notification_id)
    user = db.get(User, row.recipient_user_id)
    if notification is None or user is None:  # pragma: no cover - FK-guaranteed
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found.")
    visible = (
        db.scalar(
            select(Notification.id).where(
                Notification.id == notification.id, *_visible_to_admin(db, ctx)
            )
        )
        is not None
    )
    return _delivery_read(row, notification, user, visible=visible)


__all__ = [
    "delivery_log",
    "my_preferences",
    "org_settings",
    "retry_delivery",
    "update_my_preferences",
    "update_org_settings",
]
