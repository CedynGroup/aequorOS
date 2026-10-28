from __future__ import annotations

import json
from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Table, select, text, update
from sqlalchemy.orm import Session

from app.bi.domain.catalogue import catalogue
from app.bi.models.content import BiDashboardVersion
from app.bi.models.marts import BiDimCounterparty, BiFactPositionDaily, BiFactPositionEom
from app.bi.models.notifications import BiAlert, BiSubscription
from app.bi.schemas.query import BiQuery
from app.bi.service.compiler import compile_query
from app.core.key_management import maintenance, reencryption, registry, sdk
from app.core.key_management.fields import FORMAT, SealedField, blind_index
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope, RetainedBankKey
from app.core.key_management.presentation import LOCKED_JSON, SESSION_STATE, PresentationState
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import KeyReference, KeyStatus, KeyUnavailableError
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.identity.models.institution_profile import RelatedParty, RelatedPartyRole
from app.identity.public import Bank
from app.models.audit_event import AuditEvent
from app.models.canonical import CanonicalCounterparty, CanonicalPositionSnapshot
from app.models.job import Job
from app.reporting.models.packages import RegulatoryPackage
from app.services import canonical_withdrawal, job_queue
from app.services.canonical_withdrawal import WithdrawalError
from app.storage.client import (
    EncryptionCursor,
    EncryptionInventoryItem,
    EncryptionPage,
    ReencryptionIsolation,
    StorageBackendError,
    StorageLocation,
)
from tests.bi.service.test_mart_builder import AS_OF, build, resnapshot_loan_1, seed_book
from tests.core.test_bank_field_encryption import (
    BANK_A,
    BANK_B,
    CTX,
    IDENTIFIER,
    NAME,
    NEXT,
    connect,
    counterparty,
    provider,
    saved_version,
)
from tests.support.helpers import ORG_1
from tests.support.inmemory_storage import InMemoryStorageClient
from tests.support.reencryption import drain

__all__ = ["provider"]


class EmptyStorage(InMemoryStorageClient):
    def encryption_page(
        self, institution_slug: str, cursor: EncryptionCursor, limit: int
    ) -> EncryptionPage:
        assert not self._objects
        return EncryptionPage([], None)


class UnavailableStorage(EmptyStorage):
    def encryption_page(
        self, institution_slug: str, cursor: EncryptionCursor, limit: int
    ) -> EncryptionPage:
        raise StorageBackendError("Synthetic interruption after committed field batches.")


@pytest.mark.parametrize("crash", ["handoff", "completion"])
@pytest.mark.parametrize("reclaimed", [False, True])
def test_interrupted_terminal_parent_cannot_swallow_a_new_target_request(
    db_session: Session,
    provider: LocalKeyProvider,
    crash: str,
    reclaimed: bool,
) -> None:
    party_id = counterparty(db_session, include_package=False).id
    connect(db_session, provider)
    root = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    claimed = job_queue.claim_next(db_session, utc_now(), (reencryption.JOB_TYPE,))
    assert claimed is not None and claimed.id == root.id
    reencryption.run(db_session, claimed, EmptyStorage())
    successor_id = reencryption.Progress.model_validate(claimed.progress).next_job_id
    assert successor_id is not None
    successor = db_session.get(Job, successor_id)
    assert successor is not None
    last = drain(db_session, successor, EmptyStorage())
    parent = claimed if crash == "handoff" else last
    parent.status = "queued" if reclaimed else "running"
    parent.completed_at = None
    parent.started_at = None if reclaimed else utc_now()
    parent.coalesce_key = f"bank-reencrypt:{BANK_A}"
    db_session.commit()
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    old_target = key.current_version_id
    registry.rotate(db_session, key, NEXT, lambda _: provider)
    target = key.current_version_id
    assert target != old_target
    db_session.commit()
    fresh = reencryption.enqueue(db_session, ORG_1, BANK_A)
    assert fresh.id not in {parent.id, root.id, last.id}
    assert reencryption.enqueue(db_session, ORG_1, BANK_A).id == fresh.id
    db_session.commit()
    completed = drain(db_session, fresh, EmptyStorage())
    result = reencryption.Progress.model_validate(completed.progress)
    assert result.target_version == target and result.phase == "done"
    value = db_session.scalar(
        select(CanonicalCounterparty.name).where(CanonicalCounterparty.id == party_id)
    )
    assert isinstance(value, SealedField) and value.envelope_id == target


def test_large_object_claim_scales_with_size_and_recovers_an_interruption(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect(db_session, provider)
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    target = key.current_version_id
    assert target is not None
    now = utc_now()
    monkeypatch.setattr(job_queue, "utc_now", lambda: now)

    class LargeStorage(EmptyStorage):
        version: UUID | None = None
        copies = 0

        def encryption_page(
            self, institution_slug: str, cursor: EncryptionCursor, limit: int
        ) -> EncryptionPage:
            assert limit == 1
            return EncryptionPage(
                [
                    EncryptionInventoryItem(
                        StorageLocation(institution_slug, "temp", "large.csv"),
                        "null",
                        self.version,
                        False,
                        500 * 1024 * 1024,
                    )
                ],
                None,
            )

        def reencrypt(self, item: EncryptionInventoryItem) -> bool:
            pytest.fail("A large object must have a dedicated dispatch")

        def reencrypt_isolated(
            self, item: EncryptionInventoryItem, isolation: ReencryptionIsolation
        ) -> bool:
            nonlocal now
            isolation.release()
            now += timedelta(seconds=1200)
            assert job_queue.reclaim_stale(db_session, now, stale_after=timedelta(seconds=900)) == 0
            db_session.rollback()
            with isolation.publication(target) as allowed:
                assert allowed
                self.version = target
                self.copies += 1
            return True

    storage = LargeStorage()
    _ = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    while True:
        claimed = job_queue.claim_next(db_session, now, (reencryption.JOB_TYPE,))
        assert claimed is not None
        progress = reencryption.Progress.model_validate(claimed.progress)
        if progress.phase == "large_object":
            break
        reencryption.run(db_session, claimed, storage)
        job_queue.complete(db_session, claimed)
    assert claimed.payload == {"reencryption_bytes": 500 * 1024 * 1024}
    now += timedelta(seconds=4901)
    assert job_queue.reclaim_stale(db_session, now, stale_after=timedelta(seconds=900)) == 1
    resumed = job_queue.claim_next(db_session, now, (reencryption.JOB_TYPE,))
    assert resumed is not None and resumed.id == claimed.id
    reencryption.run(db_session, resumed, storage)
    result = reencryption.Progress.model_validate(resumed.progress)
    assert result.phase == "objects" and result.objects_rewritten == storage.copies == 1
    job_queue.complete(db_session, resumed)
    completed = drain(db_session, resumed, storage)
    assert reencryption.Progress.model_validate(completed.progress).summary == "complete"


def test_field_migration_and_verification_resume_across_bounded_dispatches(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for number in range(5):
        counterparty(db_session, reference=f"Synthetic-{number}", include_package=False)
    connect(db_session, provider)
    monkeypatch.setattr(reencryption, "BATCH_SIZE", 2)
    _ = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    checkpoints: dict[str, list[UUID | None]] = {"fields": [], "verify_fields": []}
    while True:
        claimed = job_queue.claim_next(db_session, utc_now(), (reencryption.JOB_TYPE,))
        if claimed is None:
            break
        before = reencryption.Progress.model_validate(claimed.progress)
        reencryption.run(db_session, claimed, EmptyStorage())
        after = reencryption.Progress.model_validate(claimed.progress)
        tables = maintenance.protected_tables()
        if (
            before.phase in checkpoints
            and before.table < len(tables)
            and tables[before.table].name == "canonical_counterparties"
        ):
            checkpoints[before.phase].append(after.after)
        job_queue.complete(db_session, claimed)
    for cursors in checkpoints.values():
        assert len(cursors) == 3
        assert cursors[0] is not None and cursors[1] is not None
        assert cursors[0] < cursors[1] and cursors[2] is None
    stored = list(
        db_session.scalars(
            select(CanonicalCounterparty.name).where(CanonicalCounterparty.bank_id == BANK_A)
        )
    )
    assert len(stored) == 5 and all(isinstance(value, SealedField) for value in stored)
    db_session.expunge_all()
    assert {
        party.name
        for party in db_session.scalars(
            select(CanonicalCounterparty).where(CanonicalCounterparty.bank_id == BANK_A)
        )
    } == {NAME}


def test_bounded_dispatches_outlive_the_old_six_hour_window_without_reclaim(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect(db_session, provider)
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    target = key.current_version_id
    assert target is not None
    now = utc_now()
    beginning = now
    monkeypatch.setattr(job_queue, "utc_now", lambda: now)

    def elapsed() -> float:
        return (now - beginning).total_seconds()

    monkeypatch.setattr(reencryption.time, "monotonic", elapsed)

    class SlowStorage(EmptyStorage):
        versions: dict[int, UUID | None] = dict.fromkeys(range(100))

        def encryption_page(
            self, institution_slug: str, cursor: EncryptionCursor, limit: int
        ) -> EncryptionPage:
            nonlocal now
            offset = int(cursor.key) + 1 if cursor.key is not None else 0
            numbers = list(range(offset, min(offset + limit, len(self.versions))))
            now += timedelta(seconds=3 * len(numbers))
            items = [
                EncryptionInventoryItem(
                    StorageLocation(institution_slug, "temp", str(number)),
                    "null",
                    self.versions[number],
                    False,
                )
                for number in numbers
            ]
            following = (
                EncryptionCursor(0, str(numbers[-1]), "null")
                if numbers and numbers[-1] + 1 < len(self.versions)
                else None
            )
            return EncryptionPage(items, following)

        def reencrypt(self, item: EncryptionInventoryItem) -> bool:
            nonlocal now
            now += timedelta(seconds=300)
            self.versions[int(item.location.object_path)] = target
            return True

    storage = SlowStorage()
    root = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    jobs = 0
    last = root
    while True:
        claimed = job_queue.claim_next(db_session, now, (reencryption.JOB_TYPE,))
        if claimed is None:
            break
        started = now
        reencryption.run(db_session, claimed, storage)
        assert now - started < timedelta(seconds=900)
        assert job_queue.reclaim_stale(db_session, now, stale_after=timedelta(seconds=900)) == 0
        job_queue.complete(db_session, claimed)
        jobs += 1
        last = claimed
    assert now - beginning > timedelta(hours=6)
    assert jobs > 100
    assert reencryption.Progress.model_validate(last.progress).summary == "complete"
    assert reencryption.Progress.model_validate(last.progress).objects_rewritten == 100
    assert set(storage.versions.values()) == {target}
    assert job_queue.stale_after_for(reencryption.JOB_TYPE, timedelta(seconds=900)) == timedelta(
        seconds=900
    )


def test_interrupted_batch_reclaims_at_default_window_and_resumes(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    party = counterparty(db_session)
    party_id = party.id
    db_session.commit()
    connect(db_session, provider)
    now = utc_now()
    monkeypatch.setattr(job_queue, "utc_now", lambda: now)
    root = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    with pytest.raises(StorageBackendError, match="Synthetic interruption"):
        drain(db_session, root, UnavailableStorage())
    db_session.rollback()
    orphan = db_session.scalar(
        select(Job).where(Job.job_type == reencryption.JOB_TYPE, Job.status == "running")
    )
    assert orphan is not None
    progress = reencryption.Progress.model_validate(orphan.progress)
    assert progress.fields_rewritten > 0
    started_at = orphan.started_at
    assert started_at is not None
    now = started_at.replace(tzinfo=started_at.tzinfo or UTC)
    assert (
        job_queue.reclaim_stale(
            db_session, now + timedelta(seconds=899), stale_after=timedelta(seconds=900)
        )
        == 0
    )
    now += timedelta(seconds=901)
    assert job_queue.reclaim_stale(db_session, now, stale_after=timedelta(seconds=900)) == 1
    db_session.refresh(orphan)
    assert orphan.status == "queued" and orphan.attempts == 1
    assert reencryption.Progress.model_validate(orphan.progress) == progress
    resumed = job_queue.claim_next(db_session, now, (reencryption.JOB_TYPE,))
    assert resumed is not None and resumed.id == orphan.id
    completed = drain(db_session, resumed, EmptyStorage())
    result = reencryption.Progress.model_validate(completed.progress)
    assert result.fields_rewritten == progress.fields_rewritten
    assert result.retained == {}
    assert result.summary == "complete"
    db_session.expunge_all()
    loaded = db_session.get(CanonicalCounterparty, party_id)
    assert loaded is not None and loaded.name == NAME


def test_connection_reindexes_withdrawn_generations_before_reimport(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    cp = counterparty(db_session, include_package=False)
    bank = db_session.get(Bank, BANK_A)
    assert bank is not None
    requested = canonical_withdrawal.request_withdrawal(
        db_session,
        TenantContext(organization_id=ORG_1, actor_user_id=None, roles=("admin",)),
        bank,
        entity="counterparty",
        source_system=cp.source_system,
        as_of_date=cp.as_of_date,
        reason="Synthetic withdrawal",
        requested_by="Maker",
    )
    canonical_withdrawal.approve_withdrawal(
        db_session, CTX, bank, requested.id, approved_by="Checker"
    )
    db_session.commit()
    cp_id, withdrawal_id = cp.id, requested.id
    connect(db_session, provider)
    token = blind_index(
        db_session,
        organization_id=ORG_1,
        bank_id=BANK_A,
        domain="counterparty.source_reference",
        value=IDENTIFIER,
    )
    stored = db_session.scalar(
        select(CanonicalCounterparty.source_reference_blind_index).where(
            CanonicalCounterparty.id == cp_id
        )
    )
    assert stored == token and token != IDENTIFIER
    counterparty(db_session, include_package=False)
    db_session.commit()
    with pytest.raises(WithdrawalError, match="replaced"):
        canonical_withdrawal.reverse_withdrawal(
            db_session, CTX, bank, withdrawal_id, reversed_by="Officer", reason="Synthetic reversal"
        )
    assert (
        len(
            list(
                db_session.scalars(
                    select(CanonicalCounterparty).where(
                        CanonicalCounterparty.bank_id == BANK_A,
                        CanonicalCounterparty.withdrawn_at.is_(None),
                    )
                )
            )
        )
        == 1
    )


def test_resume_moves_fields_preserves_immutable_bytes_and_is_idempotent(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    cp = counterparty(db_session)
    cp_id = cp.id
    sibling_id = counterparty(db_session, bank_id=BANK_B).id
    version = saved_version(db_session, {"synthetic": NAME})
    db_session.add(version)
    db_session.commit()
    immutable_before = cast(
        object, db_session.execute(text("SELECT spec FROM bi_dashboard_versions")).scalar_one()
    )
    snapshot_before = cast(
        object,
        db_session.execute(
            text("SELECT snapshot FROM regulatory_packages WHERE bank_id=:bank"), {"bank": BANK_A}
        ).scalar_one(),
    )
    connect(db_session, provider)
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    job_id = job.id
    with pytest.raises(StorageBackendError, match="Synthetic interruption"):
        job = drain(db_session, job, UnavailableStorage())
    db_session.rollback()
    db_session.expunge_all()
    key = registry.bank_key(db_session, ORG_1, BANK_A)
    assert key is not None
    raw = db_session.scalar(
        select(CanonicalCounterparty.name).where(CanonicalCounterparty.id == cp_id)
    )
    assert isinstance(raw, SealedField) and raw.envelope_id == key.current_version_id
    # New writes and a later rotation occur between attempts. Resume must use
    # the new current version, including for rows behind the old cursor.
    late_id = counterparty(db_session, reference="synthetic-late", include_package=False).id
    registry.rotate(db_session, key, NEXT, lambda _: provider)
    db_session.commit()
    job = db_session.get(Job, job_id)
    assert job is not None
    job = drain(db_session, job, EmptyStorage())
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.summary == "complete"
    assert progress.retained == {}
    target = db_session.scalar(
        select(BankEncryptionKey.current_version_id).where(BankEncryptionKey.bank_id == BANK_A)
    )
    for row_id in (cp_id, late_id):
        value = db_session.scalar(
            select(CanonicalCounterparty.name).where(CanonicalCounterparty.id == row_id)
        )
        assert isinstance(value, SealedField) and value.envelope_id == target
    assert (
        db_session.execute(text("SELECT spec FROM bi_dashboard_versions")).scalar_one()
        == immutable_before
    )
    assert (
        db_session.execute(
            text("SELECT snapshot FROM regulatory_packages WHERE bank_id=:bank"), {"bank": BANK_A}
        ).scalar_one()
        == snapshot_before
    )
    assert (
        db_session.scalar(
            select(CanonicalCounterparty.name).where(CanonicalCounterparty.id == sibling_id)
        )
        == NAME
    )
    before = db_session.execute(
        text("SELECT name FROM canonical_counterparties WHERE bank_id=:bank ORDER BY id"),
        {"bank": BANK_A},
    ).all()
    job = drain(db_session, job, EmptyStorage())
    after = db_session.execute(
        text("SELECT name FROM canonical_counterparties WHERE bank_id=:bank ORDER BY id"),
        {"bank": BANK_A},
    ).all()
    assert before == after
    db_session.expunge_all()
    loaded = db_session.get(CanonicalCounterparty, cp_id)
    assert loaded is not None and loaded.name == NAME


def test_plain_snapshot_does_not_retain_retired_encryption_version(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "1")
    get_key_settings.cache_clear()
    connect(db_session, provider)
    counterparty(db_session)
    db_session.commit()
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    original, old_version = registry.reference(key), key.current_version_id
    snapshot_before = db_session.scalar(select(RegulatoryPackage.snapshot))
    registry.rotate(db_session, key, NEXT, lambda _: provider)
    db_session.commit()
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    job = drain(db_session, job, EmptyStorage())
    assert reencryption.Progress.model_validate(job.progress).retained == {}
    assert db_session.get(ObjectKeyEnvelope, old_version) is None
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    hold = db_session.scalar(select(RetainedBankKey).where(RetainedBankKey.bank_id == BANK_A))
    assert hold is not None
    hold.rotated_at = utc_now() - timedelta(days=3)
    hold.decrypt_until = utc_now() - timedelta(days=2)
    db_session.flush()
    registry.authorize_retirement(db_session, key, original)
    snapshot = db_session.scalar(
        select(RegulatoryPackage.snapshot).where(RegulatoryPackage.bank_id == BANK_A)
    )
    assert snapshot == snapshot_before and isinstance(snapshot, dict)


def test_editable_only_migration_releases_unused_versions_but_keeps_backup_hold(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "1")
    get_key_settings.cache_clear()
    connect(db_session, provider)
    party = counterparty(db_session, include_package=False)
    party_id = party.id
    db_session.commit()
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    original, old_version = registry.reference(key), key.current_version_id
    token = blind_index(
        db_session,
        organization_id=ORG_1,
        bank_id=BANK_A,
        domain="counterparty.source_reference",
        value=IDENTIFIER,
    )
    registry.rotate(db_session, key, NEXT, lambda _: provider)
    db_session.commit()
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    job = drain(db_session, job, EmptyStorage())
    assert reencryption.Progress.model_validate(job.progress).summary == "complete"
    assert db_session.get(ObjectKeyEnvelope, old_version) is None
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    assert db_session.get(ObjectKeyEnvelope, key.current_version_id) is not None
    index = db_session.get(ObjectKeyEnvelope, key.index_version_id)
    assert index is not None and registry.reference(index) == registry.reference(key)
    assert (
        blind_index(
            db_session,
            organization_id=ORG_1,
            bank_id=BANK_A,
            domain="counterparty.source_reference",
            value=IDENTIFIER,
        )
        == token
    )
    assert (
        db_session.scalar(
            select(CanonicalCounterparty.source_reference_blind_index).where(
                CanonicalCounterparty.id == party_id,
            )
        )
        == token
    )
    with pytest.raises(KeyUnavailableError, match="backup retention expires"):
        registry.authorize_retirement(db_session, key, original)
    hold = db_session.scalar(select(RetainedBankKey).where(RetainedBankKey.bank_id == BANK_A))
    assert hold is not None
    hold.rotated_at = utc_now() - timedelta(days=3)
    hold.decrypt_until = utc_now() - timedelta(days=2)
    db_session.flush()
    registry.authorize_retirement(db_session, key, original)
    provider.set_status(original, KeyStatus.DISABLED)
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(CanonicalCounterparty, party_id)
    assert loaded is not None and loaded.name == NAME
    assert (
        blind_index(
            db_session,
            organization_id=ORG_1,
            bank_id=BANK_A,
            domain="counterparty.source_reference",
            value=IDENTIFIER,
        )
        == token
    )


def test_child_fields_are_scoped_and_audit_history_is_retained(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    roles: list[RelatedPartyRole] = []
    for bank in (BANK_A, BANK_B):
        party = RelatedParty(
            organization_id=ORG_1,
            bank_id=bank,
            party_type="individual",
            full_name=NAME,
            contact={"synthetic": IDENTIFIER},
        )
        db_session.add(party)
        db_session.flush()
        role = RelatedPartyRole(
            organization_id=ORG_1,
            party_id=party.id,
            role="director",
            other_responsibilities=NAME,
            term_of_appointment=NAME,
            icag_registration=IDENTIFIER,
        )
        db_session.add(role)
        roles.append(role)
    db_session.add(
        AuditEvent(organization_id=ORG_1, event_type="synthetic.retained", details={"name": NAME})
    )
    db_session.commit()
    role_ids = [r.id for r in roles]
    before = cast(object, db_session.execute(text("SELECT details FROM audit_events")).scalar_one())
    connect(db_session, provider)
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    job = drain(db_session, job, EmptyStorage())
    first = db_session.scalar(
        select(RelatedPartyRole.icag_registration).where(RelatedPartyRole.id == role_ids[0])
    )
    assert isinstance(first, SealedField)
    assert (
        db_session.scalar(
            select(RelatedPartyRole.icag_registration).where(RelatedPartyRole.id == role_ids[1])
        )
        == IDENTIFIER
    )
    assert db_session.execute(text("SELECT details FROM audit_events")).scalar_one() == before
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.retained == {"audit_events (organization scope)": 1}
    assert progress.summary == "complete except 1 retained immutable records"


def test_job_clears_historical_bi_identity_copies_and_keeps_numeric_queries(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = seed_book(db_session, live=False)
    party = db_session.scalar(
        select(CanonicalCounterparty).where(CanonicalCounterparty.bank_id == bank.id).limit(1)
    )
    assert party is not None
    party.group_reference = "synthetic-historical-group"
    db_session.commit()
    _ = build(db_session)
    historical = date(2026, 5, 31)
    resnapshot_loan_1(db_session, historical, balance="100")
    snapshot = db_session.scalar(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == historical,
        )
    )
    assert snapshot is not None
    snapshot.counterparty_id = party.id
    db_session.flush()
    _ = build(db_session, historical)
    orphan_id = uuid4()
    db_session.add(
        BiDimCounterparty(
            organization_id=ORG_1,
            bank_id=bank.id,
            counterparty_id=orphan_id,
            name="Synthetic historical identity",
            source_reference="synthetic-history",
            group_reference="synthetic-history-group",
            counterparty_type="RETAIL_INDIVIDUAL",
            builder_version=1,
            built_at=utc_now(),
        )
    )
    db_session.commit()
    query = BiQuery.model_validate(
        {"measures": ["loans.balance_rc"], "time": {"as_of": AS_OF.isoformat()}}
    )

    def numeric_rows() -> list[object]:
        compiled = compile_query(
            db_session, catalogue(), query, organization_id=ORG_1, bank_id=bank.id
        )
        return list(db_session.execute(compiled.select).all())

    before = numeric_rows()
    assert before
    for model in (BiFactPositionDaily, BiFactPositionEom):
        assert (
            db_session.scalar(
                select(model.counterparty_group)
                .where(model.bank_id == bank.id, model.counterparty_group.is_not(None))
                .limit(1)
            )
            is not None
        )
    provider = LocalKeyProvider()
    reference = KeyReference("aws_kms", "synthetic-bi-upgrade", "us-east-1", "111122223333")
    provider.add_key(reference)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    registry.register(
        db_session,
        organization_id=ORG_1,
        bank_id=bank.id,
        storage_slug="synthetic-bi-upgrade",
        key=reference,
        providers=lambda _: provider,
    )
    db_session.commit()

    class LateIdentityStorage(EmptyStorage):
        dirty = True

        def encryption_page(
            self, institution_slug: str, cursor: EncryptionCursor, limit: int
        ) -> EncryptionPage:
            if self.dirty:
                self.dirty = False
                _ = db_session.execute(
                    update(BiDimCounterparty)
                    .where(
                        BiDimCounterparty.bank_id == bank.id,
                        BiDimCounterparty.counterparty_id == orphan_id,
                    )
                    .values(name="Synthetic late identity")
                )
                for model in (BiFactPositionDaily, BiFactPositionEom):
                    _ = db_session.execute(
                        update(model)
                        .where(model.bank_id == bank.id)
                        .values(counterparty_group="synthetic-late-group")
                    )
            return super().encryption_page(institution_slug, cursor, limit)

    job = reencryption.enqueue(db_session, ORG_1, bank.id)
    db_session.commit()
    job = drain(db_session, job, LateIdentityStorage())
    key = registry.bank_key(db_session, ORG_1, bank.id)
    assert key is not None
    assert list(maintenance.bi_identity_updates(db_session, key)) == []
    for row in db_session.execute(
        select(BiDimCounterparty.__table__).where(BiDimCounterparty.bank_id == bank.id)
    ).mappings():
        assert (
            cast(object, row["name"])
            == cast(object, row["source_reference"])
            == str(cast(object, row["counterparty_id"]))
        )
        assert row["group_reference"] is None
    for model in (BiFactPositionDaily, BiFactPositionEom):
        rows = db_session.execute(
            select(model.as_of_date, model.counterparty_group).where(model.bank_id == bank.id)
        ).all()
        assert {cast(object, row.as_of_date) for row in rows} == {historical, AS_OF}
        assert all(cast(object, row.counterparty_group) is None for row in rows)
    assert numeric_rows() == before


def store_whole_bi_values(
    db_session: Session,
    records: list[tuple[Table, UUID, str, object]],
) -> None:
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    secret = registry.prepare_envelope(
        db_session,
        key,
        {
            "purpose": "bank-sensitive-fields-v1",
            "organization_id": ORG_1,
            "bank_id": BANK_A,
        },
        registry.provider_for,
    )
    for table, record_id, column, value in records:
        sealed = SealedField(
            secret.envelope_id,
            sdk.encrypt(
                json.dumps(value).encode(),
                sdk.raw_keyring(secret.plaintext, name=FORMAT),
                {
                    "purpose": "bank-sensitive-record-v1",
                    "organization_id": ORG_1,
                    "bank_id": BANK_A,
                    "table": table.name,
                    "record_id": str(record_id),
                    "field": column,
                },
            ),
        )
        _ = db_session.execute(
            table.update().where(table.c.id == record_id).values(**{column: sealed})
        )
    db_session.commit()


def assert_bi_key_loss(
    db_session: Session,
    provider: LocalKeyProvider,
    identity: bool,
    subscription_id: UUID,
    query: dict[str, object],
) -> None:
    db_session.commit()
    db_session.expunge_all()
    provider.set_status(NEXT, KeyStatus.DISABLED)
    if identity:
        locked = db_session.get(BiSubscription, subscription_id)
        assert locked is not None and locked.query == {LOCKED_JSON: "revoked"}
        assert NAME not in json.dumps(locked.query, ensure_ascii=False)
    else:
        numeric = db_session.get(BiSubscription, subscription_id)
        assert numeric is not None and numeric.query == query
        db_session.info[SESSION_STATE] = PresentationState(banks={BANK_A: "revoked"})
        numeric.query = {**query, "limit": 24}
        db_session.commit()
        db_session.expunge_all()
        reloaded = db_session.get(BiSubscription, subscription_id)
        assert reloaded is not None and reloaded.query["limit"] == 24


@pytest.mark.parametrize("encoding", ["plaintext", "leaves", "whole"])
@pytest.mark.parametrize("identity", [False, True])
def test_maintenance_preserves_numeric_bi_and_rewrites_only_identity_leaves(
    db_session: Session, provider: LocalKeyProvider, encoding: str, identity: bool
) -> None:
    if encoding != "plaintext":
        connect(db_session, provider)
    filters: list[dict[str, object]] = [{"member": "position.currency", "values": ["GHS"]}]
    if identity:
        filters.append({"member": "counterparty.name", "values": [NAME]})
    query: dict[str, object] = {"nested": [{"filters": filters}], "limit": 23}
    alert = BiAlert(
        organization_id=ORG_1,
        bank_id=BANK_A,
        owner_user_id=CTX.actor_user_id,
        name="Synthetic selective alert",
        measure_id="loans.balance_rc",
        filters=filters,
        direction="above",
        threshold_basis="stated",
        threshold=Decimal(10),
        notify_user_ids=[],
    )
    subscription = BiSubscription(
        organization_id=ORG_1,
        bank_id=BANK_A,
        owner_user_id=CTX.actor_user_id,
        name="Synthetic selective subscription",
        query=query,
        artifact_format="csv",
        cadence="daily",
        hour=7,
        minute=30,
        recipient_user_ids=[],
    )
    db_session.add_all([alert, subscription])
    db_session.commit()
    alert_id, subscription_id = alert.id, subscription.id
    if encoding == "plaintext":
        connect(db_session, provider)
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    registry.rotate(db_session, key, NEXT, lambda _: provider)
    target = key.current_version_id
    db_session.commit()
    if encoding == "whole":
        store_whole_bi_values(
            db_session,
            [
                (cast(Table, BiAlert.__table__), alert_id, "filters", filters),
                (cast(Table, BiSubscription.__table__), subscription_id, "query", query),
            ],
        )
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    _ = drain(db_session, job, EmptyStorage())
    stored_filters = db_session.scalar(select(BiAlert.filters).where(BiAlert.id == alert_id))
    stored_query = db_session.scalar(
        select(BiSubscription.query).where(BiSubscription.id == subscription_id)
    )
    assert isinstance(stored_filters, list) and stored_filters[0] == filters[0]
    assert isinstance(stored_query, dict) and stored_query["limit"] == 23
    if identity:
        leaf = cast(dict[str, list[str]], stored_filters[1]["values"])
        assert leaf[FORMAT][0] == str(target)
    else:
        assert stored_filters == filters and stored_query == query
    db_session.expunge_all()
    restored_alert = db_session.get(BiAlert, alert_id)
    restored_subscription = db_session.get(BiSubscription, subscription_id)
    assert restored_alert is not None and restored_alert.filters == filters
    assert restored_subscription is not None and restored_subscription.query == query
    db_session.commit()
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    _ = drain(db_session, job, EmptyStorage())
    assert (
        db_session.scalar(select(BiAlert.filters).where(BiAlert.id == alert_id)) == stored_filters
    )
    assert (
        db_session.scalar(select(BiSubscription.query).where(BiSubscription.id == subscription_id))
        == stored_query
    )
    assert_bi_key_loss(db_session, provider, identity, subscription_id, query)


def test_nested_immutable_identity_keeps_its_envelope_without_rewriting_bytes(
    db_session: Session, provider: LocalKeyProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "1")
    get_key_settings.cache_clear()
    connect(db_session, provider)
    spec: dict[str, object] = {
        "tiles": [{"query": {"filters": [{"member": "counterparty.name", "values": [NAME]}]}}]
    }
    version = saved_version(db_session, spec)
    db_session.add(version)
    db_session.commit()
    version_id = version.id
    before = db_session.scalar(
        select(BiDashboardVersion.spec).where(BiDashboardVersion.id == version_id)
    )
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    original, old_version = registry.reference(key), key.current_version_id
    registry.rotate(db_session, key, NEXT, lambda _: provider)
    db_session.commit()
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    last = drain(db_session, job, EmptyStorage())
    assert reencryption.Progress.model_validate(last.progress).retained == {
        "bi_dashboard_versions": 1
    }
    assert db_session.get(ObjectKeyEnvelope, old_version) is not None
    assert (
        db_session.scalar(
            select(BiDashboardVersion.spec).where(BiDashboardVersion.id == version_id)
        )
        == before
    )
    db_session.expunge_all()
    loaded = db_session.get(BiDashboardVersion, version_id)
    assert loaded is not None and loaded.spec == spec
    hold = db_session.scalar(select(RetainedBankKey).where(RetainedBankKey.bank_id == BANK_A))
    assert hold is not None
    hold.rotated_at = utc_now() - timedelta(days=3)
    hold.decrypt_until = utc_now() - timedelta(days=2)
    db_session.flush()
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    with pytest.raises(KeyUnavailableError, match="retained encryption versions"):
        registry.authorize_retirement(db_session, key, original)


@pytest.mark.parametrize(
    "representation", ["name", "group_reference", "group", "parent", "attribute_reference"]
)
def test_cutovers_preserve_every_canonical_group_representation(
    db_session: Session, provider: LocalKeyProvider, representation: str
) -> None:
    ids: list[UUID] = []
    for index in range(2):
        cp = counterparty(
            db_session,
            reference=f"synthetic-{index}",
            name="G" if representation == "name" else f"Party {index}",
            include_package=False,
        )
        cp.group_reference = "G" if representation == "group_reference" else None
        cp.attributes = (
            {"group_reference" if representation == "attribute_reference" else representation: "G"}
            if representation not in {"name", "group_reference"}
            else {}
        )
        ids.append(cp.id)
    db_session.commit()
    assert (
        len(set(db_session.scalars(select(CanonicalCounterparty.concentration_group_blind_index))))
        == 1
    )
    connect(db_session, provider)
    for rotate in (False, True):
        key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
        if rotate:
            registry.rotate(db_session, key, NEXT, lambda _: provider)
        db_session.commit()
        job = reencryption.enqueue(db_session, ORG_1, BANK_A)
        db_session.commit()
        _ = drain(db_session, job, EmptyStorage())
        tokens = list(
            db_session.scalars(
                select(CanonicalCounterparty.concentration_group_blind_index).where(
                    CanonicalCounterparty.id.in_(ids)
                )
            )
        )
        expected = blind_index(
            db_session,
            organization_id=ORG_1,
            bank_id=BANK_A,
            domain="counterparty.group",
            value="cp:G" if representation == "name" else "group:G",
        )
        assert tokens == [expected, expected]
        db_session.commit()
