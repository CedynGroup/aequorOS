"""Reminders and escalation for a return left waiting in review."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Job, Notification
from app.notifications.service import preferences
from app.reporting.models.filing_workflow import PackageStageDecision
from app.reporting.schemas.packages import PackageApprovalRequestCreate
from app.reporting.service import review_reminders, validation, workflow
from app.services import job_queue
from tests.fixtures.canonical_bank_fixture import DEMO_ORG_ID, SAMPLE_BANK_ID
from tests.notifications.service.test_notifications_integration import (
    APPROVER_ID,
    MAKER,
    _generate,
    _seed_with_baseline_run,
)

pytestmark = pytest.mark.usefixtures("return_generation_authority")


def _send_for_review(db: Session) -> tuple[UUID, datetime]:
    package = _generate(db)
    validation.validate_package(db, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(db, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate())
    since = db.scalar(
        select(func.max(PackageStageDecision.created_at)).where(
            PackageStageDecision.package_id == package.id
        )
    )
    assert since is not None
    return package.id, since if since.tzinfo else since.replace(tzinfo=UTC)


def _run(db: Session, now: datetime) -> Job:
    job = job_queue.enqueue(db, DEMO_ORG_ID, review_reminders.JOB_TYPE, payload={})
    job.status = "running"
    db.commit()
    review_reminders.run_filing_review_reminders(db, job, now=now)
    return job


def _rows(db: Session, event_key: str) -> list[Notification]:
    return list(db.scalars(select(Notification).where(Notification.event_key == event_key)))


def test_sending_for_review_schedules_the_first_reminder(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    _send_for_review(db_session)
    queued = db_session.scalar(
        select(Job).where(Job.job_type == review_reminders.JOB_TYPE, Job.status == "queued")
    )
    assert queued is not None


def test_a_waiting_return_is_reminded_then_escalated_once_each(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    _package_id, since = _send_for_review(db_session)

    early = _run(db_session, since + timedelta(hours=2))
    assert early.progress == {
        "reminded": 0,
        "escalated": 0,
        "next_check": (since + timedelta(hours=24)).isoformat(),
    }

    _run(db_session, since + timedelta(hours=25))
    reminders = _rows(db_session, "filing.review_overdue")
    assert {row.recipient_user_id for row in reminders} == {APPROVER_ID}
    assert "has waited 25 hours" in reminders[0].title

    # Re-running inside the same window adds nothing.
    _run(db_session, since + timedelta(hours=30))
    assert len(_rows(db_session, "filing.review_overdue")) == len(reminders)

    _run(db_session, since + timedelta(hours=49))
    escalations = _rows(db_session, "filing.review_escalated")
    assert APPROVER_ID in {row.recipient_user_id for row in escalations}
    assert all(row.severity == "critical" for row in escalations)
    _run(db_session, since + timedelta(hours=60))
    assert len(_rows(db_session, "filing.review_escalated")) == len(escalations)


def test_an_organisation_can_switch_reminders_off(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    _package_id, since = _send_for_review(db_session)
    preferences.save_org_delivery(
        db_session,
        DEMO_ORG_ID,
        None,
        preferences.OrgDelivery(
            email_enabled=True,
            time_zone="UTC",
            digest_hour=7,
            reminders_enabled=False,
            reminder_after_hours=24,
            escalate_after_hours=48,
        ),
    )
    db_session.commit()
    job = _run(db_session, since + timedelta(hours=50))
    assert job.progress == {"skipped": "reminders_disabled"}
    assert _rows(db_session, "filing.review_overdue") == []
