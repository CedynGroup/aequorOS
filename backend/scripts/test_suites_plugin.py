"""Classify before selection and refuse database access throughout unit execution."""

from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest
from _pytest.fixtures import FixtureDef, SubRequest
from _pytest.python import PyCollector
from _pytest.terminal import TerminalReporter

from scripts.ci_database_children import python_path
from scripts.ci_no_database import install
from scripts.test_suites import (
    case_key,
    catalogue,
    owner,
    selected_modules,
    verify_module_inventory,
)

_PATCHES = pytest.StashKey[pytest.MonkeyPatch]()
_DIRECTORY = pytest.StashKey[Path]()
_ATTEMPTS = pytest.StashKey[Path]()
_EXPECTED = pytest.StashKey[list[str]]()
_FIXTURE_PATCHES = pytest.StashKey[pytest.MonkeyPatch]()
_ATTEMPT_START = pytest.StashKey[int]()
_COLLECTION_OWNERS = pytest.StashKey[dict[str, set[str]]]()


def _worker(config: pytest.Config) -> str:
    worker = cast(dict[str, object] | None, getattr(config, "workerinput", None))
    return cast(str, worker["workerid"]) if worker is not None else "main"


def pytest_report_header(config: pytest.Config) -> str | None:
    if config.getoption("markexpr") != "unit":
        return None
    workers = cast(object, config.getoption("numprocesses", default=0))
    return f"Unit workers: {workers}; logical CPUs: {os.cpu_count()}"


def _install(config: pytest.Config) -> None:
    directory = config.stash.get(_DIRECTORY, None)
    if directory is None:
        directory = Path(os.getenv("CI_UNIT_REPORT_DIR") or tempfile.mkdtemp(prefix="unit-tests-"))
    directory.mkdir(parents=True, exist_ok=True)
    attempts = directory / f"connection-attempts-{os.getpid()}.txt"
    if _ATTEMPTS not in config.stash:
        _ = attempts.write_text("")
    patches = pytest.MonkeyPatch()
    patches.setenv("CI_NO_DATABASE_ATTEMPTS", str(attempts.resolve()))
    # Mixed migration modules gate even model-only checks on this setting.
    # Enable their collection with a refused sentinel, never a live endpoint.
    patches.setenv("TEST_DATABASE_URL", "postgresql+psycopg://unit:unit@127.0.0.1:1/unit_tests")
    patches.setenv("PYTHONPATH", python_path(os.environ.get("PYTHONPATH", "")))
    install(patches.setattr)
    config.stash[_PATCHES] = patches
    config.stash[_DIRECTORY] = directory
    config.stash[_ATTEMPTS] = attempts


@contextmanager
def _fixture_guard(config: pytest.Config, enabled: bool) -> Generator[None]:
    active = _PATCHES in config.stash
    if enabled == active:
        yield
        return
    if active:
        config.stash[_PATCHES].undo()
        del config.stash[_PATCHES]
    else:
        _install(config)
    try:
        yield
    finally:
        if active:
            _install(config)
        else:
            config.stash[_PATCHES].undo()
            del config.stash[_PATCHES]


def _guard_fixture_finalization(config: pytest.Config) -> None:
    unit_fixtures: set[FixtureDef[object]] = set()
    execute = cast(Callable[..., object], FixtureDef.execute)
    finish = cast(Callable[..., None], FixtureDef.finish)

    def guarded_execute(fixture: FixtureDef[object], request: SubRequest) -> object:
        try:
            return execute(fixture, request)
        finally:
            # A shared fixture's request.node is its scope, not its consumer.
            item = request._pyfuncitem  # pyright: ignore[reportPrivateUsage]
            if item.get_closest_marker("unit") is not None:
                unit_fixtures.add(fixture)

    def guarded_finish(fixture: FixtureDef[object], request: SubRequest) -> None:
        try:
            with _fixture_guard(config, fixture in unit_fixtures):
                finish(fixture, request)
        finally:
            unit_fixtures.discard(fixture)

    patches = pytest.MonkeyPatch()
    patches.setattr(FixtureDef, "execute", guarded_execute)
    patches.setattr(FixtureDef, "finish", guarded_finish)
    config.stash[_FIXTURE_PATCHES] = patches


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--suite-only",
        action="store_true",
        help="Collect only modules containing the selected generated suite",
    )


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_make_scheduler(config: pytest.Config, log: object) -> object | None:
    if config.getoption("markexpr") != "unit" or config.getoption("dist") != "load":
        return None
    from scripts.ci_unit_scheduler import scheduler  # noqa: PLC0415

    return scheduler(config, log)


def pytest_load_initial_conftests(early_config: pytest.Config, args: list[str]) -> None:
    if (
        os.getenv("CI_DATABASE_CENSUS") != "1"
        and cast(object, early_config.known_args_namespace.markexpr) == "unit"
    ):
        _install(early_config)
    if getattr(early_config.known_args_namespace, "suite_only", False):
        suite = cast(str, early_config.known_args_namespace.markexpr)
        if suite not in {"unit", "integration", "e2e"}:
            raise pytest.UsageError("--suite-only requires exactly one generated suite selector")
        if cast(list[str], early_config.known_args_namespace.file_or_dir):
            raise pytest.UsageError("--suite-only owns its generated module selection")
        args.extend(selected_modules(suite))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: database-free test; database connections are refused")
    config.addinivalue_line("markers", "integration: test requiring a database or external service")
    config.addinivalue_line("markers", "e2e: backend contract requiring Playwright")
    config.addinivalue_line(
        "markers", "requires_service(name): declared external service dependency"
    )
    e2e_directory = os.getenv("CI_E2E_REPORT_DIR")
    if e2e_directory:
        if config.getoption("markexpr") != "e2e":
            raise pytest.UsageError("E2E CI owner requires the e2e selector")
        config.stash[_DIRECTORY] = Path(e2e_directory)
        config.stash[_DIRECTORY].mkdir(parents=True, exist_ok=True)
    if os.getenv("CI_DATABASE_CENSUS") != "1":
        _guard_fixture_finalization(config)
    if os.getenv("CI_UNIT_REPORT_DIR") and not config.getoption("collectonly"):
        if config.getoption("markexpr") != "unit" or _DIRECTORY not in config.stash:
            raise pytest.UsageError("Unit CI owner requires the unit selector and connection guard")
        # xdist sets workerinput after initial conftest loading. Clean once on
        # the controller here, before workers start, preserving early attempts.
        if _worker(config) == "main":
            directory = config.stash[_DIRECTORY]
            for pattern in ("connection-attempts*.txt", "collection*.json", "collected*.json"):
                for stale in directory.glob(pattern):
                    if stale != config.stash[_ATTEMPTS]:
                        stale.unlink()
        if not cast(object, config.getoption("xmlpath")):
            config.option.xmlpath = str(config.stash[_DIRECTORY] / "report.xml")


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makeitem(
    collector: PyCollector, name: str, obj: object
) -> list[pytest.Item] | None:
    if not collector.config.getoption("suite_only"):
        return None
    if not (collector.istestfunction(obj, name) or collector.istestclass(obj, name)):
        return None
    prefix = f"{collector.nodeid}::{name}"
    if _COLLECTION_OWNERS not in collector.config.stash:
        prefixes: dict[str, set[str]] = {}
        for nodeid, suite in catalogue()[1].items():
            names = nodeid.partition("[")[0].split("::")
            for length in range(2, len(names) + 1):
                prefixes.setdefault("::".join(names[:length]), set()).add(suite)
        collector.config.stash[_COLLECTION_OWNERS] = prefixes
    owners = collector.config.stash[_COLLECTION_OWNERS].get(prefix, set())
    if not owners:
        # Pytest first considers imported classes such as TestClient, then
        # rejects their constructors. Let it decide whether an unknown class
        # is collectible; any actual new test method is checked below it.
        if collector.istestclass(obj, name):
            return None
        raise pytest.UsageError(f"Unclassified test: {prefix}; refresh the census")
    return [] if collector.config.getoption("markexpr") not in owners else None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("CI_DATABASE_CENSUS") == "1":
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
            mark.name for mark in item.iter_markers() if mark.name in ("unit", "integration", "e2e")
        ]
        if markers and markers != [suite]:
            raise pytest.UsageError(f"Double-classified or conflicting suite: {item.nodeid}")
        if not markers:
            item.add_marker(suite)
        classified[item.nodeid] = suite
    if (os.getenv("CI_UNIT_REPORT_DIR") or os.getenv("CI_E2E_REPORT_DIR")) and not config.getoption(
        "collectonly"
    ):
        modules, overrides = catalogue()
        actual_modules = {nodeid.split("::")[0] for nodeid in nodeids}
        suite = cast(str, config.getoption("markexpr"))
        expected_modules = (
            set(selected_modules(suite)) if config.getoption("suite_only") else set(modules)
        )
        if expected_modules != actual_modules:
            raise pytest.UsageError("CI must collect every module containing its selected suite")
        if {nodeid for nodeid, assigned in overrides.items() if assigned == suite} != {
            nodeid for nodeid, assigned in classified.items() if assigned == suite
        }:
            raise pytest.UsageError("Stale or incomplete generated test suite catalogue")
        paths = cast(list[str], config.getini("testpaths")) or ["."]
        try:
            verify_module_inventory(
                config.rootpath, paths, cast(list[str], config.getini("python_files"))
            )
        except ValueError as error:
            raise pytest.UsageError(str(error)) from error
        config.stash[_EXPECTED] = [
            nodeid for nodeid, assigned in overrides.items() if assigned == suite
        ]
        path = config.stash[_DIRECTORY] / f"collection-{_worker(config)}.json"
        _ = path.write_text(json.dumps(overrides))
        _ = (config.stash[_DIRECTORY] / f"collected-{_worker(config)}.json").write_text(
            json.dumps(classified)
        )


def pytest_collection_finish(session: pytest.Session) -> None:
    if _EXPECTED in session.config.stash and Counter(
        item.nodeid for item in session.items
    ) != Counter(session.config.stash[_EXPECTED]):
        raise pytest.UsageError("CI owner dropped or duplicated selected tests")


@contextmanager
def _managed_storage_environment(item: pytest.Item) -> Generator[None]:
    # MinIO is provisioned for storage/schema contracts in CI. Its HTTP endpoint
    # must not become ambient application configuration for production-TLS or
    # mocked AWS provisioning tests elsewhere in the tree.
    patches = pytest.MonkeyPatch()
    managed = os.getenv("CI_DATABASE_CENSUS") == "1" or bool(os.getenv("CI_UNIT_REPORT_DIR"))
    if managed and not item.nodeid.startswith(("tests/storage/", "tests/db/")):
        patches.delenv("S3_ENDPOINT", raising=False)
    try:
        yield
    finally:
        patches.undo()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    # Combined/local selections get the same refusal before fixture setup.
    with _managed_storage_environment(item):
        path = item.config.stash.get(_ATTEMPTS, None)
        item.stash[_ATTEMPT_START] = path.stat().st_size if path is not None else 0
        if (
            os.getenv("CI_DATABASE_CENSUS") == "1"
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
    if call.when != "teardown" or _ATTEMPTS not in item.config.stash:
        return None
    path = item.config.stash[_ATTEMPTS]
    attempts = path.read_text()[item.stash.get(_ATTEMPT_START, 0) :]
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
    suite = cast(str, config.getoption("markexpr"))
    expected = Counter(
        case_key(nodeid) for nodeid, assigned in classified.items() if assigned == suite
    )
    actual = Counter((case.get("classname", ""), case.get("name", "")) for case in cases)
    if (
        not expected
        or actual != expected
        or any(
            case.find(tag) is not None for case in cases for tag in ("skipped", "error", "failure")
        )
    ):
        raise ValueError("Unit JUnit coverage dropped, duplicated, skipped or failed tests")
    observations = os.getenv("CI_DATABASE_OBSERVATIONS")
    if observations and Path(observations).resolve().is_relative_to(directory.resolve()):
        from scripts.test_suite_census import check  # noqa: PLC0415

        check(Path(observations))


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    if _DIRECTORY not in config.stash or config.getoption("collectonly"):
        return
    directory = config.stash[_DIRECTORY]
    paths = (
        []
        if _ATTEMPTS not in config.stash
        else (
            [config.stash[_ATTEMPTS]]
            if _worker(config) != "main"
            else sorted(directory.glob("connection-attempts*.txt"))
        )
    )
    error = "".join(path.read_text() for path in paths)
    if (os.getenv("CI_UNIT_REPORT_DIR") or os.getenv("CI_E2E_REPORT_DIR")) and _worker(
        config
    ) == "main":
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
    if _FIXTURE_PATCHES in config.stash:
        config.stash[_FIXTURE_PATCHES].undo()
