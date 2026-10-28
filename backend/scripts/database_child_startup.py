"""Keep ordinary Python children instrumented without eagerly importing drivers."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from functools import wraps
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec, PathFinder
from types import ModuleType
from typing import cast, override

from scripts.ci_database_children import (
    confirm_startup_before_signal,
    instrument_command,
    python_path,
)
from scripts.database_events import (
    CONTEXT_ENV,
    DIRECTORY_ENV,
    RUN_ENV,
    emit,
    service_command,
    service_connection,
)

_DRIVERS = {
    "sqlalchemy",
    "psycopg",
    "sqlite3",
    "oracledb",
    "pyodbc",
    "jaydebeapi",
    "snowflake.connector",
    "asyncpg",
}


class _ObservedLoader(Loader):
    def __init__(self, loader: Loader, activate: Callable[[ModuleType], None]) -> None:
        self.loader = loader
        self.activate = activate

    @override
    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        return self.loader.create_module(spec)

    @override
    def exec_module(self, module: ModuleType) -> None:
        self.loader.exec_module(module)
        self.activate(module)

    def __getattr__(self, name: str) -> object:
        # Preserve package resource readers and other loader capabilities.
        return cast(object, getattr(self.loader, name))


class _DriverFinder(MetaPathFinder):
    def __init__(self, activate: Callable[[ModuleType], None]) -> None:
        self.activate = activate

    @override
    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if fullname not in _DRIVERS:
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.loader is not None:
            spec.loader = _ObservedLoader(spec.loader, self.activate)
        return spec


def _install_children(inherited: dict[str, str]) -> None:
    original = cast(Callable[..., None], subprocess.Popen.__init__)

    def popen(*args: object, **kwargs: object) -> None:
        supplied = kwargs.get("env")
        environment = dict(os.environ if supplied is None else cast(Mapping[str, str], supplied))
        for name, previous in inherited.items():
            environment[name] = os.getenv(name) or previous
        environment["PYTHONPATH"] = python_path(environment.get("PYTHONPATH", ""))
        kwargs["env"] = environment
        command = args[1] if len(args) > 1 else kwargs.get("args")
        replacement = instrument_command(command)
        if len(args) > 1:
            args = (args[0], replacement, *args[2:])
        else:
            kwargs["args"] = replacement
        original(*args, **kwargs)
        if args:
            process = cast(subprocess.Popen[bytes], args[0])
            confirm_startup_before_signal(process, environment, command)
            if isinstance(command, (list, tuple)) and command:
                executable = str(cast(list[object], command)[0]).rsplit("/", 1)[-1]
                if executable.startswith("python") or executable in {"uv", "pytest"}:
                    emit("python_child", child=process.pid)

    patch: Callable[[object, str, object], None] = setattr
    patch(subprocess.Popen, "__init__", popen)


def _install_audit(inherited: dict[str, str]) -> None:
    def audit(event: str, arguments: tuple[object, ...]) -> None:
        if event == "sqlite3.connect":
            _database_attempt(event, inherited)
        elif event == "socket.connect":
            service_connection(arguments[1])
        elif event == "subprocess.Popen":
            service_command(arguments[1])
            command = arguments[1]
            if isinstance(command, (list, tuple)) and command:
                executable = str(cast(list[object], command)[0]).rsplit("/", 1)[-1]
                if executable in {"psql", "sqlite3", "mysql", "sqlcmd"}:
                    _database_attempt(executable, inherited)

    sys.addaudithook(audit)


def _database_attempt(entrypoint: str, inherited: dict[str, str]) -> None:
    emit("database", entrypoint=entrypoint)
    attempts = os.getenv("CI_NO_DATABASE_ATTEMPTS") or inherited.get("CI_NO_DATABASE_ATTEMPTS")
    if attempts:
        with open(attempts, "a") as stream:
            _ = stream.write(f"Database connection attempted by process {os.getpid()}\n")
        raise AssertionError("Database connections are forbidden in unit tests")


def _instrument_driver(module: ModuleType, inherited: dict[str, str]) -> None:
    targets: list[object] = [module]
    if module.__name__ == "sqlalchemy":
        targets = [cast(object, module.Engine)]
    elif module.__name__ == "psycopg":
        targets.extend(
            [cast(object, getattr(module, name)) for name in ("Connection", "AsyncConnection")]
        )

    def instrument(target: object, name: str) -> None:
        original = cast(Callable[..., object], getattr(target, name))

        @wraps(original)
        def observed(*args: object, **kwargs: object) -> object:
            _database_attempt(f"{module.__name__}.{name}", inherited)
            return original(*args, **kwargs)

        patch: Callable[[object, str, object], None] = setattr
        patch(target, name, observed)

    for target in targets:
        for name in (
            "connect",
            "raw_connection",
            "connect_async",
            "create_pool",
            "create_pool_async",
        ):
            if callable(getattr(target, name, None)):
                instrument(target, name)


def install() -> None:
    _ = os.environ.setdefault(RUN_ENV, uuid.uuid4().hex)
    inherited = {
        name: os.environ[name]
        for name in (DIRECTORY_ENV, CONTEXT_ENV, RUN_ENV, "CI_NO_DATABASE_ATTEMPTS")
        if name in os.environ
    }

    def instrument(module: ModuleType) -> None:
        # Each loader patches only its completed module. Importing other drivers
        # here would race or deadlock with concurrent imports on other threads.
        _instrument_driver(module, inherited)

    sys.meta_path.insert(0, _DriverFinder(instrument))
    for name in _DRIVERS:
        module = sys.modules.get(name)
        if module is not None:
            instrument(module)

    _install_children(inherited)

    _install_audit(inherited)
    emit("instrumented")
