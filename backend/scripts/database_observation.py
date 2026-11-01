"""Database acquisition tracing shared by pytest and inherited Python children.

Events record connection entry points, including pooled checkouts and attempts
that callers catch. A failed acquisition still makes a test database-dependent:
the unit refusal guard must not replace the driver's failure behavior.
"""

from __future__ import annotations

import importlib
import json
import os
import socket
import sqlite3
import sqlite3.dbapi2 as sqlite_dbapi
import subprocess
import uuid
from collections.abc import Callable, Mapping
from functools import wraps
from pathlib import Path
from threading import Thread
from typing import cast

import psycopg
from sqlalchemy import Engine

from scripts.ci_database_children import (
    confirm_startup_before_signal,
    instrument_command,
    python_path,
)
from scripts.database_events import (
    CONTEXT_ENV,
    DIRECTORY_ENV,
    RUN_ENV,
    context,
    emit,
    inherited_context,
    service_connection,
)
from scripts.database_events import service_command as _command_service

__all__ = ["CONTEXT_ENV", "DIRECTORY_ENV", "RUN_ENV", "context", "emit", "install"]

_installed = False


def _observe_services() -> None:
    original = socket.socket.connect

    def service_connect(connection: socket.socket, address: object) -> None:
        service_connection(address)
        original(connection, cast(tuple[str, int], address))

    socket.socket.connect = service_connect


def _observe_threads() -> None:
    original = Thread.start

    def start(thread: Thread) -> None:
        origin = context()
        run = thread.run

        def observed_run() -> None:
            with inherited_context(origin):
                run()

        thread.run = observed_run
        original(thread)

    patch: Callable[[object, str, object], None] = setattr
    patch(Thread, "start", start)


def install() -> None:
    """Observe without refusing or replacing connection results/exceptions."""
    global _installed  # noqa: PLW0603
    if _installed or not os.getenv(DIRECTORY_ENV):
        return
    _installed = True
    os.environ["PYTHONPATH"] = python_path(os.environ.get("PYTHONPATH", ""))
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

    _observe_services()
    _observe_threads()
    _observe_python_children()
    emit("instrumented")


def _observe_python_children() -> None:
    original_popen = cast(Callable[..., None], subprocess.Popen.__init__)

    def observed_popen(*args: object, **kwargs: object) -> None:
        supplied = kwargs.get("env")
        environment = dict(os.environ if supplied is None else cast(Mapping[str, str], supplied))
        _ = environment.pop("CI_DATABASE_CENSUS", None)
        environment[DIRECTORY_ENV] = os.environ[DIRECTORY_ENV]
        environment[CONTEXT_ENV] = json.dumps(context())
        environment[RUN_ENV] = os.environ[RUN_ENV]
        environment["PYTHONPATH"] = python_path(environment.get("PYTHONPATH", ""))
        kwargs["env"] = environment
        command = args[1] if len(args) > 1 else kwargs.get("args")
        _command_service(command)
        replacement = instrument_command(command)
        if len(args) > 1:
            args = (args[0], replacement, *args[2:])
        else:
            kwargs["args"] = replacement
        original_popen(*args, **kwargs)
        if args:
            confirm_startup_before_signal(
                cast(subprocess.Popen[bytes], args[0]), environment, command
            )
        # Launchers must confirm instrumentation; an unconfirmed child cannot
        # establish database freedom. Isolated inline programs use stdlib tracing.
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
