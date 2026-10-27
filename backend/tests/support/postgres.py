"""Observe real PostgreSQL lock contention before releasing a test's holder."""

from concurrent.futures import Future
from time import monotonic, sleep
from typing import cast

from sqlalchemy import Engine, text


def wait_for_blocker[T](
    engine: Engine,
    contender_pid: int,
    blocker_pid: int,
    contender: Future[T],
) -> None:
    deadline = monotonic() + 10
    with engine.connect() as observer:
        while monotonic() < deadline:
            if contender.done():
                _ = contender.result()
                raise AssertionError("Contender completed without waiting for the holder")
            blocked = cast(
                object,
                observer.scalar(
                    text("SELECT :blocker = ANY(pg_blocking_pids(:contender))"),
                    {"blocker": blocker_pid, "contender": contender_pid},
                ),
            )
            observer.rollback()
            if blocked is True:
                assert not contender.done(), "Contender completed while its holder was locked"
                return
            sleep(0.01)
    raise AssertionError(f"Backend {contender_pid} never waited for holder {blocker_pid}")
