"""Attribute database use to test phases and every shared-fixture consumer."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from typing import cast

import pytest
from _pytest.fixtures import FixtureDef, SubRequest

from scripts import database_observation as trace

_FIXTURES: dict[int, str] = {}
_STACK: list[str] = []
_SERIAL = 0
_NODE = ""
_NESTED = False
_PATCHES = pytest.MonkeyPatch()


def _context() -> None:
    os.environ[trace.CONTEXT_ENV] = json.dumps({"nodeid": _NODE, "fixtures": list(_STACK)})


def pytest_load_initial_conftests() -> None:
    global _NESTED  # noqa: PLW0603
    if not os.getenv(trace.DIRECTORY_ENV):
        return
    _NESTED = bool(trace.context().get("nodeid"))
    trace.install()


def pytest_configure() -> None:
    if not os.getenv(trace.DIRECTORY_ENV) or _NESTED:
        return
    original = cast(Callable[..., None], FixtureDef.finish)

    def finish(fixture: FixtureDef[object], request: SubRequest) -> None:
        key = _FIXTURES.get(id(fixture))
        if key is not None:
            _STACK.append(key)
            _context()
        try:
            original(fixture, request)
        finally:
            if key is not None:
                _STACK.pop()
                _context()

    _PATCHES.setattr(FixtureDef, "finish", finish)
    trace.emit("session_start")


def pytest_collection_finish(session: pytest.Session) -> None:
    if os.getenv(trace.DIRECTORY_ENV) and not _NESTED:
        trace.emit("selected", nodeids=[item.nodeid for item in session.items])


def _consumers(item: pytest.Item) -> None:
    request = cast(object, getattr(item, "_request", None))
    definitions = cast(dict[str, object], getattr(request, "_fixture_defs", {}))
    trace.emit(
        "consumes",
        nodeid=item.nodeid,
        fixtures=[
            _FIXTURES[id(fixture)] for fixture in definitions.values() if id(fixture) in _FIXTURES
        ],
    )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    global _NODE  # noqa: PLW0603
    if not os.getenv(trace.DIRECTORY_ENV) or _NESTED:
        yield
        return
    _NODE = item.nodeid
    _context()
    try:
        yield
    finally:
        _consumers(item)
        _NODE = ""
        _context()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_fixture_setup(fixturedef: FixtureDef[object], request: SubRequest) -> Iterator[None]:
    if not os.getenv(trace.DIRECTORY_ENV) or _NESTED:
        yield
        return
    global _SERIAL  # noqa: PLW0603
    _SERIAL += 1
    key = f"{os.getpid()}:{_SERIAL}:{fixturedef.argname}"
    _FIXTURES[id(fixturedef)] = key
    _STACK.append(key)
    _context()
    try:
        yield
    finally:
        _STACK.pop()
        _context()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    if os.getenv(trace.DIRECTORY_ENV) and not _NESTED:
        _consumers(item)
    yield
    if os.getenv(trace.DIRECTORY_ENV) and not _NESTED:
        _consumers(item)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if os.getenv(trace.DIRECTORY_ENV) and not _NESTED:
        trace.emit("report", nodeid=report.nodeid, when=report.when, outcome=report.outcome)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if os.getenv(trace.DIRECTORY_ENV) and not _NESTED:
        trace.emit("session_finish", status=exitstatus)


def pytest_unconfigure() -> None:
    _PATCHES.undo()
