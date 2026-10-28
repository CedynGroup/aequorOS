"""Stdlib-only bootstrap: keep -I/-S and the original inline program's argv.

Profiling observes driver entry points when they are actually imported by the
program. No application or third-party package is imported by instrumentation.
SQLite's process-wide audit event also covers acquisitions on other threads.
"""

import json
import os
import sys
from pathlib import Path
from types import FrameType
from typing import cast


def _database_bootstrap() -> None:

    program = sys.argv.pop(1)
    context = cast(dict[str, object], json.loads(os.getenv("CI_DATABASE_CONTEXT", "{}")))
    directory = os.getenv("CI_DATABASE_OBSERVATIONS")
    attempts = os.getenv("CI_NO_DATABASE_ATTEMPTS")

    def emit(kind: str, **values: object) -> None:
        if directory:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            event = {"kind": kind, "pid": os.getpid(), **context, **values}
            with (path / f"events-{os.getpid()}.jsonl").open("a") as stream:
                _ = stream.write(json.dumps(event) + "\n")

    def database(entrypoint: str) -> None:
        emit("database", entrypoint=entrypoint)
        if attempts:
            with Path(attempts).open("a") as stream:
                _ = stream.write(f"Database connection attempted by process {os.getpid()}\n")
            raise AssertionError("Database connections are forbidden in unit tests")

    modules = (
        "sqlalchemy.engine",
        "psycopg",
        "oracledb",
        "pyodbc",
        "jaydebeapi",
        "snowflake.connector",
        "asyncpg",
    )
    names = {"connect", "raw_connection", "connect_async", "create_pool", "create_pool_async"}

    def profile(frame: FrameType, event: str, argument: object) -> None:
        if event == "call":
            module = cast(str, frame.f_globals.get("__name__", ""))
            name = frame.f_code.co_name
        elif event == "c_call":
            module = cast(str, getattr(argument, "__module__", "") or "")
            name = cast(str, getattr(argument, "__name__", ""))
        else:
            return
        if name in names and any(
            module == prefix or module.startswith(prefix + ".") for prefix in modules
        ):
            database(module + "." + name)

    def audit(event: str, arguments: tuple[object, ...]) -> None:
        _ = arguments
        if event == "sqlite3.connect":
            database(event)

    emit("instrumented")
    sys.addaudithook(audit)
    sys.setprofile(profile)
    # The bootstrap runs in a separate namespace. Keep the original -c
    # globals and flags, without injecting instrumentation imports there.
    del globals()["_database_bootstrap"]
    exec(compile(program, "<string>", "exec"), sys.modules["__main__"].__dict__)


if __name__ == "__main__":
    _database_bootstrap()
