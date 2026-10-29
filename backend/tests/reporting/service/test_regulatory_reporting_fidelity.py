"""W1 decision-fidelity suite: declined, supervisor comments, resubmission
requests + revisions, and snapshot seal.

Mirrors the documented ORASS lifecycle (LRT Portal User Guide v1.0):
Rejected = returned for correction; Declined = final; corrections to a
submitted/acknowledged return require a granted Request Resubmission and the
next submission carries revision 1.1.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    BankReportingPeriod,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    User,
)
from app.reporting.schemas.packages import (
    ChannelConfigPut,
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryPackageCreate,
    ResubmissionRequestCreate,
)
from app.reporting.service import channel_config, generation, validation, workflow
from app.reporting.service.common import read_package
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_liquidity
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.factories.attestation import relax_signing
from tests.support.factories.filing_chain import complete_chain

pytestmark = pytest.mark.usefixtures("return_generation_authority")

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)
CHECKER = TenantContext(
    organization_id=DEMO_ORG_ID,
    actor_user_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    authorization_version=1,
)
REPORTING_DATE = date(2026, 3, 31)


def _seed_with_baseline_run(db: Session) -> None:
    materialize_canonical_test_book(db)
    # This suite is about REGULATOR DECISIONS (reject, decline, resubmission),
    # not about who signed. Opt this return out of the platform's mandatory
    # signing the way an administrator would; the gate is proved in
    # tests/attestation/service/test_attestation_spine.py.
    relax_signing(db, organization_id=DEMO_ORG_ID, return_code="LCR-NSFR")
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


def _fake_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    def exporter(db, ctx, package, kind):
        artifact = RegulatoryPackageArtifact(
            organization_id=package.organization_id,
            package_id=package.id,
            kind=kind,
            object_path=f"test/{package.id}/{package.return_code}.{kind}",
            checksum_sha256="0" * 64,
            size_bytes=128,
            created_at=datetime.now(UTC),
        )
        db.add(artifact)
        db.flush()
        return artifact

    monkeypatch.setattr(workflow, "_resolve_exporter", lambda: exporter)


def _approved_package(db: Session, monkeypatch: pytest.MonkeyPatch):
    _fake_exporter(monkeypatch)
    package = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=REPORTING_DATE),
    )
    validation.validate_package(db, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(
        db, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate(reason=None)
    )
    workflow.decide_approval(
        db,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved", reason=None),
    )
    # The Approver's decision hands the return to the Validator; the Validator's
    # completes the chain and is what makes it filable. This suite is about
    # ORASS fidelity, so it walks that stage rather than asserting the old
    # two-step status.
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == package.id))
    assert row is not None
    complete_chain(db, row)
    db.commit()
    return read_package(db, row)


def _configure_sandbox(db: Session, **config) -> None:
    channel_config.put_channel_config(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        "orass_sandbox",
        ChannelConfigPut(config=config, credentials=None),
    )


# ---------------------------------------------------------------------------
# Declined (regulator final refusal) + supervisor comments
# ---------------------------------------------------------------------------


def test_decline_is_terminal_and_seals_supervisor_comments(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_with_baseline_run(db_session)
    _configure_sandbox(db_session, sandbox_behavior="decline")
    approved = _approved_package(db_session, monkeypatch)
    workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, approved.id, channel_override="orass_sandbox"
    )
    poll = workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, approved.id)
    assert poll.poll_status == "declined"
    assert poll.package.status == "declined"
    assert poll.package.regulator_comments is not None
    assert "decline" in poll.package.regulator_comments.lower()
    # Declined is terminal: no further submission or polling.
    with pytest.raises(HTTPException) as exc:
        workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, approved.id)
    assert exc.value.status_code == 409


def test_rejection_seals_supervisor_comments_for_view_comments_panel(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_with_baseline_run(db_session)
    _configure_sandbox(db_session, sandbox_behavior="reject")
    approved = _approved_package(db_session, monkeypatch)
    workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, approved.id, channel_override="orass_sandbox"
    )
    poll = workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, approved.id)
    assert poll.poll_status == "rejected"
    assert poll.package.regulator_comments is not None
    assert "SIM-LQ-104" in poll.package.regulator_comments


# ---------------------------------------------------------------------------
# Resubmission requests + revision numbering
# ---------------------------------------------------------------------------


def test_acknowledged_package_requires_granted_resubmission_to_regenerate(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_with_baseline_run(db_session)
    _configure_sandbox(db_session, sandbox_behavior="ack")
    approved = _approved_package(db_session, monkeypatch)
    workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, approved.id, channel_override="orass_sandbox"
    )
    poll = workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, approved.id)
    assert poll.package.status == "acknowledged"
    assert poll.package.submission_revision == "1.0"

    # Without a granted resubmission request, regeneration is refused.
    with pytest.raises(HTTPException) as exc:
        generation.generate_package(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=REPORTING_DATE),
        )
    assert exc.value.status_code == 409
    assert "resubmission" in str(exc.value.detail).lower()

    # Request resubmission -> sandbox grants -> regeneration is authorized once.
    request = workflow.request_resubmission(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        approved.id,
        ResubmissionRequestCreate(reason="The wrong board extract was attached."),
    )
    assert request.status == "granted"
    v2 = generation.generate_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=REPORTING_DATE),
    )
    assert v2.version == approved.version + 1
    requests = workflow.list_resubmission_requests(db_session, MAKER, SAMPLE_BANK_ID, approved.id)
    assert requests.requests[0].consumed_by_package_id == v2.id

    # The corrected submission carries ORASS revision 1.1.
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, v2.id)
    workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, v2.id, PackageApprovalRequestCreate(reason=None)
    )
    workflow.decide_approval(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        v2.id,
        PackageApprovalDecisionCreate(action="approved", reason=None),
    )
    v2_row = db_session.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == v2.id))
    assert v2_row is not None
    complete_chain(db_session, v2_row)
    db_session.commit()
    submitted = workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, v2.id, channel_override="orass_sandbox"
    )
    assert submitted.submission_revision == "1.1"


def test_denied_resubmission_keeps_acknowledged_package_immutable(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_with_baseline_run(db_session)
    _configure_sandbox(db_session, sandbox_behavior="ack", resubmission_behavior="deny")
    approved = _approved_package(db_session, monkeypatch)
    workflow.submit_package_via_channel(
        db_session, MAKER, SAMPLE_BANK_ID, approved.id, channel_override="orass_sandbox"
    )
    workflow.poll_submission(db_session, MAKER, SAMPLE_BANK_ID, approved.id)
    request = workflow.request_resubmission(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        approved.id,
        ResubmissionRequestCreate(reason="Correction requested."),
    )
    assert request.status == "denied"
    with pytest.raises(HTTPException) as exc:
        generation.generate_package(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=REPORTING_DATE),
        )
    assert exc.value.status_code == 409


def test_resubmission_requires_submitted_or_acknowledged(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_with_baseline_run(db_session)
    approved = _approved_package(db_session, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        workflow.request_resubmission(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            approved.id,
            ResubmissionRequestCreate(reason="Too early."),
        )
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Snapshot content seal
# ---------------------------------------------------------------------------


def test_snapshot_sha256_is_sealed_and_value_based(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package = generation.generate_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=REPORTING_DATE),
    )
    row = db_session.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == package.id))
    assert row is not None
    assert row.snapshot_sha256 == generation.snapshot_content_hash(row.snapshot)
    # Key order must not matter (canonical-JSON discipline).
    reordered = json.loads(json.dumps(row.snapshot, sort_keys=False))
    assert generation.snapshot_content_hash(reordered) == row.snapshot_sha256
