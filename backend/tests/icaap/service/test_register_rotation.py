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
from app.icaap.schemas.workspace import IcaapFreezeCreate
from app.icaap.service import freeze
from app.icaap.service import workflow as icaap_workflow
from app.reporting.models.packages import RegulatoryPackage, RegulatorySubmissionEvent
from app.reporting.schemas.packages import PackageApprovalRequestCreate, RegulatoryPackageCreate
from app.reporting.service import generation, validation, workflow
from app.reporting.service.channels import OrassSandboxChannel
from app.reporting.service.channels.base import FiledArtifact
from tests.core import test_key_unavailable_numeric as encrypted
from tests.core.test_bank_field_encryption import NEXT
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.icaap.service import test_p3_freeze as freezing
from tests.icaap.service.p3_support import access_for
from tests.reporting.service import test_regulatory_reporting_channels as channels
from tests.support.factories.filing_chain import complete_chain
from tests.support.helpers import ORG_1

pytestmark = [pytest.mark.committing_db, pytest.mark.usefixtures("return_generation_authority")]
exporter_calls = channels.exporter_calls


@pytest.fixture
def postgres(db_session: Session) -> Engine:
    engine = db_session.get_bind()
    if not isinstance(engine, Engine) or engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent transactions are required.")
    return engine


def _pid(db: Session) -> int:
    return cast(int, db.scalar(text("SELECT pg_backend_pid()")))


def _rotate(engine: Engine, provider: LocalKeyProvider, started: Queue[int]) -> None:
    with Session(engine) as writer, writer.begin():
        writer.info["organization_id"] = ORG_1
        started.put(_pid(writer))
        row = registry.scoped_key(writer, bank_id=SAMPLE_BANK_ID, organization_id=ORG_1)
        registry.rotate(writer, row, NEXT, lambda _name: provider)


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
def test_register_projection_pins_rotation_through_package_mint(
    canonical_book: Session,
    postgres: Engine,
    extra_frameworks: None,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["ordinary", "icaap"],
) -> None:
    db = canonical_book
    provider = encrypted.connect(db, monkeypatch)
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
        rotation = pool.submit(_rotate, postgres, provider, started)
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


def _approved_submission(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> tuple[LocalKeyProvider, RegulatoryPackage]:
    channels._seed_approved_package(db)  # pyright: ignore[reportPrivateUsage]
    provider = encrypted.connect(db, monkeypatch)
    read = generation.generate_package(
        db,
        channels.MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=channels.REPORTING_DATE),
    )
    validation.validate_package(db, channels.MAKER, SAMPLE_BANK_ID, read.id)
    workflow.request_approval(
        db, channels.MAKER, SAMPLE_BANK_ID, read.id, PackageApprovalRequestCreate()
    )
    package = db.get(RegulatoryPackage, read.id)
    assert package is not None
    complete_chain(
        db,
        package,
        approver=channels.CHECKER.actor_user_id,
        validator=channels.VALIDATOR.actor_user_id,
    )
    db.commit()
    return provider, package


def test_rotation_waits_for_remote_receipt_and_local_submission_commit(
    canonical_book: Session,
    postgres: Engine,
    monkeypatch: pytest.MonkeyPatch,
    exporter_calls: list[str],
) -> None:
    db = canonical_book
    provider, package = _approved_submission(db, monkeypatch)
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
            rotation = pool.submit(_rotate, postgres, provider, rotation_pid)
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
