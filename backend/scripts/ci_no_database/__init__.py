"""Connection refusal inherited by Python subprocesses in the pure CI owner."""

from __future__ import annotations

import os
import sqlite3
import sqlite3.dbapi2 as sqlite_dbapi
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NoReturn, cast

import psycopg
from sqlalchemy import Engine


def install(patch: Callable[[object, str, object], None] = setattr) -> None:
    """Record before raising so catching a refusal cannot make the run pass."""
    attempts = os.environ["CI_NO_DATABASE_ATTEMPTS"]

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        with Path(attempts).open("a") as stream:
            stream.write(f"Database connection attempted by process {os.getpid()}\n")
        raise AssertionError("Database connections are forbidden in the pure CI owner")

    patch(Engine, "connect", refuse)
    patch(psycopg, "connect", refuse)
    patch(psycopg.Connection, "connect", refuse)
    patch(psycopg.AsyncConnection, "connect", refuse)
    patch(sqlite3, "connect", refuse)
    patch(sqlite_dbapi, "connect", refuse)

    # Import checks deliberately supply fresh environments. Carry the guard
    # through those too, without changing their programs or assertions.
    original = cast(Callable[..., None], subprocess.Popen.__init__)
    backend = Path(__file__).resolve().parents[2]
    guard = str(Path(__file__).resolve().parent)

    def guarded_popen(*args: object, **kwargs: object) -> None:
        supplied = kwargs.get("env")
        environment = dict(os.environ if supplied is None else cast(Mapping[str, str], supplied))
        environment["CI_NO_DATABASE_ATTEMPTS"] = attempts
        environment["PYTHONPATH"] = os.pathsep.join(
            [guard, str(backend), environment.get("PYTHONPATH", "")]
        )
        kwargs["env"] = environment
        original(*args, **kwargs)

    patch(subprocess.Popen, "__init__", guarded_popen)
