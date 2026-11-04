from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from typing import Literal, cast

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.orm import Session, UOWTransaction

from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope, RetainedBankKey
from app.core.key_management.schemas import BankKeyRotate
from app.core.key_management.types import KeyDescription, KeyReference, KeyStatus
from app.identity.service import bank_encryption as administrator
from app.operator.services import bank_encryption as staff
from tests.identity.api.test_bank_encryption import BANK, CTX, KEY, NEXT, local_keys
from tests.support.helpers import ORG_1
from tests.support.key_concurrency import assert_blocked, backend_pid

__all__ = ["local_keys"]
pytestmark = pytest.mark.committing_db
ROTATION = BankKeyRotate(
    key_id=NEXT.key_id,
    region=NEXT.region,
    owner_account=NEXT.owner_account,
    reason="Synthetic concurrent bank key rotation.",
)


@pytest.fixture
def postgres(db_session: Session) -> Engine:
    engine = db_session.get_bind()
    if not isinstance(engine, Engine) or engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent transactions are required.")
    return engine


def _staff_rotate(engine: Engine, started: Queue[int], payload: BankKeyRotate) -> None:
    with Session(engine) as writer:
        writer.info["organization_id"] = ORG_1
        started.put(backend_pid(writer))
        staff.rotate_key(writer, ORG_1, BANK, payload)
        writer.commit()


def _admin_rotate(engine: Engine, started: Queue[int], payload: BankKeyRotate) -> None:
    with Session(engine) as writer:
        writer.info["organization_id"] = ORG_1
        started.put(backend_pid(writer))
        administrator.rotate(writer, CTX, BANK, payload)


def _versions(db: Session) -> int:
    return cast(
        int,
        db.scalar(
            select(func.count())
            .select_from(ObjectKeyEnvelope)
            .where(ObjectKeyEnvelope.bank_id == BANK, ObjectKeyEnvelope.organization_id == ORG_1)
        ),
    )


def test_staff_and_administrator_rotations_follow_one_lock_order(
    db_session: Session,
    postgres: Engine,
    local_keys: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    administrator.connect(db_session, CTX, BANK, KEY)
    db_session.rollback()
    final = ROTATION.model_copy(update={"key_id": NEXT.key_id + "-final"})
    local_keys.add_key(final.reference())
    entered, release = Event(), Event()
    describe = local_keys.describe

    def pause_destination(key: KeyReference) -> KeyDescription:
        if key == NEXT.reference() and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return describe(key)

    monkeypatch.setattr(local_keys, "describe", pause_destination)
    first_pid: Queue[int] = Queue()
    second_pid: Queue[int] = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_staff_rotate, postgres, first_pid, ROTATION)
        try:
            assert entered.wait(5)
            second = pool.submit(_admin_rotate, postgres, second_pid, final)
            assert_blocked(postgres, first_pid.get(timeout=5), second_pid.get(timeout=5), second)
            with Session(postgres) as reader:
                reader.info["organization_id"] = ORG_1
                reader.execute(text("SET LOCAL lock_timeout='200ms'"))
                assert staff.get_key(reader, ORG_1, BANK).key_id == KEY.key_id
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert staff.get_key(db_session, ORG_1, BANK).key_id == final.key_id
    assert _versions(db_session) == 3
    assert (
        len(
            list(
                db_session.scalars(
                    select(RetainedBankKey).where(
                        RetainedBankKey.bank_id == BANK, RetainedBankKey.organization_id == ORG_1
                    )
                )
            )
        )
        == 2
    )


@pytest.mark.parametrize("rotator", ["administrator", "staff"])
def test_legacy_probe_serializes_with_rotation(
    db_session: Session,
    postgres: Engine,
    local_keys: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
    rotator: Literal["administrator", "staff"],
) -> None:
    db_session.add(
        BankEncryptionKey(
            bank_id=BANK,
            organization_id=ORG_1,
            storage_slug="synthetic-legacy-lock-order",
            provider=KEY.provider,
            key_id=KEY.key_id,
            region=KEY.region,
            owner_account=KEY.owner_account,
            status=KeyStatus.ACTIVE.value,
        )
    )
    db_session.commit()
    entered, release = Event(), Event()
    probed = Event()
    describe = local_keys.describe
    unwrap = local_keys.unwrap

    def complete_probe(key: KeyReference, wrapped: bytes, context: dict[str, str]) -> bytes:
        plaintext = unwrap(key, wrapped, context)
        if context.get("purpose") == "bank-key-connection-test":
            probed.set()
        return plaintext

    def pause_initialization(key: KeyReference) -> KeyDescription:
        if key == KEY.reference() and probed.is_set() and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return describe(key)

    def probe(started: Queue[int]) -> bool:
        with Session(postgres) as writer:
            writer.info["organization_id"] = ORG_1
            started.put(backend_pid(writer))
            return administrator.test_connection(writer, CTX, BANK, KEY).passed

    monkeypatch.setattr(local_keys, "describe", pause_initialization)
    monkeypatch.setattr(local_keys, "unwrap", complete_probe)
    first_pid: Queue[int] = Queue()
    second_pid: Queue[int] = Queue()
    rotate = _admin_rotate if rotator == "administrator" else _staff_rotate
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(probe, first_pid)
        try:
            assert entered.wait(5)
            second = pool.submit(rotate, postgres, second_pid, ROTATION)
            assert_blocked(postgres, first_pid.get(timeout=5), second_pid.get(timeout=5), second)
        finally:
            release.set()
        assert first.result(timeout=5)
        second.result(timeout=5)
    row = db_session.scalar(
        select(BankEncryptionKey).where(
            BankEncryptionKey.bank_id == BANK, BankEncryptionKey.organization_id == ORG_1
        )
    )
    assert row is not None and row.key_id == NEXT.key_id
    assert row.index_version_id is not None and row.current_version_id != row.index_version_id
    assert _versions(db_session) == 2


@pytest.mark.parametrize("leader", ["staff", "administrator"])
def test_registration_fk_serializes_between_staff_and_administrator(
    db_session: Session,
    postgres: Engine,
    local_keys: LocalKeyProvider,
    leader: Literal["staff", "administrator"],
) -> None:
    entered, release = Event(), Event()
    first_pid: Queue[int] = Queue()
    second_pid: Queue[int] = Queue()

    def pause_insert(peer: Session, _flush: UOWTransaction) -> None:
        if not entered.is_set() and any(
            isinstance(row, BankEncryptionKey) for row in cast(Iterable[object], peer.new)
        ):
            entered.set()
            assert release.wait(10)

    def connect(owner: Literal["staff", "administrator"], *, leading: bool) -> None:
        with Session(postgres) as writer:
            writer.info["organization_id"] = ORG_1
            (first_pid if leading else second_pid).put(backend_pid(writer))

            def invoke() -> None:
                if owner == "staff":
                    staff.connect_key(writer, ORG_1, BANK, KEY)
                else:
                    administrator.connect(writer, CTX, BANK, KEY)

            if leading:
                event.listen(writer, "after_flush", pause_insert)
                invoke()
                writer.commit()
            else:
                with pytest.raises(HTTPException) as existing:
                    invoke()
                assert existing.value.status_code == 409

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(connect, leader, leading=True)
        try:
            assert entered.wait(5)
            second = pool.submit(
                connect, "administrator" if leader == "staff" else "staff", leading=False
            )
            assert_blocked(postgres, first_pid.get(timeout=5), second_pid.get(timeout=5), second)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    state = administrator.status(db_session, CTX, BANK)
    assert state.connected and state.key is not None and state.key.key_id == KEY.key_id
    assert state.current_version is not None and _versions(db_session) == 1
