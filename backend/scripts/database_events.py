"""Stdlib-only observation protocol shared by pytest and Python startup."""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

DIRECTORY_ENV = "CI_DATABASE_OBSERVATIONS"
CONTEXT_ENV = "CI_DATABASE_CONTEXT"
RUN_ENV = "CI_DATABASE_OBSERVATION_RUN"

_inherited_context: ContextVar[dict[str, object] | None] = ContextVar(
    "database_observation_context", default=None
)


def context() -> dict[str, object]:
    inherited = _inherited_context.get()
    if inherited is not None:
        return inherited
    return cast(dict[str, object], json.loads(os.getenv(CONTEXT_ENV, "{}")))


@contextmanager
def inherited_context(origin: dict[str, object]) -> Generator[None]:
    """Keep deferred work attributed to the test or fixture that started it."""
    token = _inherited_context.set(origin)
    try:
        yield
    finally:
        _inherited_context.reset(token)


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


def service_connection(address: object) -> None:
    if isinstance(address, tuple) and len(cast(tuple[object, ...], address)) >= 2:
        host, port = cast(tuple[str, int], address)[:2]
        for name in ("S3_ENDPOINT", "OPENBAO_TEST_ADDR"):
            endpoint = urlsplit(os.getenv(name, ""))
            hosts = {endpoint.hostname}
            if endpoint.hostname in {"localhost", "127.0.0.1", "::1"}:
                hosts.update({"localhost", "127.0.0.1", "::1"})
            if host in hosts and port == (
                endpoint.port or (443 if endpoint.scheme == "https" else 80)
            ):
                emit("service", service=name, suite="integration")


def service_command(command: object) -> None:
    if isinstance(command, (tuple, list)) and command:
        words = [str(word) for word in cast(list[object], command)]
        executable = Path(words[0]).name
        if executable in {"minio", "bao"}:
            emit("service", service=executable, suite="integration")
        if executable in {"node", "pnpm", "npm", "npx", "playwright"} and any(
            "playwright" in word for word in words
        ):
            emit("service", service="playwright", suite="e2e")
