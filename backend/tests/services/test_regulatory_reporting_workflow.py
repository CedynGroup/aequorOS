from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import BankReportingPeriod, RegulatoryPackage, User
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import (
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryPackageCreate,
)
from app.services import regulatory_liquidity
from app.services.regulatory_reporting import calendar, generation, validation, workflow
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
CHECKER = TenantContext(
    organization_id=DEMO_ORG_ID,
    actor_user_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
)
REPORTING_DATE = date(2026, 3, 31)


def _seed_with_baseline_run(db: Session) -> None:
    seed_sample_bank(db)
    if db.scalar(select(User.id).where(User.id == CHECKER.actor_user_id)) is None:
        db.add(
            User(
                id=CHECKER.actor_user_id,
                organization_id=DEMO_ORG_ID,
                email="demo.checker@example.test",
                display_name="Demo Checker",
            )
        )
        db.commit()
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    run = regulatory_liquidity.create_liquidity_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded"


def _generate(db: Session):
    return generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="BSD3", reporting_date=REPORTING_DATE),
    )


def _package_row(db: Session, package_id: UUID) -> RegulatoryPackage:
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == package_id))
    assert row is not None
    return row


def test_allowed_transition_table_blocks_illegal_moves() -> None:
    package = RegulatoryPackage(status="generated")
    workflow.ensure_transition_allowed(package, "validated")  # legal, no raise

    for illegal in ("approved", "submitted", "acknowledged", "draft"):
        with pytest.raises(HTTPException) as exc_info:
            workflow.ensure_transition_allowed(package, illegal)
        assert exc_info.value.status_code == 409

    for terminal in ("acknowledged", "superseded"):
        package.status = terminal
        for target in ("generated", "validated", "submitted"):
            with pytest.raises(HTTPException):
                workflow.ensure_transition_allowed(package, target)

    package.status = "submitted"
    workflow.ensure_transition_allowed(package, "acknowledged")
    workflow.ensure_transition_allowed(package, "rejected")
    with pytest.raises(HTTPException):
        workflow.ensure_transition_allowed(package, "generated")


def test_full_lifecycle_to_acknowledged(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package = _generate(db_session)
    assert package.status == "generated"

    validated = validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert validated.status == "validated"
    assert validated.validation_report is not None
    assert validated.validation_report.passed is True

    pending = workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    assert pending.status == "pending_approval"

    approved = workflow.decide_approval(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    assert approved.status == "approved"

    submitted = workflow.submit_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        package.id,
        channel="manual",
        external_ref="BOG-RCPT-0001",
    )
    assert submitted.status == "submitted"

    acknowledged = workflow.record_regulator_decision(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        package.id,
        channel="manual",
        event="acknowledged",
        external_ref="BOG-RCPT-0001",
        detail={"note": "Received in good order."},
    )
    assert acknowledged.status == "acknowledged"

    events = workflow.list_submission_events(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert events.total == 2
    assert [event.event for event in events.events] == ["acknowledged", "submitted"]
    assert all(event.channel == "manual" for event in events.events)
    assert events.events[1].external_ref == "BOG-RCPT-0001"


def test_maker_checker_rejects_same_user_decision(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package = _generate(db_session)
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )

    with pytest.raises(HTTPException) as exc_info:
        workflow.decide_approval(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            package.id,
            PackageApprovalDecisionCreate(action="approved"),
        )
    assert exc_info.value.status_code == 409
    assert "different user" in str(exc_info.value.detail)
    assert _package_row(db_session, package.id).status == "pending_approval"


def test_rejected_decision_returns_package_to_generated(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package = _generate(db_session)
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    rejected = workflow.decide_approval(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="rejected", reason="Numbers moved after cutoff."),
    )
    assert rejected.status == "generated"
    assert [approval.action for approval in rejected.approvals] == ["requested", "rejected"]

    revalidated = validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert revalidated.status == "validated"


def test_regeneration_supersedes_prior_version_immutably(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    first = _generate(db_session)
    first_snapshot = _package_row(db_session, first.id).snapshot

    second = _generate(db_session)
    assert second.version == first.version + 1
    assert second.supersedes_id == first.id
    assert second.status == "generated"

    prior = _package_row(db_session, first.id)
    assert prior.status == "superseded"
    assert prior.snapshot == first_snapshot
    assert prior.source_runs == [entry.model_dump(mode="json") for entry in first.source_runs]

    # The superseded version is terminal.
    with pytest.raises(HTTPException):
        workflow.ensure_transition_allowed(prior, "validated")


def test_validation_errors_block_approval_request(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package = _generate(db_session)

    row = _package_row(db_session, package.id)
    snapshot = dict(row.snapshot)
    sections = [dict(section) for section in snapshot["sections"]]
    sections[0]["rows"] = []
    snapshot["sections"] = sections
    row.snapshot = snapshot
    db_session.commit()

    result = validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert result.status == "generated"
    assert result.validation_report is not None
    assert result.validation_report.passed is False
    assert result.validation_report.error_count >= 1

    with pytest.raises(HTTPException) as exc_info:
        workflow.request_approval(
            db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
        )
    assert exc_info.value.status_code == 409


def test_prior_period_movement_flags_large_swings_as_warning(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    prior_date = date(2026, 2, 28)
    prior_period_id = db_session.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == prior_date,
        )
    )
    assert prior_period_id is not None
    regulatory_liquidity.create_liquidity_run(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=prior_period_id, scenario_code="baseline"
        ),
    )
    prior_package = generation.generate_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="BSD3", reporting_date=prior_date),
    )
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, prior_package.id)
    workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, prior_package.id, PackageApprovalRequestCreate()
    )
    workflow.decide_approval(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        prior_package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    workflow.submit_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        prior_package.id,
        channel="manual",
        external_ref="BOG-RCPT-FEB",
    )

    # Force a >25% swing: halve the submitted February HQLA total in place.
    prior_row = _package_row(db_session, prior_package.id)
    snapshot = dict(prior_row.snapshot)
    totals = [dict(row) for row in snapshot["totals"]]
    for row in totals:
        if row["code"] == "hqla_total_ghs":
            row["value"] = str(Decimal(row["value"]) / 2)
    snapshot["totals"] = totals
    prior_row.snapshot = snapshot
    db_session.commit()

    current = _generate(db_session)
    validated = validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, current.id)
    assert validated.status == "validated"  # WARNINGs never block validation
    report = validated.validation_report
    assert report is not None
    assert report.passed is True
    movements = [
        finding
        for finding in report.findings
        if finding.rule == "package.prior_period_movement" and finding.severity == "WARNING"
    ]
    assert len(movements) == 1
    assert "hqla_total_ghs" in movements[0].detail
    assert "2026-02-28" in movements[0].detail


def test_calendar_links_current_package_and_grades_rag(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package = _generate(db_session)

    # As of 2026-04-05 the March month-end BSD3 filing (due April 9) is due soon
    # and covered by the generated package.
    obligations = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 1, as_of=date(2026, 4, 5)
    ).obligations
    bsd3 = [
        item
        for item in obligations
        if item.return_code == "BSD3" and item.reporting_date == REPORTING_DATE
    ]
    assert len(bsd3) == 1
    assert bsd3[0].due_date == date(2026, 4, 9)
    assert bsd3[0].package_id == package.id
    assert bsd3[0].package_status == "generated"
    assert bsd3[0].rag == "due_soon"

    # Past the deadline without a submission the same obligation is overdue.
    late = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 1, as_of=date(2026, 4, 20)
    ).obligations
    late_bsd3 = [
        item
        for item in late
        if item.return_code == "BSD3" and item.reporting_date == REPORTING_DATE
    ]
    assert late_bsd3 and late_bsd3[0].rag == "overdue"

    # Once submitted, the obligation is back on track.
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    workflow.decide_approval(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    workflow.submit_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        package.id,
        channel="manual",
        external_ref="BOG-RCPT-0002",
    )
    submitted = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 1, as_of=date(2026, 4, 20)
    ).obligations
    submitted_bsd3 = [
        item
        for item in submitted
        if item.return_code == "BSD3" and item.reporting_date == REPORTING_DATE
    ]
    assert submitted_bsd3 and submitted_bsd3[0].rag == "on_track"
    assert submitted_bsd3[0].package_status == "submitted"


def test_unknown_bank_is_tenant_scoped_404(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    stranger = TenantContext(organization_id=uuid4(), actor_user_id=uuid4())
    with pytest.raises(HTTPException) as exc_info:
        generation.generate_package(
            db_session,
            stranger,
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(return_code="BSD3", reporting_date=REPORTING_DATE),
        )
    assert exc_info.value.status_code == 404
