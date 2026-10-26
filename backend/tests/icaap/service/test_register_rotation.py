from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from queue import Queue
from threading import Event
from typing import Literal, cast

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.attestation.service import digests, register_state
from app.attestation.service import workflow as attestation
from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.schemas import BankKeyConnect, BankKeyRotate
from app.icaap.schemas.workspace import IcaapFreezeCreate
from app.icaap.service import freeze
from app.icaap.service import workflow as icaap_workflow
from app.identity.public import Bank
from app.identity.service import bank_encryption
from app.reporting.models.filing_workflow import PackageStageDecision
from app.reporting.models.packages import RegulatoryPackage, RegulatorySubmissionEvent
from app.reporting.schemas.packages import (
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryPackageCreate,
)
from app.reporting.service import generation, validation, workflow
from app.reporting.service.channels import OrassSandboxChannel
from app.reporting.service.channels.base import FiledArtifact
from tests.core import test_key_unavailable_numeric as encrypted
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.icaap.service import test_p3_freeze as freezing
from tests.icaap.service.p3_support import access_for
from tests.reporting.service import test_regulatory_reporting_channels as channels
from tests.support.factories.filing_chain import complete_chain
from tests.support.helpers import ORG_1

pytestmark = [pytest.mark.committing_db, pytest.mark.usefixtures("return_generation_authority")]
exporter_calls = channels.exporter_calls
ADMIN_KEY = BankKeyConnect(
    key_id="arn:aws:kms:us-east-1:111122223333:key/synthetic-register-source",
    region="us-east-1",
    owner_account="111122223333",
)
ADMIN_ROTATION = BankKeyRotate(
    key_id="arn:aws:kms:us-east-1:111122223333:key/synthetic-register-destination",
    region="us-east-1",
    owner_account="111122223333",
    reason="Synthetic administrator rotation during filing.",
)


@pytest.fixture
def postgres(db_session: Session) -> Engine:
    engine = db_session.get_bind()
    if not isinstance(engine, Engine) or engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent transactions are required.")
    return engine


def _pid(db: Session) -> int:
    return cast(int, db.scalar(text("SELECT pg_backend_pid()")))


def _prepare_key(db: Session, monkeypatch: pytest.MonkeyPatch, *, connected: bool) -> None:
    provider = LocalKeyProvider()
    provider.add_key(ADMIN_KEY.reference())
    provider.add_key(ADMIN_ROTATION.reference())

    def resolve(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", resolve)
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    bank.storage_slug = "synthetic-admin-concurrency"
    db.commit()
    if connected:
        bank_encryption.connect(db, channels.MAKER, SAMPLE_BANK_ID, ADMIN_KEY)
        db.commit()


def _change_key(engine: Engine, started: Queue[int], action: Literal["connect", "rotate"]) -> None:
    with Session(engine) as writer:
        writer.info["organization_id"] = ORG_1
        started.put(_pid(writer))
        if action == "connect":
            bank_encryption.connect(writer, channels.MAKER, SAMPLE_BANK_ID, ADMIN_KEY)
        else:
            bank_encryption.rotate(writer, channels.MAKER, SAMPLE_BANK_ID, ADMIN_ROTATION)


def _assert_blocked(engine: Engine, blocker: int, waiter: int, rotation: Future[None]) -> None:
    deadline = time.monotonic() + 5
    blocked = False
    with engine.connect() as observer:
        while not blocked and not rotation.done() and time.monotonic() < deadline:
            blocked = cast(
                bool,
                observer.scalar(
                    text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"),
                    {"blocker": blocker, "waiter": waiter},
                ),
            )
            if not blocked:
                time.sleep(0.01)
    assert blocked


@pytest.mark.parametrize("kind", ["ordinary", "icaap"])
@pytest.mark.parametrize("action", ["connect", "rotate"])
@pytest.mark.usefixtures("extra_frameworks")
def test_register_projection_pins_rotation_through_package_mint(
    canonical_book: Session,
    postgres: Engine,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["ordinary", "icaap"],
    action: Literal["connect", "rotate"],
) -> None:
    db = canonical_book
    _prepare_key(db, monkeypatch, connected=action == "rotate")
    encrypted.register(db)
    access = access_for(db)
    cycle = None
    if kind == "icaap":
        access, _reviewer, _approver, cycle = freezing._build(db)  # pyright: ignore[reportPrivateUsage]
    committed = digests.register_state_digest(
        register_state.project_register_state(db, access.ctx, SAMPLE_BANK_ID)
    )
    reader = _pid(db)
    started: Queue[int] = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        rotation = pool.submit(_change_key, postgres, started, action)
        try:
            _assert_blocked(postgres, reader, started.get(timeout=5), rotation)
            if cycle is None:
                read = generation.generate_package(
                    db,
                    access.ctx,
                    SAMPLE_BANK_ID,
                    RegulatoryPackageCreate(
                        return_code="BSD14", reporting_date=channels.REPORTING_DATE
                    ),
                )
                package_id = read.id
            else:
                stages = icaap_workflow.get_stages(db, access, cycle.id)
                frozen = freeze.freeze_cycle(
                    db,
                    access,
                    cycle.id,
                    IcaapFreezeCreate(
                        review_digest=stages.review_digest, reason="Seal the synthetic report."
                    ),
                )
                package_id = frozen.package.id
        finally:
            db.rollback()
        rotation.result(timeout=5)
    package = db.get(RegulatoryPackage, package_id)
    assert package is not None and package.register_state_digest == committed
    if kind == "icaap":
        metadata = cast(Mapping[str, object], package.snapshot["metadata"])
        icaap = cast(Mapping[str, object], metadata["icaap"])
        profile = cast(Mapping[str, object], icaap["institution_profile"])
        assert profile["register_state_digest"] == package.register_state_digest
        assert package.source_runs
    current = digests.register_state_digest(
        register_state.project_register_state(db, access.ctx, SAMPLE_BANK_ID)
    )
    assert current != committed
    with pytest.raises(attestation.AttestationConflict, match="register_state_changed"):
        attestation.ensure_register_current(db, access.ctx, package)


def _prepared_submission(
    db: Session, monkeypatch: pytest.MonkeyPatch, *, connected: bool
) -> RegulatoryPackage:
    channels._seed_approved_package(db)  # pyright: ignore[reportPrivateUsage]
    _prepare_key(db, monkeypatch, connected=connected)
    read = generation.generate_package(
        db,
        channels.MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=channels.REPORTING_DATE),
    )
    validation.validate_package(db, channels.MAKER, SAMPLE_BANK_ID, read.id)
    package = db.get(RegulatoryPackage, read.id)
    assert package is not None
    return package


def _approved_submission(db: Session, monkeypatch: pytest.MonkeyPatch) -> RegulatoryPackage:
    package = _prepared_submission(db, monkeypatch, connected=True)
    workflow.request_approval(
        db, channels.MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    complete_chain(
        db,
        package,
        approver=channels.CHECKER.actor_user_id,
        validator=channels.VALIDATOR.actor_user_id,
    )
    db.commit()
    return package


@pytest.mark.parametrize("stage", ["start", "approve"])
@pytest.mark.parametrize("action", ["connect", "rotate"])
def test_admin_key_change_waits_for_guarded_stage_insert(
    canonical_book: Session,
    postgres: Engine,
    monkeypatch: pytest.MonkeyPatch,
    stage: Literal["start", "approve"],
    action: Literal["connect", "rotate"],
) -> None:
    db = canonical_book
    package = _prepared_submission(db, monkeypatch, connected=action == "rotate")
    package_id = package.id
    if stage == "approve":
        workflow.request_approval(
            db, channels.MAKER, SAMPLE_BANK_ID, package_id, PackageApprovalRequestCreate()
        )
    attestation.ensure_register_current(db, channels.MAKER, package)
    reader = _pid(db)
    started: Queue[int] = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        change = pool.submit(_change_key, postgres, started, action)
        try:
            _assert_blocked(postgres, reader, started.get(timeout=5), change)
            if stage == "start":
                workflow.request_approval(
                    db, channels.MAKER, SAMPLE_BANK_ID, package_id, PackageApprovalRequestCreate()
                )
            else:
                workflow.decide_approval(
                    db,
                    channels.CHECKER,
                    SAMPLE_BANK_ID,
                    package_id,
                    PackageApprovalDecisionCreate(action="approved"),
                )
        finally:
            db.rollback()
        change.result(timeout=5)
    decision = db.scalar(
        select(PackageStageDecision).where(
            PackageStageDecision.organization_id == ORG_1,
            PackageStageDecision.package_id == package_id,
            PackageStageDecision.stage_seq == (1 if stage == "start" else 2),
        )
    )
    assert decision is not None
    assert decision.decision == ("submitted" if stage == "start" else "approved")
    with pytest.raises(attestation.AttestationConflict, match="register_state_changed"):
        attestation.ensure_register_current(db, channels.MAKER, package)


def test_rotation_waits_for_remote_receipt_and_local_submission_commit(
    canonical_book: Session,
    postgres: Engine,
    monkeypatch: pytest.MonkeyPatch,
    exporter_calls: list[str],
) -> None:
    db = canonical_book
    package = _approved_submission(db, monkeypatch)
    package_id = package.id
    version = db.scalar(
        select(BankEncryptionKey.current_version_id).where(
            BankEncryptionKey.bank_id == SAMPLE_BANK_ID,
            BankEncryptionKey.organization_id == ORG_1,
        )
    )
    db.commit()
    accepted, release = Event(), Event()
    sender_pid: Queue[int] = Queue()
    rotation_pid: Queue[int] = Queue()
    receipts: Queue[str] = Queue()
    submit = OrassSandboxChannel.submit

    def remote(
        channel: OrassSandboxChannel, pack: RegulatoryPackage, artifacts: Sequence[FiledArtifact]
    ) -> str:
        receipt = submit(channel, pack, artifacts)
        receipts.put(receipt)
        accepted.set()
        assert release.wait(10)
        return receipt

    def send() -> None:
        with Session(postgres) as sender:
            sender.info["organization_id"] = ORG_1
            sender_pid.put(_pid(sender))
            workflow.submit_package_via_channel(
                sender,
                channels.MAKER,
                SAMPLE_BANK_ID,
                package_id,
                channel_override="orass_sandbox",
            )

    monkeypatch.setattr(OrassSandboxChannel, "submit", remote)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(send)
        try:
            if not accepted.wait(5):
                sending.result(timeout=5)
                pytest.fail("The sandbox did not accept the filing.")
            rotation = pool.submit(_change_key, postgres, rotation_pid, "rotate")
            _assert_blocked(
                postgres, sender_pid.get(timeout=5), rotation_pid.get(timeout=5), rotation
            )
        finally:
            release.set()
        sending.result(timeout=5)
        rotation.result(timeout=5)
    db.expire_all()
    submitted = db.get(RegulatoryPackage, package_id)
    assert submitted is not None and submitted.status == "submitted"
    event = db.scalar(
        select(RegulatorySubmissionEvent).where(
            RegulatorySubmissionEvent.package_id == package_id,
            RegulatorySubmissionEvent.organization_id == ORG_1,
            RegulatorySubmissionEvent.event == "submitted",
        )
    )
    assert event is not None and event.external_ref == receipts.get(timeout=5)
    assert (
        db.scalar(
            select(BankEncryptionKey.current_version_id).where(
                BankEncryptionKey.bank_id == SAMPLE_BANK_ID,
                BankEncryptionKey.organization_id == ORG_1,
            )
        )
        != version
    )
    attestation.ensure_register_current(db, channels.MAKER, submitted)
    assert exporter_calls
