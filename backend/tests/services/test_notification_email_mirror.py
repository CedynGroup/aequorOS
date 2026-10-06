"""GAP-5 SMTP mirror: outbox semantics, default-off, fan-out, idempotency."""

from __future__ import annotations

import smtplib
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import RoleBundle
from app.core.config import get_settings
from app.models import AuthorizationBinding, Job, Notification, User
from app.services import job_queue, notifications
from app.services.notification_email_mirror import (
    enqueue_due_notification_mirror,
    run_notification_email_mirror,
)
from tests.api.helpers import ORG_1, USER_1
from tests.api.test_package_authorization import _add_bank, _package
from tests.factories.authorization import grant_institution_authority
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID

APPROVER_ID = UUID("dddddddd-1111-4ddd-8ddd-ddddddddddd1")
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
NOW = datetime(2026, 7, 24, 9, 0, tzinfo=UTC)


class _FakeSmtp:
    """Captures messages; class-level store survives context-manager scope."""

    sent: list = []
    fail = False

    def __init__(self, host: str, port: int, timeout: int = 0) -> None:
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        return None

    def send_message(self, message) -> None:
        if _FakeSmtp.fail:
            raise smtplib.SMTPException("simulated outage")
        _FakeSmtp.sent.append(message)


@pytest.fixture(autouse=True)
def _reset_fake_smtp() -> None:
    _FakeSmtp.sent = []
    _FakeSmtp.fail = False


def _enable_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")
    monkeypatch.setenv("SMTP_FROM", "aequoros@test.local")
    get_settings.cache_clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)


def _seed_admin(db: Session) -> None:
    user = db.get(User, USER_1)
    assert user is not None
    user.role = "account_admin"
    db.add(
        User(
            id=APPROVER_ID,
            organization_id=ORG_1,
            email="admin.two@aequoros.example",
            display_name="Admin Two",
            role="account_admin",
        )
    )
    db.commit()


def _emit_rows(db: Session) -> None:
    _add_bank(SAMPLE_BANK_ID, organization_id=ORG_1)
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    for user_id in (USER_1, APPROVER_ID):
        grant_institution_authority(
            db,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            user_id=user_id,
            bundle=RoleBundle.VIEWER,
        )
    notifications.emit(
        db,
        CTX,
        type="reporting.package.approved",
        severity="info",
        title="BSD3 2026-03-31 approved",
        body="Version 1 approved for submission.",
        recipient_user_id=USER_1,
        entity_type="regulatory_package",
        entity_id=package_id,
    )
    notifications.emit(
        db,
        CTX,
        type="reporting.deadline.overdue:LCR-NSFR:2026-03-31:2026-07-24",
        severity="critical",
        title="BSD3 2026-03-31 overdue",
        body="The return is past its due date.",
        recipient_user_id=None,
        entity_type="bank",
        entity_id=SAMPLE_BANK_ID,
    )
    db.commit()


def _run_mirror(db: Session) -> Job:
    job_queue.enqueue(db, ORG_1, "notification_email_mirror", payload={})
    db.commit()
    job = job_queue.claim_next(db, NOW, ("notification_email_mirror",))
    assert job is not None
    run_notification_email_mirror(db, job)
    return job


def test_mirror_disabled_by_default(db_session: Session) -> None:
    assert enqueue_due_notification_mirror(db_session, ORG_1, now=NOW) is False
    assert (
        db_session.scalar(select(Job.id).where(Job.job_type == "notification_email_mirror")) is None
    )


def test_mirror_sends_and_stamps_outbox(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_smtp(monkeypatch)
    _seed_admin(db_session)
    _emit_rows(db_session)

    job = _run_mirror(db_session)
    assert job.progress == {"sent": 2, "skipped_no_recipient": 0}
    assert len(_FakeSmtp.sent) == 2
    subjects = {str(message["Subject"]) for message in _FakeSmtp.sent}
    assert subjects == {
        "[AequorOS] BSD3 2026-03-31 approved",
        "[AequorOS] BSD3 2026-03-31 overdue",
    }
    # Org-wide row fanned out to the active admin's address.
    recipients = {str(message["To"]) for message in _FakeSmtp.sent}
    assert any("admin.two@aequoros.example" in value for value in recipients)
    assert db_session.scalar(select(Notification).where(Notification.emailed_at.is_(None))) is None

    # Second cycle: outbox drained, nothing re-sent (idempotent).
    _FakeSmtp.sent = []
    job2 = _run_mirror(db_session)
    assert job2.progress == {"sent": 0, "skipped_no_recipient": 0}
    assert _FakeSmtp.sent == []


def test_mirror_outage_leaves_outbox_pending(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_smtp(monkeypatch)
    _seed_admin(db_session)
    _emit_rows(db_session)
    _FakeSmtp.fail = True

    job = _run_mirror(db_session)
    assert job.progress["sent"] == 0
    assert job.progress["error"] == "SMTPException"
    pending = db_session.scalars(
        select(Notification).where(Notification.emailed_at.is_(None))
    ).all()
    assert len(pending) == 2  # nothing lost; retried next cycle

    # Outage over: the same rows deliver.
    _FakeSmtp.fail = False
    job2 = _run_mirror(db_session)
    assert job2.progress == {"sent": 2, "skipped_no_recipient": 0}


def test_enqueue_coalesces_per_hour(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_smtp(monkeypatch)
    assert enqueue_due_notification_mirror(db_session, ORG_1, now=NOW) is True
    db_session.commit()
    assert enqueue_due_notification_mirror(db_session, ORG_1, now=NOW) is False


@pytest.mark.parametrize("revoke_all", [False, True])
def test_mirror_filters_return_state_by_recipient_authority(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, revoke_all: bool
) -> None:
    _enable_smtp(monkeypatch)
    _seed_admin(db_session)
    _emit_rows(db_session)
    stmt = update(AuthorizationBinding)
    if not revoke_all:
        stmt = stmt.where(AuthorizationBinding.principal_user_id == APPROVER_ID)
    db_session.execute(
        stmt.values(
            status="revoked",
            revoked_at=datetime.now(UTC),
            revoked_by_type="system",
            revoked_by_id="notification-fixture",
            revoked_reason="Remove return visibility",
        )
    )
    db_session.commit()
    job = _run_mirror(db_session)
    if revoke_all:
        assert job.progress == {"sent": 0, "skipped_no_recipient": 2}
        assert _FakeSmtp.sent == []
    else:
        assert job.progress == {"sent": 2, "skipped_no_recipient": 0}
        assert all(
            "admin.two@aequoros.example" not in str(message["To"]) for message in _FakeSmtp.sent
        )
