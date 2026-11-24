"""The regulator status sync: a filed return moves on its own and its people are told.

Before ``regulatory_submission_sync`` a return the regulator had accepted stayed
"submitted" until an officer pressed "check status". These tests file through
the deterministic sandbox channel and let the background job find the outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, Notification, RegulatorySubmissionEvent
from app.reporting.schemas.packages import (
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
)
from app.reporting.service import submission_sync, validation, workflow
from app.services import job_queue
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.notifications.service.test_notifications_integration import (
    APPROVER_ID,
    CHECKER,
    DEMO_USER_ID,
    MAKER,
    _generate,
    _package_row,
    _seed_with_baseline_run,
)
from tests.support.factories.filing_chain import complete_chain

pytestmark = pytest.mark.usefixtures("return_generation_authority")

NOW = datetime(2026, 11, 23, 21, 30, tzinfo=UTC)


def _file_through_sandbox(db: Session, behavior: str = "ack") -> UUID:
    from app.reporting.models.packages import RegulatoryChannelConfig  # noqa: PLC0415

    db.add(
        RegulatoryChannelConfig(
            organization_id=MAKER.organization_id,
            bank_id=SAMPLE_BANK_ID,
            channel="orass_sandbox",
            config={"sandbox_behavior": behavior},
        )
    )
    package = _generate(db)
    validation.validate_package(db, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(db, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate())
    workflow.decide_approval(
        db, CHECKER, SAMPLE_BANK_ID, package.id, PackageApprovalDecisionCreate(action="approved")
    )
    complete_chain(db, _package_row(db, package.id))
    db.commit()
    workflow.submit_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        package.id,
        channel="orass_sandbox",
        external_ref="SANDBOX-TEST-0001",
    )
    return package.id


def _run_sync(db: Session, now: datetime) -> Job:
    job = db.scalars(
        select(Job).where(Job.job_type == submission_sync.JOB_TYPE, Job.status == "queued")
    ).first()
    if job is None:
        job = job_queue.enqueue(db, MAKER.organization_id, submission_sync.JOB_TYPE, payload={})
    job.status = "running"
    job.started_at = now
    db.commit()
    submission_sync.run_regulatory_submission_sync(db, job, now=now)
    job.status = "succeeded"
    db.commit()
    return job


def _rows(db: Session, event_key: str) -> list[Notification]:
    return list(db.scalars(select(Notification).where(Notification.event_key == event_key)))


def test_filing_schedules_a_check_and_announces_the_filing(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    _file_through_sandbox(db_session)

    queued = db_session.scalar(
        select(Job).where(Job.job_type == submission_sync.JOB_TYPE, Job.status == "queued")
    )
    assert queued is not None, "filing must start the status sync"
    filed = _rows(db_session, "filing.filed")
    recipients = {row.recipient_user_id for row in filed}
    # The officer who filed is not told about their own act; the approver is.
    assert APPROVER_ID in recipients
    assert DEMO_USER_ID not in recipients


def test_the_sync_records_an_acceptance_without_anyone_pressing_a_button(
    db_session: Session,
) -> None:
    _seed_with_baseline_run(db_session)
    package_id = _file_through_sandbox(db_session, behavior="ack")

    job = _run_sync(db_session, NOW)

    assert job.progress is not None and job.progress["decided"] == 1
    assert _package_row(db_session, package_id).status == "acknowledged"
    accepted = _rows(db_session, "regulator.accepted")
    # A system act excludes nobody: the preparer and the approver both hear.
    assert {DEMO_USER_ID, APPROVER_ID} <= {row.recipient_user_id for row in accepted}
    assert all("accepted LCR-NSFR for 31 March 2026" in row.title for row in accepted)

    # Nothing left to watch: running again decides nothing and notifies nobody.
    again = _run_sync(db_session, NOW + timedelta(minutes=1))
    assert again.progress is not None and again.progress["decided"] == 0
    assert len(_rows(db_session, "regulator.accepted")) == len(accepted)


def test_a_rejection_carries_the_regulators_comments(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package_id = _file_through_sandbox(db_session, behavior="reject")

    _run_sync(db_session, NOW)

    assert _package_row(db_session, package_id).status == "rejected"
    sent_back = _rows(db_session, "regulator.sent_back")
    assert sent_back
    assert all(row.data.get("quote") for row in sent_back)
    assert all(row.severity == "warning" for row in sent_back)


def test_a_slow_regulator_is_checked_again_until_it_decides(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package_id = _file_through_sandbox(db_session, behavior="slow")

    first = _run_sync(db_session, NOW)
    assert first.progress is not None and first.progress["still_waiting"] == 1
    assert _package_row(db_session, package_id).status == "submitted"
    next_run = db_session.scalar(
        select(Job).where(Job.job_type == submission_sync.JOB_TYPE, Job.status == "queued")
    )
    assert next_run is not None, "a waiting return keeps its sync chain alive"

    for minute in range(1, 6):
        _run_sync(db_session, NOW + timedelta(minutes=minute))
        if _package_row(db_session, package_id).status != "submitted":
            break
    assert _package_row(db_session, package_id).status == "acknowledged"
    polls = db_session.scalars(
        select(RegulatorySubmissionEvent).where(
            RegulatorySubmissionEvent.package_id == package_id,
            RegulatorySubmissionEvent.event == "status_poll",
        )
    ).all()
    assert polls


def test_the_cadence_slows_as_a_filing_ages() -> None:
    assert submission_sync.next_interval(timedelta(minutes=3)) == timedelta(minutes=1)
    assert submission_sync.next_interval(timedelta(minutes=30)) == timedelta(minutes=5)
    assert submission_sync.next_interval(timedelta(hours=5)) == timedelta(minutes=15)
    assert submission_sync.next_interval(timedelta(days=3)) == timedelta(hours=1)
