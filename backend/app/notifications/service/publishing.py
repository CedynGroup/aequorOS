"""``publish``: the one way a feature tells people something (backend/docs/notifications.md).

A producer names a catalogue event, says what happened as a structured
:class:`~app.notifications.rendering.Message`, and lists who should hear it.
Inside the caller's transaction this writes:

* one ``notifications`` row per recipient — the in-app feed and the record;
* one ``notification_deliveries`` row per recipient whose preferences want email,
  due now (immediate) or at the organisation's next summary hour (digest);
* a ``notification_dispatch`` job, pulled forward if one is already waiting.

Nothing is sent here. The dispatcher reads committed rows, so a business action
that rolls back never mails anyone, and a mail outage never fails the action.

``dedupe_key`` makes scheduled producers (reminders, escalations, the regulator
sync) safe to re-run: a recipient who already has a row for
``<event>:<dedupe_key>`` is skipped.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import InstitutionScope, Module, Sensitivity
from app.identity.public import User
from app.notifications import catalog
from app.notifications.catalog import EmailMode, EventDefinition, Severity
from app.notifications.models import Notification, NotificationDelivery
from app.notifications.rendering import Message
from app.notifications.service import dispatch, preferences
from app.services import mailer

#: Display-safe ceiling for the stored title (column width).
_TITLE_LIMIT = 200


def notification_type(event: EventDefinition, dedupe_key: str | None) -> str:
    """The stored ``type``: the event key, suffixed with the dedupe key when given."""
    return event.key if dedupe_key is None else f"{event.key}:{dedupe_key}"[:120]


def _eligible_users(db: Session, organization_id: str, user_ids: Sequence[UUID]) -> list[User]:
    """Active humans of this organisation, in the order asked for, without duplicates."""
    if not user_ids:
        return []
    rows = {
        user.id: user
        for user in db.scalars(
            select(User).where(
                User.organization_id == organization_id,
                User.id.in_(list(user_ids)),
                User.is_active.is_(True),
            )
        )
        if user.auth_provider != "service"
    }
    return [rows[user_id] for user_id in dict.fromkeys(user_ids) if user_id in rows]


def _already_notified(
    db: Session, organization_id: str, type_: str, user_ids: Iterable[UUID]
) -> set[UUID]:
    ids = list(user_ids)
    if not ids:
        return set()
    return {
        recipient
        for recipient in db.scalars(
            select(Notification.recipient_user_id).where(
                Notification.organization_id == organization_id,
                Notification.type == type_,
                Notification.recipient_user_id.in_(ids),
            )
        )
        if recipient is not None
    }


def publish(  # noqa: PLR0913 - a notification is exactly these named parts
    db: Session,
    ctx: TenantContext,
    *,
    event: EventDefinition,
    message: Message,
    recipients: Iterable[UUID],
    module: Module,
    sensitivity: Sensitivity,
    bank_id: str | None = None,
    entity_type: str | None = None,
    entity_id: UUID | str | None = None,
    severity: Severity | None = None,
    dedupe_key: str | None = None,
    exclude: Iterable[UUID] = (),
    now: datetime | None = None,
) -> list[Notification]:
    """Notify named people about one event (no commit; the caller's transaction owns it).

    ``exclude`` removes people who caused the event — the officer who sent a
    return back does not need an email telling them they sent it back.
    Recipients who are inactive, service accounts or outside the organisation are
    dropped silently: a producer resolves recipients from authority, and
    authority can change between the decision and the notice.
    """
    catalog.get(event.key)  # refuse an event the catalogue does not declare
    moment = now or datetime.now(UTC)
    excluded = set(exclude)
    wanted = [user_id for user_id in recipients if user_id not in excluded]
    users = _eligible_users(db, ctx.organization_id, wanted)
    type_ = notification_type(event, dedupe_key)
    if dedupe_key is not None:
        skip = _already_notified(db, ctx.organization_id, type_, (user.id for user in users))
        users = [user for user in users if user.id not in skip]
    if not users:
        return []
    rows = [
        _feed_row(
            ctx,
            event=event,
            type_=type_,
            message=message,
            recipient=user.id,
            severity=severity or event.severity,
            module=module,
            sensitivity=sensitivity,
            bank_id=bank_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        for user in users
    ]
    db.add_all(rows)
    db.flush()
    queue_email(db, ctx.organization_id, event, list(zip(rows, users, strict=True)), now=moment)
    return rows


def _feed_row(  # noqa: PLR0913 - mirrors the column set
    ctx: TenantContext,
    *,
    event: EventDefinition,
    type_: str,
    message: Message,
    recipient: UUID | None,
    severity: Severity,
    module: Module,
    sensitivity: Sensitivity,
    bank_id: str | None,
    entity_type: str | None,
    entity_id: UUID | str | None,
) -> Notification:
    scope = InstitutionScope.INSTITUTION if bank_id is not None else InstitutionScope.ORGANIZATION
    return Notification(
        organization_id=ctx.organization_id,
        recipient_user_id=recipient,
        type=type_,
        event_key=event.key,
        severity=severity.value,
        title=message.title[:_TITLE_LIMIT],
        body=message.feed_body(),
        data=message.to_data(),
        action_path=None if message.action is None else message.action.path,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        institution_scope=scope.value,
        bank_id=bank_id,
        module=module.value,
        sensitivity=sensitivity.value,
    )


def queue_email(
    db: Session,
    organization_id: str,
    event: EventDefinition,
    targets: Sequence[tuple[Notification, User]],
    *,
    now: datetime,
) -> int:
    """Create the email deliveries ``targets`` are owed and schedule dispatch.

    Returns how many deliveries were queued. A deployment with no relay queues
    nothing: the feed still has the notification, and enabling mail later does
    not flood people with a backlog they already read in the app.
    """
    if not targets or not mailer.relay_configured():
        return 0
    org, modes = preferences.resolve_for_recipients(
        db, organization_id, event, (user.id for _row, user in targets)
    )
    digest_at = org.next_digest_at(now)
    earliest: datetime | None = None
    queued = 0
    for row, user in targets:
        mode = modes.get(user.id, EmailMode.OFF)
        if mode is EmailMode.OFF or not user.email:
            continue
        due = now if mode is EmailMode.IMMEDIATE else digest_at
        db.add(
            NotificationDelivery(
                organization_id=organization_id,
                notification_id=row.id,
                recipient_user_id=user.id,
                channel="email",
                address=user.email,
                mode=mode.value,
                status="pending",
                attempts=0,
                next_attempt_at=due,
            )
        )
        queued += 1
        earliest = due if earliest is None or due < earliest else earliest
    if earliest is not None:
        db.flush()
        dispatch.schedule(db, organization_id, earliest)
        logger.debug(
            "queued {} {} email(s) for {}; first due {}",
            queued,
            event.key,
            organization_id,
            earliest.isoformat(),
        )
    return queued


__all__ = ["notification_type", "publish", "queue_email"]
