from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session

from app.core.key_management import maintenance, reencryption, registry
from app.core.key_management.fields import SealedField, blind_index
from app.core.key_management.local import LocalKeyProvider
from app.core.tenancy import TenantContext
from app.identity.public import Bank
from app.models.canonical import CanonicalCounterparty
from app.models.job import Job
from app.services import canonical_withdrawal
from app.services.canonical_withdrawal import WithdrawalError
from tests.core.test_bank_field_encryption import (
    BANK_A,
    CTX,
    IDENTIFIER,
    NEXT,
    connect,
    counterparty,
    provider,
)
from tests.core.test_bank_reencryption import EmptyStorage
from tests.support.helpers import ORG_1

__all__ = ["provider"]
pytestmark = pytest.mark.committing_db


def engine(db: Session) -> Engine:
    bind = db.get_bind()
    if not isinstance(bind, Engine) or bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent transactions are required.")
    return bind


def test_first_connection_waits_for_earlier_plaintext_writer_and_indexes_it(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = engine(db_session)
    db_session.commit()
    row_id = counterparty(db_session, include_package=False).id
    entered = Event()
    real_lock = maintenance.lock_bank

    def observed_lock(
        db: Session, organization: str, bank: str, *, exclusive: bool = False
    ) -> None:
        if exclusive:
            entered.set()
        real_lock(db, organization, bank, exclusive=exclusive)

    monkeypatch.setattr(maintenance, "lock_bank", observed_lock)

    def connection() -> None:
        with Session(database) as peer:
            connect(peer, provider)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(connection)
        try:
            assert entered.wait(5) and not future.done()
        finally:
            db_session.commit()
        future.result(timeout=20)
    db_session.expire_all()
    token = blind_index(
        db_session,
        organization_id=ORG_1,
        bank_id=BANK_A,
        domain="counterparty.source_reference",
        value=IDENTIFIER,
    )
    assert (
        db_session.scalar(
            select(CanonicalCounterparty.source_reference_blind_index).where(
                CanonicalCounterparty.id == row_id
            )
        )
        == token
    )
    assert token != IDENTIFIER


def test_job_drains_writer_admitted_before_rotation_and_migrates_its_commit(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = engine(db_session)
    connect(db_session, provider)
    late = counterparty(db_session, include_package=False)
    late_id = late.id
    with Session(database) as peer:
        key = registry.scoped_key(peer, organization_id=ORG_1, bank_id=BANK_A)
        registry.rotate(peer, key, NEXT, lambda _: provider)
        job_id = reencryption.enqueue(peer, ORG_1, BANK_A).id
        peer.commit()
    entered = Event()
    real_lock = maintenance.lock_bank

    def observed_lock(
        db: Session, organization: str, bank: str, *, exclusive: bool = False
    ) -> None:
        if exclusive:
            entered.set()
        real_lock(db, organization, bank, exclusive=exclusive)

    monkeypatch.setattr(maintenance, "lock_bank", observed_lock)

    def migrate() -> None:
        with Session(database) as peer:
            job = peer.get(Job, job_id)
            assert job is not None
            reencryption.run(peer, job, EmptyStorage())

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(migrate)
        try:
            assert entered.wait(5) and not future.done()
        finally:
            db_session.commit()
        future.result(timeout=30)
    db_session.expire_all()
    key = registry.bank_key(db_session, ORG_1, BANK_A)
    assert key is not None
    value = db_session.scalar(
        select(CanonicalCounterparty.name).where(CanonicalCounterparty.id == late_id)
    )
    assert isinstance(value, SealedField) and value.envelope_id == key.current_version_id


def test_reversal_refuses_import_committed_after_its_conflict_snapshot(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    database = engine(db_session)
    cp = counterparty(db_session, include_package=False)
    bank = db_session.get(Bank, BANK_A)
    assert bank is not None
    request = canonical_withdrawal.request_withdrawal(
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
        db_session, CTX, bank, request.id, approved_by="Checker"
    )
    request_id = request.id
    db_session.commit()
    connect(db_session, provider)
    checked, imported = Event(), Event()

    def reverse() -> None:
        with Session(database) as peer:
            other_bank = peer.get(Bank, BANK_A)
            assert other_bank is not None
            connection = peer.connection()

            def after_query(
                _conn: object,
                _cursor: object,
                statement: str,
                _parameters: object,
                _context: object,
                _many: bool,
            ) -> None:
                if "count(" in statement and "canonical_counterparties AS" in statement:
                    checked.set()
                    assert imported.wait(15)

            event.listen(connection, "after_cursor_execute", after_query)
            with pytest.raises(WithdrawalError, match="concurrently replaced"):
                canonical_withdrawal.reverse_withdrawal(
                    peer,
                    CTX,
                    other_bank,
                    request_id,
                    reversed_by="Officer",
                    reason="Synthetic reversal",
                )
            peer.rollback()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reverse)
        try:
            assert checked.wait(10)
            counterparty(db_session, include_package=False)
            db_session.commit()
        finally:
            imported.set()
        future.result(timeout=20)
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
