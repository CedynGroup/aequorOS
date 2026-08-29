"""SMTP mirror for in-app notifications (plan GAP-5) — outbox pattern.

The `notifications` table is the outbox: rows with ``emailed_at IS NULL`` are
pending. The worker job drains them per organization — user-directed rows go
to that user's email; org-wide rows fan out to active admin users — then
stamps ``emailed_at``. Delivery is strictly best-effort and post-commit (the
worker reads committed rows), so a rolled-back business transaction can never
send email, and an SMTP outage never fails a business action: undelivered
rows simply wait for the next cycle.

Disabled by default: activates only when SMTP_HOST and SMTP_FROM are set.
"""

from __future__ import annotations

import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import ACCOUNT_ADMIN_ROLE, ADMIN_ROLE
from app.models import Job, Notification, User
from app.services import job_queue

JOB_TYPE = "notification_email_mirror"
# One batch per cycle; stragglers roll to the next tick.
_BATCH_SIZE = 200
# Never mirror stale history (e.g. right after enabling SMTP on an old install).
_MAX_AGE_DAYS = 7
_FOOTER = (
    "\n\n--\nAequorOS notification mirror. Manage notifications in the "
    "dashboard (inbox icon); this mailbox is not monitored."
)


def mirror_enabled() -> bool:
    return get_settings().smtp.enabled


def enqueue_due_notification_mirror(session: Session, org_id: str, *, now: datetime) -> bool:
    """Enqueue at most one mirror job per org per hour; no-op when disabled."""
    if not mirror_enabled():
        return False
    coalesce_key = f"notification_mirror:{org_id}:{now.strftime('%Y-%m-%dT%H')}"
    existing = session.scalar(
        select(Job.id).where(
            Job.organization_id == org_id,
            Job.job_type == JOB_TYPE,
            Job.coalesce_key == coalesce_key,
        )
    )
    if existing is not None:
        return False
    job_queue.enqueue(session, org_id, JOB_TYPE, payload={}, coalesce_key=coalesce_key)
    return True


def _pending_rows(session: Session, org_id: str, now: datetime) -> list[Notification]:
    cutoff = now - timedelta(days=_MAX_AGE_DAYS)
    return list(
        session.scalars(
            select(Notification)
            .where(
                Notification.organization_id == org_id,
                Notification.emailed_at.is_(None),
                Notification.created_at >= cutoff,
            )
            .order_by(Notification.created_at)
            .limit(_BATCH_SIZE)
        )
    )


def _recipient_emails(
    session: Session, notification: Notification, admin_cache: list[str] | None = None
) -> list[str]:
    if notification.recipient_user_id is not None:
        email = session.scalar(
            select(User.email).where(
                User.id == notification.recipient_user_id,
                User.organization_id == notification.organization_id,
                User.is_active.is_(True),
            )
        )
        return [email] if email else []
    # Org-wide rows mirror to active account administrators. The caller may
    # pass a cached list so the same admin emails are not re-queried per row.
    if admin_cache is not None:
        return admin_cache
    return list(
        session.scalars(
            select(User.email).where(
                User.organization_id == notification.organization_id,
                User.role.in_((ACCOUNT_ADMIN_ROLE, ADMIN_ROLE)),
                User.is_active.is_(True),
            )
        )
    )


def _compose(notification: Notification, recipients: list[str], sender: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = f"[AequorOS] {notification.title}"
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(notification.body + _FOOTER)
    return message


def run_notification_email_mirror(session: Session, job: Job) -> None:
    """Worker handler: drain this org's outbox through the configured SMTP."""
    settings = get_settings().smtp
    if not settings.enabled:
        job.progress = {"skipped": "smtp_disabled"}
        return
    now = datetime.now(UTC)
    rows = _pending_rows(session, job.organization_id, now)
    sent = 0
    skipped_no_recipient = 0
    assert settings.smtp_host is not None and settings.smtp_from is not None
    # Fetch admin emails once for the whole batch so org-wide rows do not
    # each trigger a separate query for the same recipient list.
    org_wide_recipients = list(
        session.scalars(
            select(User.email).where(
                User.organization_id == job.organization_id,
                User.role.in_((ACCOUNT_ADMIN_ROLE, ADMIN_ROLE)),
                User.is_active.is_(True),
            )
        )
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as client:
            if settings.smtp_starttls:
                client.starttls()
            if settings.smtp_username and settings.smtp_password:
                client.login(settings.smtp_username, settings.smtp_password)
            for row in rows:
                recipients = _recipient_emails(session, row, admin_cache=org_wide_recipients)
                if not recipients:
                    # Nothing to deliver to (deactivated user, no account admins):
                    # stamp it so the outbox cannot jam on the row forever.
                    row.emailed_at = now
                    skipped_no_recipient += 1
                    continue
                client.send_message(_compose(row, recipients, settings.smtp_from))
                row.emailed_at = now
                sent += 1
                session.flush()
    except (smtplib.SMTPException, OSError) as exc:
        # Best-effort: keep what was stamped, surface the rest next cycle.
        logger.warning("notification email mirror interrupted: {}", exc)
        job.progress = {
            "sent": sent,
            "skipped_no_recipient": skipped_no_recipient,
            "pending_after_error": max(len(rows) - sent - skipped_no_recipient, 0),
            "error": type(exc).__name__,
        }
        session.commit()
        return
    job.progress = {"sent": sent, "skipped_no_recipient": skipped_no_recipient}
    session.commit()
