"""Operator cursors remain stable when an earlier audit insert commits late."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from typing import cast
from uuid import UUID

import pytest
from pydantic import BaseModel, JsonValue
from sqlalchemy import Integer, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import OperatorAuditLog
from app.operator.deps import OperatorContext, record_operator_action
from app.operator.features import audit_log
from app.schemas.operator import OperatorAuditLogListRead
from tests.db.test_database_roles import administrator, role_banks, role_schema
from tests.support.helpers import ORG_1

__all__ = ["administrator", "role_banks", "role_schema"]
pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required"),
]


def _assert_reader_waits_for_writer(
    administrator: Engine, writer_pid: int, reader_pid: int
) -> None:
    blocked = False
    deadline = time.monotonic() + 10
    with administrator.connect() as observer:
        while not blocked and time.monotonic() < deadline:
            blocked = cast(
                bool,
                observer.scalar(
                    text("SELECT :writer = ANY(pg_blocking_pids(:reader))"),
                    {"writer": writer_pid, "reader": reader_pid},
                ),
            )
            if not blocked:
                time.sleep(0.01)
    assert blocked


def test_cursor_excludes_late_commit_ahead_of_the_last_displayed_row(
    administrator: Engine, role_banks: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    operator = OperatorContext("pagination@example.test", "dev", "super_admin")
    bank_id = role_banks[ORG_1]
    timestamp = utc_now() - timedelta(hours=1)
    expected = [str(UUID(int=index)) for index in range(6, 0, -1)]
    with Session(administrator) as db:
        db.add_all(
            [
                OperatorAuditLog(
                    id=UUID(int=index),
                    operator_email=operator.email,
                    auth_mode="dev",
                    target_org=ORG_1,
                    action="pagination.probe",
                    detail={"bank_id": bank_id},
                    created_at=timestamp,
                )
                for index in range(1, 7)
            ]
        )
        db.commit()
    ready = Event()
    reader_pids: Queue[int] = Queue()

    def observed_record(
        db: Session,
        operator: OperatorContext,
        *,
        action: str,
        resources: BaseModel | list[dict[str, JsonValue]] | None = None,
    ) -> OperatorAuditLog:
        ready.set()
        return record_operator_action(db, operator, action=action, resources=resources)

    monkeypatch.setattr(audit_log, "record_operator_action", observed_record)

    def first_read() -> OperatorAuditLogListRead:
        with Session(administrator) as db:
            pid = db.scalar(select(func.pg_backend_pid().cast(Integer)))
            assert pid is not None
            reader_pids.put(pid)
            return audit_log.read_audit_log(db, operator, action="pagination.probe", limit=2)

    with Session(administrator) as pending, ThreadPoolExecutor(max_workers=1) as pool:
        writer_pid = pending.scalar(select(func.pg_backend_pid().cast(Integer)))
        assert writer_pid is not None
        delayed = OperatorAuditLog(
            operator_email=operator.email,
            auth_mode="dev",
            target_org=ORG_1,
            action="pagination.probe",
            detail={"bank_id": bank_id},
        )
        pending.add(delayed)
        pending.flush()
        future = pool.submit(first_read)
        try:
            reader_pid = reader_pids.get(timeout=10)
            assert ready.wait(timeout=10)
            _assert_reader_waits_for_writer(administrator, writer_pid, reader_pid)
            pending.commit()
            first = future.result(timeout=10)
        finally:
            pending.rollback()
    assert [item.id for item in first.items] == expected[:2]
    assert first.total == 6 and first.next_cursor is not None
    seen = [item.id for item in first.items]
    cursor = first.next_cursor
    while cursor is not None:
        with Session(administrator) as db:
            page = audit_log.read_audit_log(
                db, operator, action="pagination.probe", limit=2, cursor=cursor
            )
        assert page.as_of == first.as_of and page.total == 7
        assert set(seen).isdisjoint(item.id for item in page.items)
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor
    assert seen == expected
    with Session(administrator) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(OperatorAuditLog)
                .where(OperatorAuditLog.action == "audit.read")
            )
            == 3
        )
