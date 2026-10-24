from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.bi.domain.catalogue import catalogue
from app.bi.models.marts import BiDimCounterparty, BiFactPositionDaily, BiFactPositionEom
from app.bi.schemas.query import BiQuery
from app.bi.service.compiler import compile_query
from app.core.key_management import maintenance, reencryption, registry
from app.core.key_management.fields import SealedField, blind_index
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey, RetainedBankKey
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import KeyReference, KeyUnavailableError
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.identity.models.institution_profile import RelatedParty, RelatedPartyRole
from app.identity.public import Bank
from app.models.audit_event import AuditEvent
from app.models.canonical import CanonicalCounterparty, CanonicalPositionSnapshot
from app.models.job import Job
from app.reporting.models.packages import RegulatoryPackage
from app.services import canonical_withdrawal
from app.services.canonical_withdrawal import WithdrawalError
from app.storage.client import EncryptionInventoryItem, StorageBackendError
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

__all__ = ["provider"]


class EmptyStorage(InMemoryStorageClient):
    def encryption_inventory(self, institution_slug: str) -> Iterator[EncryptionInventoryItem]:
        assert not self._objects
        return iter(())


class UnavailableStorage(EmptyStorage):
    def encryption_inventory(self, institution_slug: str) -> Iterator[EncryptionInventoryItem]:
        raise StorageBackendError("Synthetic interruption after committed field batches.")


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
        reencryption.run(db_session, job, UnavailableStorage())
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
    reencryption.run(db_session, job, EmptyStorage())
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.summary == "complete except 2 retained immutable records"
    assert progress.retained == {"bi_dashboard_versions": 1, "regulatory_packages": 1}
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
    reencryption.run(db_session, job, EmptyStorage())
    after = db_session.execute(
        text("SELECT name FROM canonical_counterparties WHERE bank_id=:bank ORDER BY id"),
        {"bank": BANK_A},
    ).all()
    assert before == after
    db_session.expunge_all()
    loaded = db_session.get(CanonicalCounterparty, cp_id)
    assert loaded is not None and loaded.name == NAME


def test_retired_version_referenced_by_locked_snapshot_remains_required(
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
    original = registry.reference(key)
    registry.rotate(db_session, key, NEXT, lambda _: provider)
    db_session.commit()
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    db_session.commit()
    reencryption.run(db_session, job, EmptyStorage())
    assert reencryption.Progress.model_validate(job.progress).retained["regulatory_packages"] == 1
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    hold = db_session.scalar(select(RetainedBankKey).where(RetainedBankKey.bank_id == BANK_A))
    assert hold is not None
    hold.rotated_at = utc_now() - timedelta(days=3)
    hold.decrypt_until = utc_now() - timedelta(days=2)
    db_session.flush()
    with pytest.raises(KeyUnavailableError, match="retained encryption versions"):
        registry.authorize_retirement(db_session, key, original)
    snapshot = db_session.scalar(
        select(RegulatoryPackage.snapshot).where(RegulatoryPackage.bank_id == BANK_A)
    )
    assert isinstance(snapshot, SealedField) and snapshot.envelope_id != key.current_version_id


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
    reencryption.run(db_session, job, EmptyStorage())
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
    monkeypatch.setattr(registry, "provider_for", lambda _: provider)
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

        def encryption_inventory(self, institution_slug: str) -> Iterator[EncryptionInventoryItem]:
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
            return super().encryption_inventory(institution_slug)

    job = reencryption.enqueue(db_session, ORG_1, bank.id)
    db_session.commit()
    reencryption.run(db_session, job, LateIdentityStorage())
    key = registry.bank_key(db_session, ORG_1, bank.id)
    assert key is not None
    assert list(maintenance.bi_identity_updates(db_session, key)) == []
    for row in db_session.execute(
        select(BiDimCounterparty.__table__).where(BiDimCounterparty.bank_id == bank.id)
    ).mappings():
        assert row["name"] == row["source_reference"] == str(cast(object, row["counterparty_id"]))
        assert row["group_reference"] is None
    for model in (BiFactPositionDaily, BiFactPositionEom):
        rows = db_session.execute(
            select(model.as_of_date, model.counterparty_group).where(model.bank_id == bank.id)
        ).all()
        assert {row.as_of_date for row in rows} == {historical, AS_OF}
        assert all(row.counterparty_group is None for row in rows)
    assert numeric_rows() == before
