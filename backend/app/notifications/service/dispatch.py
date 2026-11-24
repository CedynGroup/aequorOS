"""The email dispatcher: drains due deliveries, retries, digests (backend/docs/notifications.md).

``notification_dispatch`` runs per organisation. Each run claims the deliveries
that are due (``FOR UPDATE SKIP LOCKED``, so two workers never send the same
row), and for each one:

* re-checks, at send time, that the recipient is still active, still has an
  address, may still see the notification under the same rules as the in-app
  feed, and passes the deployment's allowlist — otherwise the row is
  ``suppressed`` with the reason, never silently dropped;
* sends it — one email per immediate delivery, one summary email per person for
  all of their due digest deliveries;
* records the outcome: ``sent``; a temporary failure stays ``pending`` with the
  error and a later ``next_attempt_at`` (1 min, 5 min, 15 min, 1 h, 4 h, 12 h);
  a permanent refusal (unknown mailbox) or the last attempt becomes ``dead``.

Then it schedules its own next run for the earliest remaining due time, so
retries and the morning digest need no hourly tick. Delivery is at-least-once:
each send commits immediately, and only a crash between the relay accepting a
message and that commit can repeat it.
"""

from __future__ import annotations

import smtplib
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from email.utils import make_msgid
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.identity.public import User
from app.models.job import Job
from app.models.organization import Organization
from app.notifications import catalog
from app.notifications.catalog import Severity
from app.notifications.models import Notification, NotificationDelivery
from app.notifications.rendering import (
    Branding,
    DigestItem,
    Message,
    RenderedEmail,
    render_digest,
    render_email,
)
from app.services import job_queue, mailer

JOB_TYPE = "notification_dispatch"

#: Wait before attempt n+1 after n failures. The last entry repeats.
RETRY_DELAYS: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=4),
    timedelta(hours=12),
)
#: A delivery left pending this long since it was last touched is not worth sending:
#: the person has read it in the app, or it is about something long resolved.
EXPIRE_AFTER = timedelta(days=3)
_ERROR_LIMIT = 500


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def schedule(db: Session, organization_id: str, due_at: datetime) -> None:
    """Make sure a dispatch run is queued no later than ``due_at``.

    ``job_queue.enqueue``'s coalescing keeps the LATER of two times (right for
    debouncing a refresh, wrong for "send as soon as possible"), so this pulls
    an already-queued run earlier itself instead of coalescing.
    """
    queued = db.scalars(
        select(Job)
        .where(
            Job.organization_id == organization_id,
            Job.job_type == JOB_TYPE,
            Job.status == "queued",
            Job.started_at.is_(None),
        )
        .order_by(Job.run_after)
    ).first()
    if queued is not None:
        current = _aware(queued.run_after)
        if current is None or current > due_at:
            queued.run_after = due_at
        return
    job_queue.enqueue(
        db,
        organization_id,
        JOB_TYPE,
        payload={},
        run_after=due_at,
        max_attempts=5,
        locator=None,
        provenance=job_queue.worker_provenance("worker.notification_dispatch"),
    )


def ensure_scheduled(db: Session, organization_id: str, *, now: datetime) -> bool:
    """Heal the dispatch chain: schedule a run if a pending delivery has none waiting.

    Returns whether anything was pending. Called by the hourly tick; publishing
    and retries normally schedule their own runs, so this only matters when a
    dispatch job was lost.
    """
    earliest = db.scalar(
        select(func.min(NotificationDelivery.next_attempt_at)).where(
            NotificationDelivery.organization_id == organization_id,
            NotificationDelivery.status == "pending",
        )
    )
    aware = _aware(earliest)
    if aware is None:
        return False
    schedule(db, organization_id, max(aware, now))
    return True


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# The worker handler
# ---------------------------------------------------------------------------


class _Outcome:
    __slots__ = ("dead", "expired", "retrying", "sent", "suppressed")

    def __init__(self) -> None:
        self.sent = 0
        self.retrying = 0
        self.dead = 0
        self.suppressed = 0
        self.expired = 0

    def as_progress(self) -> dict[str, int]:
        return {
            "sent": self.sent,
            "retrying": self.retrying,
            "dead": self.dead,
            "suppressed": self.suppressed,
            "expired": self.expired,
        }


def run_notification_dispatch(session: Session, job: Job, *, now: datetime | None = None) -> None:
    """Worker handler: send this organisation's due email deliveries."""
    moment = now or datetime.now(UTC)
    if not mailer.relay_configured():
        job.progress = {"skipped": "smtp_disabled"}
        return
    settings = get_settings().notifications
    outcome = _Outcome()
    due = list(
        session.scalars(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.organization_id == job.organization_id,
                NotificationDelivery.status == "pending",
                NotificationDelivery.next_attempt_at <= moment,
            )
            .order_by(NotificationDelivery.next_attempt_at)
            .limit(settings.dispatch_batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    if due:
        _drain(session, job.organization_id, due, moment, outcome)
    _reschedule(
        session, job.organization_id, moment, full_batch=len(due) >= settings.dispatch_batch_size
    )
    job.progress = outcome.as_progress()
    session.commit()


def _drain(
    session: Session,
    organization_id: str,
    due: Sequence[NotificationDelivery],
    now: datetime,
    outcome: _Outcome,
) -> None:
    notifications_by_id = {
        row.id: row
        for row in session.scalars(
            select(Notification).where(
                Notification.organization_id == organization_id,
                Notification.id.in_([delivery.notification_id for delivery in due]),
            )
        )
    }
    users = {
        user.id: user
        for user in session.scalars(
            select(User).where(
                User.organization_id == organization_id,
                User.id.in_([delivery.recipient_user_id for delivery in due]),
            )
        )
    }
    organization = session.get(Organization, organization_id)
    branding = Branding(
        app_base_url=get_settings().notifications.bank_app_base_url,
        organization_name=None if organization is None else organization.name,
    )
    immediate: list[NotificationDelivery] = []
    digests: dict[UUID, list[NotificationDelivery]] = defaultdict(list)
    for delivery in due:
        reason = _suppression_reason(session, delivery, users, notifications_by_id, now)
        if reason is not None:
            _finish(delivery, "suppressed" if reason != "expired" else "dead", reason, now)
            if reason == "expired":
                outcome.expired += 1
            else:
                outcome.suppressed += 1
            continue
        if delivery.mode == "digest":
            digests[delivery.recipient_user_id].append(delivery)
        else:
            immediate.append(delivery)
    session.flush()
    if not immediate and not digests:
        return
    try:
        with mailer.open_relay() as relay:
            for delivery in immediate:
                notification = notifications_by_id[delivery.notification_id]
                user = users[delivery.recipient_user_id]
                rendered = _render_one(notification, user, branding)
                _send(session, relay, [delivery], user.email, rendered, now, outcome)
            for user_id, group in digests.items():
                user = users[user_id]
                rendered = _render_digest(group, notifications_by_id, user, branding, now)
                _send(session, relay, group, user.email, rendered, now, outcome)
    except mailer.TRANSPORT_ERRORS as exc:
        # The relay itself could not be reached or refused the login: nothing in
        # this batch was attempted past the failure point. Each untouched row
        # keeps its attempt count and is retried on the first backoff step.
        logger.warning("notification relay unavailable: {}", exc)
        for delivery in [*immediate, *(row for group in digests.values() for row in group)]:
            if delivery.status == "pending" and delivery.sent_at is None:
                _retry_or_bury(delivery, exc, now, outcome)
        session.commit()


def _suppression_reason(
    session: Session,
    delivery: NotificationDelivery,
    users: dict[UUID, User],
    notifications_by_id: dict[UUID, Notification],
    now: datetime,
) -> str | None:
    # ``updated_at`` moves on every attempt and on a manual retry, so this
    # expires deliveries nobody has touched for EXPIRE_AFTER — never a retry.
    touched = _aware(delivery.updated_at) or now
    if now - touched > EXPIRE_AFTER:
        return "expired"
    user = users.get(delivery.recipient_user_id)
    notification = notifications_by_id.get(delivery.notification_id)
    if user is None or notification is None or not user.is_active:
        return "recipient_inactive"
    if not user.email:
        return "no_address"
    if not _allowed(user.email):
        return "not_on_allowlist"
    from app.notifications.service import notifications  # noqa: PLC0415 - imports reporting

    visible = session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.id == notification.id,
            *notifications.visibility_conditions(
                session,
                TenantContext(
                    organization_id=user.organization_id,
                    actor_user_id=user.id,
                    authorization_version=user.authorization_version,
                ),
            ),
        )
    )
    if not visible:
        return "recipient_lost_access"
    return None


def _allowed(address: str) -> bool:
    entries = get_settings().notifications.allowlist_entries()
    if not entries:
        return True
    lowered = address.lower()
    domain = "@" + lowered.rsplit("@", 1)[-1]
    return lowered in entries or domain in entries


def _render_one(notification: Notification, user: User, branding: Branding) -> RenderedEmail:
    event = (
        catalog.EVENTS.get(notification.event_key)
        if notification.event_key
        else catalog.for_type(notification.type)
    ) or catalog.for_type(notification.type)
    return render_email(
        _message_of(notification),
        event=event,
        severity=_severity(notification.severity),
        branding=branding,
        recipient_name=user.display_name,
    )


def _render_digest(
    group: Sequence[NotificationDelivery],
    notifications_by_id: dict[UUID, Notification],
    user: User,
    branding: Branding,
    now: datetime,
) -> RenderedEmail:
    items: list[DigestItem] = []
    for delivery in group:
        notification = notifications_by_id[delivery.notification_id]
        event = (
            catalog.EVENTS.get(notification.event_key) if notification.event_key else None
        ) or catalog.for_type(notification.type)
        items.append(
            DigestItem(
                event=event,
                message=_message_of(notification),
                severity=_severity(notification.severity),
                created_at=_aware(notification.created_at) or now,
            )
        )
    return render_digest(
        items,
        branding=branding,
        recipient_name=user.display_name,
        period_label=f"{now.day} {now:%B %Y}",
    )


def _message_of(notification: Notification) -> Message:
    return Message.from_stored(
        title=notification.title,
        body=notification.body,
        data=notification.data or {},
        action_path=notification.action_path,
    )


def _severity(value: str) -> Severity:
    try:
        return Severity(value)
    except ValueError:
        return Severity.INFO


def _send(  # noqa: PLR0913 - one send is exactly these parts
    session: Session,
    relay: mailer.Relay,
    deliveries: Sequence[NotificationDelivery],
    address: str,
    rendered: RenderedEmail,
    now: datetime,
    outcome: _Outcome,
) -> None:
    sender = mailer.sender()
    domain = sender.rsplit("@", 1)[-1].rstrip(">").strip() or None
    message = mailer.compose(
        subject=rendered.subject,
        body=rendered.text,
        html=rendered.html,
        recipients=[address],
        sender_address=sender,
        message_id=make_msgid(idstring=deliveries[0].id.hex, domain=domain),
    )
    digest_id = deliveries[0].id if len(deliveries) > 1 or deliveries[0].mode == "digest" else None
    try:
        relay.send_message(message)
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
        if _permanent(exc):
            for delivery in deliveries:
                delivery.attempts += 1
                _finish(delivery, "dead", _describe(exc), now)
                outcome.dead += 1
            session.commit()
            return
        for delivery in deliveries:
            _retry_or_bury(delivery, exc, now, outcome)
        session.commit()
        return
    for delivery in deliveries:
        delivery.attempts += 1
        delivery.digest_id = digest_id
        _finish(delivery, "sent", None, now)
        outcome.sent += 1
    session.commit()


def _permanent(exc: smtplib.SMTPException) -> bool:
    """5xx means the relay will never take this message as it is; 4xx may pass later."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return all(code >= 500 for code, _detail in exc.recipients.values())  # noqa: PLR2004
    code = getattr(exc, "smtp_code", None)
    return isinstance(code, int) and code >= 500  # noqa: PLR2004


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:_ERROR_LIMIT]


def _retry_or_bury(
    delivery: NotificationDelivery, exc: BaseException, now: datetime, outcome: _Outcome
) -> None:
    delivery.attempts += 1
    if delivery.attempts >= get_settings().notifications.max_attempts:
        _finish(delivery, "dead", _describe(exc), now)
        outcome.dead += 1
        return
    delay = RETRY_DELAYS[min(delivery.attempts - 1, len(RETRY_DELAYS) - 1)]
    delivery.last_error = _describe(exc)
    delivery.next_attempt_at = now + delay
    outcome.retrying += 1


def _finish(delivery: NotificationDelivery, status: str, error: str | None, now: datetime) -> None:
    delivery.status = status
    delivery.last_error = error
    if status == "sent":
        delivery.sent_at = now


def _reschedule(session: Session, organization_id: str, now: datetime, *, full_batch: bool) -> None:
    """Queue the next run for the earliest pending delivery, or straight away if more are due."""
    if full_batch:
        schedule(session, organization_id, now)
        return
    ensure_scheduled(session, organization_id, now=now)


# ---------------------------------------------------------------------------
# Administrator actions on the delivery log
# ---------------------------------------------------------------------------


def retry_now(
    db: Session, organization_id: str, delivery_id: UUID, *, now: datetime
) -> NotificationDelivery | None:
    """Send a dead or suppressed delivery again (an administrator's decision)."""
    row = db.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.organization_id == organization_id,
            NotificationDelivery.id == delivery_id,
        )
    )
    if row is None:
        return None
    if row.status == "sent":
        return row
    row.status = "pending"
    row.next_attempt_at = now
    row.updated_at = now  # a manual retry restarts the expiry window
    row.last_error = None
    db.flush()
    schedule(db, organization_id, now)
    return row


__all__ = [
    "EXPIRE_AFTER",
    "JOB_TYPE",
    "RETRY_DELAYS",
    "ensure_scheduled",
    "retry_now",
    "run_notification_dispatch",
    "schedule",
]
