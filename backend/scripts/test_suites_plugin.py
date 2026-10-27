"""Classify before selection and refuse database access throughout unit execution."""

from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from _pytest.terminal import TerminalReporter

from scripts.ci_no_database import install
from scripts.test_suites import case_key, catalogue, owner

_PATCHES = pytest.StashKey[pytest.MonkeyPatch]()
_DIRECTORY = pytest.StashKey[Path]()
_ATTEMPTS = pytest.StashKey[Path]()
_EXPECTED = pytest.StashKey[list[str]]()


def _worker(config: pytest.Config) -> str:
    worker = cast(dict[str, object] | None, getattr(config, "workerinput", None))
    return cast(str, worker["workerid"]) if worker is not None else "main"


def _install(config: pytest.Config) -> None:
    directory = config.stash.get(_DIRECTORY, None)
    if directory is None:
        directory = Path(os.getenv("CI_UNIT_REPORT_DIR") or tempfile.mkdtemp(prefix="unit-tests-"))
    directory.mkdir(parents=True, exist_ok=True)
    attempts = directory / f"connection-attempts-{os.getpid()}.txt"
    _ = attempts.write_text("")
    patches = pytest.MonkeyPatch()
    patches.setenv("CI_NO_DATABASE_ATTEMPTS", str(attempts.resolve()))
    install(patches.setattr)
    config.stash[_PATCHES] = patches
    config.stash[_DIRECTORY] = directory
    config.stash[_ATTEMPTS] = attempts


def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    if (
        not os.getenv("CI_DATABASE_CENSUS")
        and cast(object, early_config.known_args_namespace.markexpr) == "unit"
    ):
        _install(early_config)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: database-free test; database connections are refused")
    config.addinivalue_line("markers", "integration: test requiring a database or external service")
    if os.getenv("CI_UNIT_REPORT_DIR") and not config.getoption("collectonly"):
        if config.getoption("markexpr") != "unit" or _DIRECTORY not in config.stash:
            raise pytest.UsageError("Unit CI owner requires the unit selector and connection guard")
        # xdist sets workerinput after initial conftest loading. Clean once on
        # the controller here, before workers start, preserving early attempts.
        if _worker(config) == "main":
            directory = config.stash[_DIRECTORY]
            for pattern in ("connection-attempts*.txt", "collection*.json"):
                for stale in directory.glob(pattern):
                    if stale != config.stash[_ATTEMPTS]:
                        stale.unlink()
        if not cast(object, config.getoption("xmlpath")):
            config.option.xmlpath = str(config.stash[_DIRECTORY] / "report.xml")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("CI_DATABASE_CENSUS"):
        return
    nodeids = [item.nodeid for item in items]
    if len(set(nodeids)) != len(nodeids):
        raise pytest.UsageError("Duplicate test collection")
    classified: dict[str, str] = {}
    for item in items:
        try:
            suite = owner(item.nodeid)
        except ValueError as error:
            raise pytest.UsageError(str(error)) from error
        markers = [
            mark.name for mark in item.iter_markers() if mark.name in ("unit", "integration")
        ]
        if markers and markers != [suite]:
            raise pytest.UsageError(f"Double-classified or conflicting suite: {item.nodeid}")
        if not markers:
            item.add_marker(suite)
        classified[item.nodeid] = suite
    if os.getenv("CI_UNIT_REPORT_DIR") and not config.getoption("collectonly"):
        modules, overrides = catalogue()
        if set(modules) != {nodeid.split("::")[0] for nodeid in nodeids}:
            raise pytest.UsageError("Unit CI must collect the entire classified test tree")
        if set(overrides) != set(nodeids):
            raise pytest.UsageError("Stale or incomplete generated test suite catalogue")
        config.stash[_EXPECTED] = [
            nodeid for nodeid, suite in classified.items() if suite == "unit"
        ]
        path = config.stash[_DIRECTORY] / f"collection-{_worker(config)}.json"
        _ = path.write_text(json.dumps(classified))


def pytest_collection_finish(session: pytest.Session) -> None:
    if _EXPECTED in session.config.stash and Counter(
        item.nodeid for item in session.items
    ) != Counter(session.config.stash[_EXPECTED]):
        raise pytest.UsageError("Unit CI owner dropped or duplicated unit tests")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    # Combined/local selections get the same refusal before fixture setup.
    if (
        os.getenv("CI_DATABASE_CENSUS")
        or item.get_closest_marker("unit") is None
        or _PATCHES in item.config.stash
    ):
        yield
        return
    _install(item.config)
    try:
        yield
    finally:
        item.config.stash[_PATCHES].undo()
        del item.config.stash[_PATCHES]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> pytest.TestReport | None:
    # Emit a failed report so caught attempts also reach an xdist controller.
    if call.when != "teardown" or item.get_closest_marker("unit") is None:
        return None
    path = item.config.stash[_ATTEMPTS]
    attempts = path.read_text()
    if not attempts:
        return None
    report = pytest.TestReport.from_item_and_call(item, call)
    report.outcome = "failed"
    report.longrepr = "Unit database connection guard failed: " + attempts
    return report


def _verify_execution(config: pytest.Config, directory: Path) -> None:
    manifests = sorted(directory.glob("collection-*.json"))
    if not manifests:
        raise ValueError("Missing unit collection manifests")
    collections = [cast(dict[str, str], json.loads(path.read_text())) for path in manifests]
    classified = collections[0]
    if any(other != classified for other in collections):
        raise ValueError("Unit workers collected different classifications")
    _ = (directory / "collection.json").write_text(json.dumps(classified))
    report = Path(cast(str, config.getoption("xmlpath")))
    cases = list(ET.parse(report).iter("testcase")) if report.exists() else []
    expected = Counter(case_key(nodeid) for nodeid, suite in classified.items() if suite == "unit")
    actual = Counter((case.get("classname", ""), case.get("name", "")) for case in cases)
    if (
        not expected
        or actual != expected
        or any(
            case.find(tag) is not None for case in cases for tag in ("skipped", "error", "failure")
        )
    ):
        raise ValueError("Unit JUnit coverage dropped, duplicated, skipped or failed tests")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    if _DIRECTORY not in config.stash or config.getoption("collectonly"):
        return
    directory = config.stash[_DIRECTORY]
    paths = (
        [config.stash[_ATTEMPTS]]
        if _worker(config) != "main"
        else sorted(directory.glob("connection-attempts*.txt"))
    )
    error = "".join(path.read_text() for path in paths)
    if os.getenv("CI_UNIT_REPORT_DIR") and _worker(config) == "main":
        try:
            _verify_execution(config, directory)
        except (ValueError, ET.ParseError) as failure:
            error += str(failure)
    if error:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = cast(
            TerminalReporter | None, config.pluginmanager.get_plugin("terminalreporter")
        )
        if reporter:
            reporter.write_line("Unit execution guard failed: " + error)


def pytest_unconfigure(config: pytest.Config) -> None:
    if _PATCHES in config.stash:
        config.stash[_PATCHES].undo()
