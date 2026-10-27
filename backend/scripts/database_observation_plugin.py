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
_serial = 0
_node = ""
_nested = False
_selected = False
_PATCHES = pytest.MonkeyPatch()


def _context() -> None:
    os.environ[trace.CONTEXT_ENV] = json.dumps({"nodeid": _node, "fixtures": list(_STACK)})


def pytest_load_initial_conftests() -> None:
    global _nested  # noqa: PLW0603
    if not os.getenv(trace.DIRECTORY_ENV):
        return
    _nested = bool(trace.context().get("nodeid"))
    trace.install()


def pytest_configure() -> None:
    if not os.getenv(trace.DIRECTORY_ENV) or _nested:
        return
    original = cast(Callable[..., None], FixtureDef.finish)
    original_execute = cast(Callable[..., object], FixtureDef.execute)

    def execute(fixture: FixtureDef[object], request: SubRequest) -> object:
        try:
            return original_execute(fixture, request)
        finally:
            key = _FIXTURES.get(id(fixture))
            if key is not None:
                item = cast(pytest.Item, getattr(request, "_pyfuncitem"))
                trace.emit("consumes", nodeid=item.nodeid, fixtures=[key])

    def finish(fixture: FixtureDef[object], request: SubRequest) -> None:
        key = _FIXTURES.get(id(fixture))
        if key is not None:
            _STACK.append(key)
            _context()
        try:
            original(fixture, request)
        finally:
            if key is not None:
                _ = _STACK.pop()
                _context()
                _ = _FIXTURES.pop(id(fixture), None)

    _PATCHES.setattr(FixtureDef, "execute", execute)
    _PATCHES.setattr(FixtureDef, "finish", finish)


def pytest_collection_finish(session: pytest.Session) -> None:
    global _selected  # noqa: PLW0603
    if os.getenv(trace.DIRECTORY_ENV) and not _nested:
        _selected = True
        trace.emit("session_start")
        trace.emit("selected", nodeids=[item.nodeid for item in session.items])


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    global _node  # noqa: PLW0603
    if not os.getenv(trace.DIRECTORY_ENV) or _nested:
        yield
        return
    _node = item.nodeid
    _context()
    try:
        yield
    finally:
        _node = ""
        _context()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_fixture_setup(fixturedef: FixtureDef[object]) -> Iterator[None]:
    if not os.getenv(trace.DIRECTORY_ENV) or _nested:
        yield
        return
    global _serial  # noqa: PLW0603
    _serial += 1
    key = f"{os.getpid()}:{_serial}:{fixturedef.argname}"
    _FIXTURES[id(fixturedef)] = key
    _STACK.append(key)
    _context()
    try:
        yield
    finally:
        _ = _STACK.pop()
        _context()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if os.getenv(trace.DIRECTORY_ENV) and _selected:
        trace.emit("report", nodeid=report.nodeid, when=report.when, outcome=report.outcome)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _ = session
    if os.getenv(trace.DIRECTORY_ENV) and _selected:
        trace.emit("session_finish", status=exitstatus)


def pytest_unconfigure() -> None:
    _PATCHES.undo()
