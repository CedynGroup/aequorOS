from __future__ import annotations

from datetime import date
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    BankReportingPeriod,
    RegulatoryChannelConfig,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatorySubmissionEvent,
    User,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import (
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryPackageCreate,
)
from app.services import regulatory_liquidity
from app.services.regulatory_reporting import calendar, generation, validation, workflow
from app.services.regulatory_reporting.channels import (
    ACT_930_PENALTY_REMINDER,
    CONFIRMED_CONSULTATION_ADDRESS,
    SANDBOX_NOTE,
    ChannelDowntimeError,
    ChannelPreconditionError,
    EmailFallbackChannel,
    OrassSandboxChannel,
)
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


# ---------------------------------------------------------------------------
# Channel unit tests (no database)
# ---------------------------------------------------------------------------


def _transient_package(status: str = "approved") -> RegulatoryPackage:
    return RegulatoryPackage(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        return_code="BSD3",
        return_family="liquidity",
        reporting_date=REPORTING_DATE,
        frequency="monthly",
        status=status,
        version=1,
        snapshot={"institution": {"short_name": "SBL", "name": "Sample Bank Ltd"}},
    )


def _transient_artifact(kind: str = "xlsx") -> RegulatoryPackageArtifact:
    return RegulatoryPackageArtifact(
        organization_id=DEMO_ORG_ID,
        kind=kind,
        object_path=f"bog_returns/2026-03-31/pkg/BSD3.{kind}",
        checksum_sha256="a" * 64,
        size_bytes=2048,
    )


def _poll_event(external_ref: str) -> RegulatorySubmissionEvent:
    return RegulatorySubmissionEvent(
        organization_id=DEMO_ORG_ID,
        channel="orass_sandbox",
        event="status_poll",
        external_ref=external_ref,
        detail={},
    )


def test_sandbox_submit_labels_everything_as_simulation() -> None:
    channel = OrassSandboxChannel()
    ref = channel.submit(_transient_package(), [_transient_artifact()])
    assert ref.startswith("SANDBOX-ORASS-BSD3-")
    assert channel.last_detail["sandbox"] is True
    assert channel.last_detail["note"] == SANDBOX_NOTE
    assert "not publicly documented" in SANDBOX_NOTE
    assert channel.last_detail["response"].startswith("SANDBOX-")
    assert channel.last_detail["artifact_kinds"] == ["xlsx"]


def test_sandbox_submit_precondition_failures() -> None:
    channel = OrassSandboxChannel()
    with pytest.raises(ChannelPreconditionError) as unapproved:
        channel.submit(_transient_package(status="generated"), [_transient_artifact()])
    assert "approved" in unapproved.value.operator_message

    with pytest.raises(ChannelPreconditionError) as no_artifacts:
        channel.submit(_transient_package(), [])
    assert "artifact" in no_artifacts.value.operator_message


def test_sandbox_downtime_config_raises_typed_error() -> None:
    channel = OrassSandboxChannel(config={"downtime": True})
    with pytest.raises(ChannelDowntimeError) as exc_info:
        channel.submit(_transient_package(), [_transient_artifact()])
    assert "email fallback" in exc_info.value.operator_message
    assert "BG/FMD/2026/07" in exc_info.value.operator_message
    # str() renders only the operator message (no internals leak).
    assert str(exc_info.value) == exc_info.value.operator_message


def test_sandbox_poll_ack_acknowledges_on_first_poll() -> None:
    channel = OrassSandboxChannel(config={"sandbox_behavior": "ack"})
    status, detail = channel.poll_with_detail("SANDBOX-ORASS-BSD3-abc")
    assert status == "acknowledged"
    assert detail["sandbox"] is True
    assert detail["poll_number"] == 1
    assert detail["response"].startswith("SANDBOX-ACK-")


def test_sandbox_poll_reject_carries_simulated_bog_style_message() -> None:
    channel = OrassSandboxChannel(config={"sandbox_behavior": "reject"})
    status, detail = channel.poll_with_detail("SANDBOX-ORASS-BSD3-abc")
    assert status == "rejected"
    assert detail["response"].startswith("SANDBOX-REJECTED-")
    assert "validation" in detail["message"]
    assert "SANDBOX" in detail["message"]  # never passed off as a real BoG message


def test_sandbox_poll_slow_is_deterministic_from_event_chain() -> None:
    ref = "SANDBOX-ORASS-BSD3-abc"
    config = {"sandbox_behavior": "slow"}

    first = OrassSandboxChannel(config=config, prior_events=[])
    assert first.poll(ref) == "pending"
    # Determinism: a fresh instance with identical inputs answers identically.
    assert OrassSandboxChannel(config=config, prior_events=[]).poll(ref) == "pending"

    second = OrassSandboxChannel(config=config, prior_events=[_poll_event(ref)])
    assert second.poll(ref) == "pending"

    third = OrassSandboxChannel(config=config, prior_events=[_poll_event(ref), _poll_event(ref)])
    assert third.poll(ref) == "acknowledged"

    # Polls for other refs don't advance this ref's counter.
    other = OrassSandboxChannel(config=config, prior_events=[_poll_event("SANDBOX-ORASS-BSD3-zzz")])
    assert other.poll(ref) == "pending"


def test_sandbox_unknown_behavior_falls_back_to_ack() -> None:
    channel = OrassSandboxChannel(config={"sandbox_behavior": "explode"})
    assert channel.behavior == "ack"


def test_email_bundle_contents_and_pending_flag() -> None:
    channel = EmailFallbackChannel(config={"institution_code": "SBL-001"})
    package = _transient_package()
    ref = channel.submit(package, [_transient_artifact(), _transient_artifact("pdf")])
    assert ref.startswith("EMAIL-BSD3-")

    detail = channel.last_detail
    assert detail["pending_orass_reupload"] is True
    assert detail["subject"] == "[SBL-001] [BSD3] [2026-03-31] – submitted under ORASS downtime"
    assert detail["penalty_reminder"] == ACT_930_PENALTY_REMINDER
    assert "500 penalty units" in detail["penalty_reminder"]
    assert "50 penalty units" in detail["penalty_reminder"]

    guidance = detail["recipient_guidance"]
    assert guidance["confirmed_consultation_address"] == CONFIRMED_CONSULTATION_ADDRESS
    assert "CONFIRMED" in guidance["confirmed_consultation_note"]
    assert guidance["downtime_return_address"] is None  # UNKNOWN in the public record
    assert "UNKNOWN" in guidance["downtime_return_note"]
    assert "supervision contact" in guidance["downtime_return_note"]

    instructions = detail["instructions"]
    assert CONFIRMED_CONSULTATION_ADDRESS in instructions
    assert "BG/FMD/2026/07" in instructions
    assert "deemed" in instructions
    assert "BSD3.xlsx" in instructions and "BSD3.pdf" in instructions
    assert [entry["kind"] for entry in detail["attachments"]] == ["xlsx", "pdf"]


def test_email_configured_recipient_is_surfaced() -> None:
    channel = EmailFallbackChannel(config={"fallback_recipient": "returns.desk@examplebank.test"})
    channel.submit(_transient_package(), [_transient_artifact()])
    detail = channel.last_detail
    assert (
        detail["recipient_guidance"]["downtime_return_address"] == "returns.desk@examplebank.test"
    )
    assert "returns.desk@examplebank.test" in detail["instructions"]


def test_email_submit_preconditions_and_poll_always_pending() -> None:
    channel = EmailFallbackChannel()
    with pytest.raises(ChannelPreconditionError):
        channel.submit(_transient_package(status="submitted"), [_transient_artifact()])
    with pytest.raises(ChannelPreconditionError):
        channel.submit(_transient_package(), [])

    status, detail = channel.poll_with_detail("EMAIL-BSD3-abc")
    assert status == "pending"
    assert detail["pending_orass_reupload"] is True


# ---------------------------------------------------------------------------
# Workflow integration (database; exporter seam faked)
# ---------------------------------------------------------------------------


@pytest.fixture
def exporter_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake the lazy exports seam: mint an artifact row, record the kind."""
    calls: list[str] = []

    def _fake_export(
        db: Session, ctx: TenantContext, package: RegulatoryPackage, kind: str
    ) -> RegulatoryPackageArtifact:
        _ = ctx
        calls.append(kind)
        artifact = RegulatoryPackageArtifact(
            organization_id=package.organization_id,
            package_id=package.id,
            kind=kind,
            object_path=(
                f"bog_returns/{package.reporting_date.isoformat()}/"
                f"{package.id}/{package.return_code}.{kind}"
            ),
            checksum_sha256="b" * 64,
            size_bytes=4096,
        )
        db.add(artifact)
        db.flush()
        return artifact

    monkeypatch.setattr(workflow, "_resolve_exporter", lambda: _fake_export)
    return calls


def _seed_approved_package(db: Session) -> RegulatoryPackage:
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
    package = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="BSD3", reporting_date=REPORTING_DATE),
    )
    validation.validate_package(db, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(db, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate())
    approved = workflow.decide_approval(
        db,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    assert approved.status == "approved"
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == package.id))
    assert row is not None
    return row


def _set_channel_config(db: Session, channel: str, config: dict[str, Any]) -> None:
    row = db.scalar(
        select(RegulatoryChannelConfig).where(
            RegulatoryChannelConfig.organization_id == DEMO_ORG_ID,
            RegulatoryChannelConfig.bank_id == SAMPLE_BANK_ID,
            RegulatoryChannelConfig.channel == channel,
        )
    )
    if row is None:
        row = RegulatoryChannelConfig(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            channel=channel,
            config=config,
        )
        db.add(row)
    else:
        row.config = config
    db.commit()


def test_submit_auto_exports_xlsx_when_no_artifacts(
    db_session: Session, exporter_calls: list[str]
) -> None:
    package = _seed_approved_package(db_session)
    submitted = workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
    )
    assert submitted.status == "submitted"
    assert exporter_calls == ["xlsx"]

    events = workflow.list_submission_events(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert events.total == 1
    event = events.events[0]
    assert event.channel == "orass_sandbox"
    assert event.event == "submitted"
    assert event.external_ref is not None
    assert event.external_ref.startswith("SANDBOX-ORASS-BSD3-")
    assert event.detail["sandbox"] is True
    assert event.detail["note"] == SANDBOX_NOTE
    assert event.detail["auto_exported_kinds"] == ["xlsx"]

    # A second submit must not double-export: artifacts already exist.
    with pytest.raises(HTTPException):
        workflow.submit_package_via_channel(
            db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
        )
    assert exporter_calls == ["xlsx"]


def test_submit_then_poll_acknowledges(db_session: Session, exporter_calls: list[str]) -> None:
    package = _seed_approved_package(db_session)
    workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
    )
    result = workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert result.poll_status == "acknowledged"
    assert result.package.status == "acknowledged"
    assert result.event.event == "status_poll"
    assert result.event.detail["result"] == "acknowledged"

    events = workflow.list_submission_events(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert [event.event for event in events.events] == [
        "acknowledged",
        "status_poll",
        "submitted",
    ]

    # Terminal: polling again is a 409.
    with pytest.raises(HTTPException) as exc_info:
        workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert exc_info.value.status_code == 409


def test_poll_reject_records_regulator_rejection(
    db_session: Session, exporter_calls: list[str]
) -> None:
    package = _seed_approved_package(db_session)
    _set_channel_config(db_session, "orass_sandbox", {"sandbox_behavior": "reject"})
    workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
    )
    result = workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert result.poll_status == "rejected"
    assert result.package.status == "rejected"
    events = workflow.list_submission_events(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    rejected = [event for event in events.events if event.event == "rejected"]
    assert len(rejected) == 1
    assert "SANDBOX" in rejected[0].detail["message"]


def test_poll_slow_pending_twice_then_acknowledged(
    db_session: Session, exporter_calls: list[str]
) -> None:
    package = _seed_approved_package(db_session)
    _set_channel_config(db_session, "orass_sandbox", {"sandbox_behavior": "slow"})
    workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
    )
    outcomes = [
        workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, package.id).poll_status
        for _ in range(3)
    ]
    assert outcomes == ["pending", "pending", "acknowledged"]
    events = workflow.list_submission_events(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    polls = [event for event in events.events if event.event == "status_poll"]
    assert len(polls) == 3
    assert sorted(event.detail["poll_number"] for event in polls) == [1, 2, 3]


def test_downtime_maps_to_structured_409_pointing_at_email_fallback(
    db_session: Session, exporter_calls: list[str]
) -> None:
    package = _seed_approved_package(db_session)
    _set_channel_config(db_session, "orass_sandbox", {"downtime": True})
    with pytest.raises(HTTPException) as exc_info:
        workflow.submit_package_via_channel(
            db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
        )
    assert exc_info.value.status_code == 409
    detail = cast("dict[str, Any]", exc_info.value.detail)
    assert isinstance(detail, dict)
    assert detail["error_code"] == "channel_downtime"
    assert detail["fallback"]["channel"] == "email"
    assert detail["fallback"]["endpoint"].endswith(f"/regulatory-packages/{package.id}/submit")
    # The package stays approved: nothing was submitted.
    db_session.rollback()
    row = db_session.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == package.id))
    assert row is not None and row.status == "approved"


def test_email_then_orass_reupload_clears_pending_flag(
    db_session: Session, exporter_calls: list[str]
) -> None:
    package = _seed_approved_package(db_session)

    submitted = workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="email"
    )
    assert submitted.status == "submitted"
    events = workflow.list_submission_events(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    email_event = events.events[0]
    assert email_event.channel == "email"
    assert email_event.external_ref is not None
    assert email_event.external_ref.startswith("EMAIL-BSD3-")
    assert email_event.detail["pending_orass_reupload"] is True
    assert CONFIRMED_CONSULTATION_ADDRESS in email_event.detail["instructions"]

    row = db_session.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == package.id))
    assert row is not None
    assert workflow.has_pending_orass_reupload(db_session, row) is True

    # Calendar/RAG treats the pending email submission as not-yet-complete.
    late = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 1, as_of=date(2026, 4, 20)
    ).obligations
    bsd3 = [
        item
        for item in late
        if item.return_code == "BSD3" and item.reporting_date == REPORTING_DATE
    ]
    assert bsd3 and bsd3[0].package_status == "submitted"
    assert bsd3[0].rag == "overdue"

    # A second email submission is refused: ORASS re-upload is the only way out.
    with pytest.raises(HTTPException) as email_again:
        workflow.submit_package_via_channel(
            db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="email"
        )
    assert email_again.value.status_code == 409
    assert "ORASS" in str(email_again.value.detail)

    # The ORASS re-upload (submitted -> submitted) clears the flag.
    reuploaded = workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
    )
    assert reuploaded.status == "submitted"
    assert workflow.has_pending_orass_reupload(db_session, row) is False
    events = workflow.list_submission_events(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    orass_event = events.events[0]
    assert orass_event.channel == "orass_sandbox"
    assert orass_event.detail["pending_orass_reupload"] is False
    assert orass_event.detail["reupload_of"] == email_event.external_ref

    complete = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 1, as_of=date(2026, 4, 20)
    ).obligations
    bsd3 = [
        item
        for item in complete
        if item.return_code == "BSD3" and item.reporting_date == REPORTING_DATE
    ]
    assert bsd3 and bsd3[0].rag == "on_track"

    # Once re-uploaded, another submit is refused and polling acknowledges.
    with pytest.raises(HTTPException):
        workflow.submit_package_via_channel(
            db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
        )
    result = workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert result.poll_status == "acknowledged"
    assert result.package.status == "acknowledged"


def test_unapproved_submit_is_409(db_session: Session, exporter_calls: list[str]) -> None:
    seed_sample_bank(db_session)
    period_id = db_session.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    regulatory_liquidity.create_liquidity_run(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    package = generation.generate_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="BSD3", reporting_date=REPORTING_DATE),
    )
    with pytest.raises(HTTPException) as exc_info:
        workflow.submit_package_via_channel(
            db_session, MAKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
        )
    assert exc_info.value.status_code == 409
    assert exporter_calls == []  # precondition fails before any export

    with pytest.raises(HTTPException) as poll_info:
        workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert poll_info.value.status_code == 409


def test_email_fallback_instructions_preview_without_submitting(
    db_session: Session, exporter_calls: list[str]
) -> None:
    package = _seed_approved_package(db_session)
    _set_channel_config(db_session, "email", {"institution_code": "SBL-001"})
    preview = workflow.email_fallback_instructions(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert preview.package_id == package.id
    assert preview.pending_orass_reupload is True
    assert preview.subject.startswith("[SBL-001] [BSD3] [2026-03-31]")
    assert preview.recipient_guidance.confirmed_consultation_address == (
        CONFIRMED_CONSULTATION_ADDRESS
    )
    assert preview.attachments == []  # nothing exported yet; preview flags it
    assert "No artifacts exported yet" in preview.instructions
    assert preview.penalty_reminder == ACT_930_PENALTY_REMINDER
    # Preview records nothing.
    events = workflow.list_submission_events(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert events.total == 0
