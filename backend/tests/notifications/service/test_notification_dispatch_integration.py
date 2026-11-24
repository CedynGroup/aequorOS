"""Publishing and dispatch: preferences, branded delivery, retries, dead letters, digests.

Replaces the SMTP-mirror suite: every behaviour that suite pinned (default-off,
org-wide fan-out to administrators, idempotent draining, outage recovery,
recipient authority at send time) is pinned again here against the per-recipient
delivery log, alongside what the platform adds.
"""

from __future__ import annotations

import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import ClassVar
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module, RoleBundle, Sensitivity
from app.core.config import get_settings
from app.identity.public import Bank
from app.models import AuthorizationBinding, Job, Notification, NotificationDelivery, User
from app.notifications import catalog
from app.notifications.catalog import EmailMode
from app.notifications.rendering import Action, Fact, Message
from app.notifications.service import dispatch, notifications, preferences, publishing
from app.policy.service.institution_types import FALLBACK_TYPE_CODE
from app.services import job_queue
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.support.factories.authorization import grant_institution_authority
from tests.support.helpers import ORG_1, USER_1

SECOND_ID = UUID("dddddddd-1111-4ddd-8ddd-ddddddddddd1")
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


class _FakeSmtp:
    sent: ClassVar[list[EmailMessage]] = []
    failure: ClassVar[Exception | None] = None

    def __init__(self, host: str, port: int, timeout: int = 0) -> None:
        self.host = host

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def starttls(self, *, context: ssl.SSLContext | None = None) -> None:
        assert context is not None

    def login(self, username: str, password: str) -> None:
        return None

    def send_message(self, message: EmailMessage) -> None:
        if _FakeSmtp.failure is not None:
            raise _FakeSmtp.failure
        _FakeSmtp.sent.append(message)


@pytest.fixture(autouse=True)
def _reset_fake_smtp() -> None:
    _FakeSmtp.sent = []
    _FakeSmtp.failure = None


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")
    monkeypatch.setenv("SMTP_FROM", "AequorOS <noreply@aequoros.test>")
    monkeypatch.setenv("BANK_APP_BASE_URL", "https://bank.example.test")
    get_settings.cache_clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)


def _seed(db: Session) -> None:
    """A bank, two officers who can see regulatory material at it, one of them an admin."""
    db.add(
        Bank(
            id=SAMPLE_BANK_ID,
            organization_id=ORG_1,
            name="Fixture Bank",
            short_name="Fixture",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type=FALLBACK_TYPE_CODE,
        )
    )
    first = db.get(User, USER_1)
    assert first is not None
    first.role = "account_admin"
    first.display_name = "Ama Mensah"
    db.add(
        User(
            id=SECOND_ID,
            organization_id=ORG_1,
            email="kofi@aequoros.test",
            display_name="Kofi Boateng",
            role="analyst",
        )
    )
    db.flush()
    for user_id in (USER_1, SECOND_ID):
        grant_institution_authority(
            db,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            user_id=user_id,
            bundle=RoleBundle.VIEWER,
        )
    db.commit()


def _message(title: str = "CAR-RWA is waiting for your review") -> Message:
    return Message(
        title=title,
        summary="Version 1 of CAR-RWA for 30 June 2026 was sent to Review.",
        facts=(Fact("Return", "CAR-RWA"),),
        action=Action("Review the return", "/submissions/returns"),
    )


def _publish(
    db: Session,
    *,
    event: catalog.EventDefinition = catalog.FILING_REVIEW_REQUESTED,
    recipients: tuple[UUID, ...] = (SECOND_ID,),
    dedupe_key: str | None = None,
    now: datetime = NOW,
) -> list[Notification]:
    rows = publishing.publish(
        db,
        CTX,
        event=event,
        message=_message(),
        recipients=recipients,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        bank_id=SAMPLE_BANK_ID,
        dedupe_key=dedupe_key,
        now=now,
    )
    db.commit()
    return rows


def _dispatch(db: Session, now: datetime = NOW) -> Job:
    job = db.scalars(
        select(Job).where(Job.job_type == dispatch.JOB_TYPE, Job.status == "queued")
    ).first()
    if job is None:
        job = job_queue.enqueue(db, ORG_1, dispatch.JOB_TYPE, payload={})
    job.status = "running"
    job.started_at = now
    db.commit()
    dispatch.run_notification_dispatch(db, job, now=now)
    job.status = "succeeded"
    db.commit()
    return job


def _utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive timestamps; Postgres returns aware ones."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _deliveries(db: Session) -> list[NotificationDelivery]:
    return list(db.scalars(select(NotificationDelivery).order_by(NotificationDelivery.created_at)))


# ---------------------------------------------------------------------------


def test_no_relay_means_feed_only(db_session: Session) -> None:
    _seed(db_session)
    rows = _publish(db_session)
    assert [row.recipient_user_id for row in rows] == [SECOND_ID]
    assert rows[0].event_key == "filing.review_requested"
    assert _deliveries(db_session) == []
    assert db_session.scalar(select(Job.id).where(Job.job_type == dispatch.JOB_TYPE)) is None


def test_publish_queues_and_dispatch_sends_one_branded_email(
    db_session: Session, relay: None
) -> None:
    _seed(db_session)
    _publish(db_session)
    [delivery] = _deliveries(db_session)
    assert (delivery.status, delivery.mode, delivery.address) == (
        "pending",
        "immediate",
        "kofi@aequoros.test",
    )
    queued = db_session.scalar(select(Job).where(Job.job_type == dispatch.JOB_TYPE))
    assert queued is not None

    job = _dispatch(db_session)
    assert job.progress == {"sent": 1, "retrying": 0, "dead": 0, "suppressed": 0, "expired": 0}
    [message] = _FakeSmtp.sent
    assert str(message["To"]) == "kofi@aequoros.test"
    assert str(message["Subject"]) == "[AequorOS] CAR-RWA is waiting for your review"
    assert message.get_content_type() == "multipart/alternative"
    html = message.get_body(preferencelist=("html",))
    assert html is not None
    assert "https://bank.example.test/submissions/returns" in html.get_content()
    db_session.refresh(delivery)
    assert (delivery.status, delivery.attempts, _utc(delivery.sent_at)) == ("sent", 1, NOW)

    # Draining again sends nothing: the log is the outbox.
    _FakeSmtp.sent = []
    _dispatch(db_session, NOW + timedelta(minutes=5))
    assert _FakeSmtp.sent == []


def test_the_actor_is_excluded_and_inactive_people_are_dropped(
    db_session: Session, relay: None
) -> None:
    _seed(db_session)
    user = db_session.get(User, SECOND_ID)
    assert user is not None
    user.is_active = False
    db_session.commit()
    rows = publishing.publish(
        db_session,
        CTX,
        event=catalog.FILING_REVIEW_REQUESTED,
        message=_message(),
        recipients=(USER_1, SECOND_ID),
        exclude=(USER_1,),
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        bank_id=SAMPLE_BANK_ID,
        now=NOW,
    )
    assert rows == []


def test_a_person_who_turned_email_off_gets_the_feed_only(db_session: Session, relay: None) -> None:
    _seed(db_session)
    preferences.set_user_choices(
        db_session, ORG_1, SECOND_ID, {"filing.review_requested": EmailMode.OFF}
    )
    db_session.commit()
    rows = _publish(db_session)
    assert len(rows) == 1
    assert _deliveries(db_session) == []


def test_a_locked_policy_cannot_be_overridden_by_the_person(
    db_session: Session, relay: None
) -> None:
    _seed(db_session)
    preferences.set_policies(
        db_session,
        ORG_1,
        USER_1,
        {"filing.review_requested": preferences.PolicyChange(EmailMode.IMMEDIATE, locked=True)},
    )
    db_session.commit()
    with pytest.raises(preferences.PreferenceError, match="set by your organisation"):
        preferences.set_user_choices(
            db_session, ORG_1, SECOND_ID, {"filing.review_requested": EmailMode.OFF}
        )


def test_digest_deliveries_wait_for_the_summary_hour_and_go_as_one_email(
    db_session: Session, relay: None
) -> None:
    _seed(db_session)
    preferences.set_user_choices(
        db_session, ORG_1, SECOND_ID, {"filing.review_requested": EmailMode.DIGEST}
    )
    db_session.commit()
    _publish(db_session)
    _publish(db_session, now=NOW + timedelta(minutes=10))
    deliveries = _deliveries(db_session)
    summary_at = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)
    assert {_utc(delivery.next_attempt_at) for delivery in deliveries} == {summary_at}

    _dispatch(db_session, NOW + timedelta(hours=1))
    assert _FakeSmtp.sent == []

    job = _dispatch(db_session, summary_at)
    assert job.progress["sent"] == 2
    [message] = _FakeSmtp.sent
    assert str(message["Subject"]) == "[AequorOS] Your daily summary: 2 updates"
    digest_ids = {delivery.digest_id for delivery in _deliveries(db_session)}
    assert len(digest_ids) == 1 and None not in digest_ids


def test_a_temporary_failure_retries_with_backoff_then_succeeds(
    db_session: Session, relay: None
) -> None:
    _seed(db_session)
    _publish(db_session)
    _FakeSmtp.failure = smtplib.SMTPServerDisconnected("relay went away")
    job = _dispatch(db_session)
    assert job.progress["retrying"] == 1
    [delivery] = _deliveries(db_session)
    assert delivery.status == "pending"
    assert delivery.attempts == 1
    assert _utc(delivery.next_attempt_at) == NOW + dispatch.RETRY_DELAYS[0]
    assert delivery.last_error is not None and "relay went away" in delivery.last_error
    # The retry is scheduled without waiting for the hourly tick.
    queued = db_session.scalar(
        select(Job).where(Job.job_type == dispatch.JOB_TYPE, Job.status == "queued")
    )
    assert queued is not None

    _FakeSmtp.failure = None
    _dispatch(db_session, NOW + dispatch.RETRY_DELAYS[0])
    db_session.refresh(delivery)
    assert (delivery.status, delivery.attempts) == ("sent", 2)


def test_a_permanent_refusal_is_dead_lettered_at_once(db_session: Session, relay: None) -> None:
    _seed(db_session)
    _publish(db_session)
    _FakeSmtp.failure = smtplib.SMTPRecipientsRefused(
        {"kofi@aequoros.test": (550, b"5.1.1 No such user")}
    )
    job = _dispatch(db_session)
    assert job.progress["dead"] == 1
    [delivery] = _deliveries(db_session)
    assert delivery.status == "dead"
    assert delivery.last_error is not None and "SMTPRecipientsRefused" in delivery.last_error

    # An administrator can send it again once the mailbox is fixed.
    _FakeSmtp.failure = None
    retried = dispatch.retry_now(db_session, ORG_1, delivery.id, now=NOW + timedelta(hours=1))
    db_session.commit()
    assert retried is not None and retried.status == "pending"
    _dispatch(db_session, NOW + timedelta(hours=1))
    db_session.refresh(delivery)
    assert delivery.status == "sent"


def test_attempts_are_capped(
    db_session: Session, relay: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFICATION_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    _seed(db_session)
    _publish(db_session)
    _FakeSmtp.failure = smtplib.SMTPServerDisconnected("down")
    _dispatch(db_session)
    _dispatch(db_session, NOW + dispatch.RETRY_DELAYS[0])
    [delivery] = _deliveries(db_session)
    assert (delivery.status, delivery.attempts) == ("dead", 2)


def test_the_allowlist_suppresses_everyone_else(
    db_session: Session, relay: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFICATION_EMAIL_ALLOWLIST", "someone@elsewhere.test, @other.test")
    get_settings.cache_clear()
    _seed(db_session)
    _publish(db_session)
    job = _dispatch(db_session)
    assert job.progress["suppressed"] == 1
    assert _FakeSmtp.sent == []
    [delivery] = _deliveries(db_session)
    assert (delivery.status, delivery.last_error) == ("suppressed", "not_on_allowlist")


def test_a_recipient_who_lost_access_is_suppressed_at_send_time(
    db_session: Session, relay: None
) -> None:
    _seed(db_session)
    _publish(db_session)
    db_session.execute(
        update(AuthorizationBinding)
        .where(AuthorizationBinding.principal_user_id == SECOND_ID)
        .values(
            status="revoked",
            revoked_at=NOW,
            revoked_by_type="system",
            revoked_by_id="notification-fixture",
            revoked_reason="Remove return visibility",
        )
    )
    db_session.commit()
    _dispatch(db_session)
    assert _FakeSmtp.sent == []
    [delivery] = _deliveries(db_session)
    assert (delivery.status, delivery.last_error) == ("suppressed", "recipient_lost_access")


def test_a_dedupe_key_makes_a_scheduled_producer_safe_to_rerun(
    db_session: Session, relay: None
) -> None:
    _seed(db_session)
    first = _publish(db_session, event=catalog.FILING_REVIEW_OVERDUE, dedupe_key="pkg:2:1")
    again = _publish(db_session, event=catalog.FILING_REVIEW_OVERDUE, dedupe_key="pkg:2:1")
    assert len(first) == 1
    assert again == []
    assert first[0].type == "filing.review_overdue:pkg:2:1"


def test_legacy_emit_reaches_email_and_fans_org_wide_rows_to_admins(
    db_session: Session, relay: None
) -> None:
    _seed(db_session)
    notifications.emit(
        db_session,
        CTX,
        type="reporting.deadline.overdue:CAR-RWA:2026-06-30:2026-11-23",
        severity="critical",
        title="CAR-RWA 2026-06-30 is overdue",
        body="The return is past its due date.",
        recipient_user_id=None,
        # Deadline rows name their bank: the feed's return-family factor
        # admits them only through it, and the dispatcher applies that factor.
        entity_type="bank",
        entity_id=SAMPLE_BANK_ID,
        bank_id=SAMPLE_BANK_ID,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        now=NOW,
    )
    db_session.commit()
    [row] = db_session.scalars(select(Notification)).all()
    assert row.event_key == "deadlines.missed"
    [delivery] = _deliveries(db_session)
    # Only the account administrator receives the org-wide row by email.
    assert delivery.recipient_user_id == USER_1
    _dispatch(db_session)
    db_session.refresh(delivery)
    assert (delivery.status, delivery.last_error) == ("sent", None)
    [message] = _FakeSmtp.sent
    assert str(message["Subject"]) == "[AequorOS] CAR-RWA 2026-06-30 is overdue"
