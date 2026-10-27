"""Connection refusal inherited by Python subprocesses in database-free test runs."""

from __future__ import annotations

import importlib
import os
import sqlite3
import sqlite3.dbapi2 as sqlite_dbapi
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NoReturn, cast

import psycopg
from sqlalchemy import Engine

from scripts.ci_database_children import instrument_command

_ORIGINAL_POPEN = cast(Callable[..., None], subprocess.Popen.__init__)


def install(patch: Callable[[object, str, object], None] = setattr) -> None:
    """Record before raising so catching a refusal cannot make the run pass."""
    attempts = os.environ["CI_NO_DATABASE_ATTEMPTS"]

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        with Path(attempts).open("a") as stream:
            stream.write(f"Database connection attempted by process {os.getpid()}\n")
        raise AssertionError("Database connections are forbidden in unit tests")

    patch(Engine, "connect", refuse)
    patch(Engine, "raw_connection", refuse)
    patch(psycopg, "connect", refuse)
    patch(psycopg.Connection, "connect", refuse)
    patch(psycopg.AsyncConnection, "connect", refuse)
    patch(sqlite3, "connect", refuse)
    patch(sqlite_dbapi, "connect", refuse)

    # Database-direct adapters have DBAPI entry points outside SQLAlchemy.
    # Optional drivers remain optional; installed drivers share the refusal.
    for name in ("oracledb", "pyodbc", "jaydebeapi", "snowflake.connector", "asyncpg"):
        try:
            driver = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        for attribute in ("connect", "connect_async", "create_pool", "create_pool_async"):
            if hasattr(driver, attribute):
                patch(driver, attribute, refuse)

    # Import checks deliberately supply fresh environments. Carry the guard
    # through those too, without changing their programs or assertions.
    backend = Path(__file__).resolve().parents[2]
    guard = str(Path(__file__).resolve().parent)

    def guarded_popen(*args: object, **kwargs: object) -> None:
        supplied = kwargs.get("env")
        environment = dict(os.environ if supplied is None else cast(Mapping[str, str], supplied))
        environment["CI_NO_DATABASE_ATTEMPTS"] = attempts
        for key in (
            "CI_DATABASE_OBSERVATIONS",
            "CI_DATABASE_CONTEXT",
            "CI_DATABASE_OBSERVATION_RUN",
        ):
            if key in os.environ:
                environment[key] = os.environ[key]
        environment["PYTHONPATH"] = os.pathsep.join(
            [guard, str(backend), environment.get("PYTHONPATH", "")]
        )
        kwargs["env"] = environment
        command = args[1] if len(args) > 1 else kwargs.get("args")
        replacement = instrument_command(command)
        if len(args) > 1:
            args = (args[0], replacement, *args[2:])
        else:
            kwargs["args"] = replacement
        _ORIGINAL_POPEN(*args, **kwargs)

    patch(subprocess.Popen, "__init__", guarded_popen)
