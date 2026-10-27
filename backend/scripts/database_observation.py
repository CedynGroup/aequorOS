"""Database acquisition tracing shared by pytest and inherited Python children.

Events record connection entry points, including pooled checkouts and attempts
that callers catch. A failed acquisition still makes a test database-dependent:
the unit refusal guard must not replace the driver's failure behavior.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sqlite3.dbapi2 as sqlite_dbapi
import subprocess
import uuid
from collections.abc import Callable, Mapping
from functools import wraps
from pathlib import Path
from typing import cast

import psycopg
from sqlalchemy import Engine

DIRECTORY_ENV = "CI_DATABASE_OBSERVATIONS"
CONTEXT_ENV = "CI_DATABASE_CONTEXT"
RUN_ENV = "CI_DATABASE_OBSERVATION_RUN"
_installed = False


def context() -> dict[str, object]:
    return cast(dict[str, object], json.loads(os.getenv(CONTEXT_ENV, "{}")))


def emit(kind: str, **values: object) -> None:
    directory = os.getenv(DIRECTORY_ENV)
    if not directory:
        return
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    event = {"kind": kind, "pid": os.getpid(), "run": os.getenv(RUN_ENV, ""), **context(), **values}
    descriptor = os.open(
        path / f"events-{os.getpid()}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
    )
    try:
        _ = os.write(descriptor, (json.dumps(event, sort_keys=True) + "\n").encode())
    finally:
        os.close(descriptor)


def install() -> None:
    """Observe without refusing or replacing connection results/exceptions."""
    global _installed  # noqa: PLW0603
    if _installed or not os.getenv(DIRECTORY_ENV):
        return
    _installed = True
    _ = os.environ.setdefault(RUN_ENV, uuid.uuid4().hex)

    def instrument(target: object, name: str) -> None:
        original = cast(Callable[..., object], getattr(target, name))

        @wraps(original)
        def observed(*args: object, **kwargs: object) -> object:
            emit(
                "database",
                entrypoint=f"{getattr(target, '__name__', type(target).__name__)}.{name}",
            )
            return original(*args, **kwargs)

        setattr(target, name, observed)

    for target, names in (
        (Engine, ("connect", "raw_connection")),
        (psycopg, ("connect",)),
        (psycopg.Connection, ("connect",)),
        (psycopg.AsyncConnection, ("connect",)),
        (sqlite3, ("connect",)),
        (sqlite_dbapi, ("connect",)),
    ):
        for name in names:
            instrument(target, name)
    for name in ("oracledb", "pyodbc", "jaydebeapi", "snowflake.connector", "asyncpg"):
        try:
            driver = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        for attribute in ("connect", "connect_async", "create_pool", "create_pool_async"):
            if hasattr(driver, attribute):
                instrument(driver, attribute)

    original_popen = cast(Callable[..., None], subprocess.Popen.__init__)
    backend = Path(__file__).resolve().parents[1]
    bootstrap = str(Path(__file__).with_name("ci_no_database"))

    def observed_popen(*args: object, **kwargs: object) -> None:
        supplied = kwargs.get("env")
        environment = dict(os.environ if supplied is None else cast(Mapping[str, str], supplied))
        _ = environment.pop("CI_DATABASE_CENSUS", None)
        environment[DIRECTORY_ENV] = os.environ[DIRECTORY_ENV]
        environment[CONTEXT_ENV] = os.getenv(CONTEXT_ENV, "{}")
        environment[RUN_ENV] = os.environ[RUN_ENV]
        environment["PYTHONPATH"] = os.pathsep.join(
            [bootstrap, str(backend), environment.get("PYTHONPATH", "")]
        )
        kwargs["env"] = environment
        original_popen(*args, **kwargs)
        # Python launchers must confirm that sitecustomize was loaded. Isolated
        # Python (-I/-S) is inconclusive rather than proof of database freedom.
        command = args[1] if len(args) > 1 else kwargs.get("args")
        if isinstance(command, (list, tuple)) and command and args:
            executable = Path(str(cast(list[object], command)[0])).name
            process = cast(subprocess.Popen[bytes], args[0])
            if executable.startswith("python") or executable in {"uv", "pytest"}:
                emit("python_child", child=process.pid)
            if executable in {"psql", "sqlite3", "mysql", "sqlcmd"}:
                emit("database", entrypoint=executable)

    patch: Callable[[object, str, object], None] = setattr
    patch(subprocess.Popen, "__init__", observed_popen)
    emit("instrumented")
