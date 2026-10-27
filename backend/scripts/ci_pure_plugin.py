"""Fail closed on missing, skipped, duplicated or database-using pure CI tests."""

from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import cast

import pytest
from _pytest.terminal import TerminalReporter

from scripts.ci_no_database import install
from scripts.ci_postgres import case_key
from scripts.ci_pure import PURE_MODULES

_PATCHES = pytest.StashKey[pytest.MonkeyPatch]()
_DIRECTORY = pytest.StashKey[Path]()
_COLLECTED = pytest.StashKey[list[str]]()


def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    _install_guard(early_config)


def _install_guard(config: pytest.Config) -> None:
    directory = Path(os.environ.get("CI_PURE_REPORT_DIR") or tempfile.mkdtemp(prefix="ci-pure-"))
    directory.mkdir(parents=True, exist_ok=True)
    attempts = directory / "connection-attempts.txt"
    attempts.write_text("")
    patches = pytest.MonkeyPatch()
    patches.setenv("CI_NO_DATABASE_ATTEMPTS", str(attempts.resolve()))
    install(patches.setattr)
    config.stash[_PATCHES] = patches
    config.stash[_DIRECTORY] = directory


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    if _DIRECTORY not in config.stash:
        _install_guard(config)
    directory = config.stash[_DIRECTORY]
    if not cast(object, config.getoption("xmlpath")):
        config.option.xmlpath = str(directory / "report.xml")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    collected = [item.nodeid for item in items]
    modules = frozenset(nodeid.split("::")[0] for nodeid in collected)
    if modules != PURE_MODULES or len(set(collected)) != len(collected):
        raise pytest.UsageError("Pure CI owner must collect every owned module exactly once")
    config.stash[_COLLECTED] = collected
    (config.stash[_DIRECTORY] / "collection.json").write_text(json.dumps(collected))


def pytest_collection_finish(session: pytest.Session) -> None:
    if Counter(item.nodeid for item in session.items) != Counter(
        session.config.stash.get(_COLLECTED, list[str]())
    ):
        raise pytest.UsageError("Pure CI owner must not deselect owned tests")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    directory = config.stash[_DIRECTORY]
    attempts = (directory / "connection-attempts.txt").read_text()
    report = Path(cast(str, config.getoption("xmlpath")))
    cases = list(ET.parse(report).iter("testcase")) if report.exists() else []
    expected = Counter(case_key(nodeid) for nodeid in config.stash.get(_COLLECTED, list[str]()))
    actual = Counter((case.get("classname", ""), case.get("name", "")) for case in cases)
    if (
        attempts
        or not expected
        or actual != expected
        or any(
            case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")
        )
    ):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = cast(
            TerminalReporter | None, config.pluginmanager.get_plugin("terminalreporter")
        )
        if reporter:
            reporter.write_line("Pure CI execution guard failed: " + (attempts or "JUnit coverage"))


def pytest_unconfigure(config: pytest.Config) -> None:
    if _PATCHES in config.stash:
        config.stash[_PATCHES].undo()
