"""W1 decision-fidelity suite: declined, supervisor comments, resubmission
requests + revisions, snapshot seal, and the production ORASS API channel.

Mirrors the documented ORASS lifecycle (LRT Portal User Guide v1.0):
Rejected = returned for correction; Declined = final; corrections to a
submitted/acknowledged return require a granted Request Resubmission and the
next submission carries revision 1.1.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import UUID

import httpx
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
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import (
    ChannelConfigPut,
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryPackageCreate,
    ResubmissionRequestCreate,
)
from app.services import regulatory_liquidity
from app.services.regulatory_reporting import channel_config, generation, validation, workflow
from app.services.regulatory_reporting.channels.errors import (
    ChannelDowntimeError,
    ChannelPreconditionError,
)
from app.services.regulatory_reporting.channels.orass_api import OrassApiChannel
from tests.factories.attestation import relax_signing
from tests.factories.outbound import stub_dns, stub_public_dns
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
CHECKER = TenantContext(
    organization_id=DEMO_ORG_ID,
    actor_user_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
)
REPORTING_DATE = date(2026, 3, 31)


def _seed_with_baseline_run(db: Session) -> None:
    materialize_canonical_test_book(db)
    # This suite is about REGULATOR DECISIONS (reject, decline, resubmission),
    # not about who signed. Opt this return out of the platform's mandatory
    # signing the way an administrator would; the gate is proved in
    # tests/services/test_attestation_spine.py.
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
    return workflow.decide_approval(
        db,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved", reason=None),
    )


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


# ---------------------------------------------------------------------------
# ORASS API channel (production client, provisional wire contract)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _resolvable_orass(monkeypatch: pytest.MonkeyPatch) -> None:
    """``api_base_url`` is tenant-supplied, so the channel resolves it through
    the egress guard before every request; stub DNS to stay offline."""
    stub_public_dns(monkeypatch, "orass.example.test")


def _api_channel(handler, *, config=None, credentials=None) -> OrassApiChannel:
    return OrassApiChannel(
        config={"api_base_url": "https://orass.example.test", **(config or {})},
        credentials=credentials if credentials is not None else {"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )


def _approved_stub_package(db_session: Session) -> RegulatoryPackage:
    _seed_with_baseline_run(db_session)
    generated = generation.generate_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=REPORTING_DATE),
    )
    row = db_session.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == generated.id))
    assert row is not None
    row.status = "approved"  # direct fixture shortcut for channel unit tests
    return row


def _stub_artifact(package: RegulatoryPackage):
    return RegulatoryPackageArtifact(
        organization_id=package.organization_id,
        package_id=package.id,
        kind="xlsx",
        object_path="x",
        checksum_sha256="0" * 64,
        size_bytes=1,
        created_at=datetime.now(UTC),
    )


def test_orass_api_submit_returns_reference_and_labels_contract(
    db_session: Session,
) -> None:
    package = _approved_stub_package(db_session)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"reference": "LCRN01390", "status": "received"})

    channel = _api_channel(handler)
    ref = channel.submit(package, [_stub_artifact(package)])
    assert ref == "LCRN01390"
    assert seen["url"].endswith("/api/v1/returns/LCR-NSFR/submissions")
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["metadata"]["return_code"] == "LCR-NSFR"
    assert channel.last_detail["provisional_contract"] is True


def test_orass_api_connectivity_failure_maps_to_downtime(db_session: Session) -> None:
    package = _approved_stub_package(db_session)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    channel = _api_channel(handler)
    with pytest.raises(ChannelDowntimeError) as exc:
        channel.submit(package, [_stub_artifact(package)])
    assert "BG/FMD/2026/07" in str(exc.value)


def test_orass_api_5xx_maps_to_downtime_and_4xx_to_precondition(
    db_session: Session,
) -> None:
    package = _approved_stub_package(db_session)

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    with pytest.raises(ChannelDowntimeError):
        _api_channel(server_error).submit(package, [_stub_artifact(package)])

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    with pytest.raises(ChannelPreconditionError):
        _api_channel(unauthorized).submit(package, [_stub_artifact(package)])


def test_orass_api_poll_maps_statuses_and_carries_comments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "declined", "comments": "Not approvable as filed."}
        )

    status, detail = _api_channel(handler).poll_with_detail("LCRN01390")
    assert status == "declined"
    assert detail["comments"] == "Not approvable as filed."


def test_orass_api_requires_base_url_and_credentials(db_session: Session) -> None:
    package = _approved_stub_package(db_session)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json={})

    unconfigured = OrassApiChannel(
        config={}, credentials={"api_key": "k"}, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ChannelPreconditionError):
        unconfigured.submit(package, [_stub_artifact(package)])

    keyless = _api_channel(handler, credentials={})
    with pytest.raises(ChannelPreconditionError):
        keyless.submit(package, [_stub_artifact(package)])


def test_orass_api_resubmission_request_flow() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/submissions/LCRN01390/resubmission-requests")
        return httpx.Response(201, json={"status": "granted"})

    status, detail = _api_channel(handler).request_resubmission("LCRN01390", "Wrong attachment")
    assert status == "granted"
    assert detail["provisional_contract"] is True


# --- ORASS egress guard (audit P0-6) ---------------------------------------

BLOCKED_ORASS_BASE_URLS = [
    "https://127.0.0.1",
    "https://localhost",
    "https://[::1]",
    "https://169.254.169.254",
    "https://metadata.google.internal",
    "https://10.0.0.5",
    "https://192.168.1.1",
    "https://172.16.0.1",
    "https://100.64.0.1",
    "https://0.0.0.0",
    "https://[::ffff:127.0.0.1]",
    "http://orass.example.test",  # scheme allow-list: TLS only
]


@pytest.mark.parametrize("base_url", BLOCKED_ORASS_BASE_URLS)
def test_orass_api_refuses_a_blocked_base_url(db_session: Session, base_url: str) -> None:
    """A tenant-set ``api_base_url`` must not become an internal socket, and a
    blocked target is a configuration fault — never regulator downtime (which
    would route the operator into the email fallback)."""
    package = _approved_stub_package(db_session)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the guard must refuse before any request is made")

    channel = _api_channel(handler, config={"api_base_url": base_url})
    with pytest.raises(ChannelPreconditionError) as blocked:
        channel.submit(package, [_stub_artifact(package)])
    assert not isinstance(blocked.value, ChannelDowntimeError)


def test_orass_api_refuses_a_base_url_that_resolves_internally(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A public-looking name whose DNS answer is the metadata service."""
    package = _approved_stub_package(db_session)
    stub_dns(monkeypatch, {"orass.example.test": ("169.254.169.254",)})

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the guard must refuse before any request is made")

    with pytest.raises(ChannelPreconditionError) as blocked:
        _api_channel(handler).submit(package, [_stub_artifact(package)])
    assert "169.254.169.254" not in str(blocked.value)


def test_orass_api_rejects_a_redirect_to_a_blocked_destination(db_session: Session) -> None:
    """The transport does not follow redirects, and the hook refuses one that
    points at a blocked destination rather than letting it through."""
    package = _approved_stub_package(db_session)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/"})

    with pytest.raises(ChannelPreconditionError) as blocked:
        _api_channel(handler).submit(package, [_stub_artifact(package)])
    assert "redirected" in str(blocked.value)
