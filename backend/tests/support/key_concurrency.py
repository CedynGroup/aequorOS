from __future__ import annotations

import time
from concurrent.futures import Future
from typing import cast

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session


def backend_pid(db: Session) -> int:
    return cast(int, db.scalar(text("SELECT pg_backend_pid()")))


def assert_blocked(engine: Engine, blocker: int, waiter: int, change: Future[None]) -> None:
    deadline = time.monotonic() + 5
    blocked = False
    with engine.connect() as observer:
        while not blocked and not change.done() and time.monotonic() < deadline:
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
