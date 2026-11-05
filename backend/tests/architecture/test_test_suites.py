"""Failure controls for filename suite ownership and the unit execution boundary.

This module's other-suite cases live in ``test_test_suites_integration.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

import pytest

BACKEND = Path(__file__).parents[2]


def standalone_contracts(tmp_path: Path) -> str:
    """Standalone probes isolate the reviewed behavioral-contract catalogue."""
    path = tmp_path / "contracts.json"
    _ = path.write_text(json.dumps({"contracts": []}))
    return (
        "from scripts import database_contracts\n"
        f"database_contracts.CATALOGUE = Path({str(path)!r})\n"
    )


@pytest.mark.parametrize("mutation", ["none", "missing", "duplicate", "wrong-suite"])
def test_ci_partition_requires_exactly_one_suite_execution(
    tmp_path: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.test_suites import verify_partition  # noqa: PLC0415

    e2e_node = "tests/test_browser_e2e.py::test_browser"
    unit = tmp_path / "unit-tests"
    unit.mkdir()
    _ = (unit / "all-tests.json").write_text(
        json.dumps(
            {
                "collected": {
                    "tests/test_unit.py::test_unit": "unit",
                    "tests/test_db_integration.py::test_db": "integration",
                    e2e_node: "e2e",
                },
                "services": [e2e_node],
            }
        )
    )
    for name in [
        "unit-tests",
        "e2e-tests",
        "pg-locks",
        "storage-tests",
        *[f"pg-suite-{i}" for i in range(1, 5)],
        *[f"pg-schema-{i}" for i in range(1, 3)],
    ]:
        path = tmp_path / name
        path.mkdir(exist_ok=True)
        cases = ""
        if name == "e2e-tests":
            cases = '<testcase classname="tests.test_browser_e2e" name="test_browser"/>'
        if name == "unit-tests":
            cases = '<testcase classname="tests.test_unit" name="test_unit"/>'
        if name == "pg-suite-1" and mutation != "missing":
            cases = '<testcase classname="tests.test_db_integration" name="test_db"/>'
        if name == "pg-suite-2" and mutation == "duplicate":
            cases = '<testcase classname="tests.test_db_integration" name="test_db"/>'
        if name == "pg-suite-2" and mutation == "wrong-suite":
            cases = '<testcase classname="tests.test_unit" name="test_unit"/>'
        _ = (path / "report.xml").write_text(f"<testsuite>{cases}</testsuite>")
    if mutation == "none":
        verify_partition(tmp_path, protected={})
        _ = (tmp_path / "e2e-tests/report.xml").write_text(
            '<testsuite><testcase classname="tests.test_browser_e2e" name="test_browser">'
            "<skipped/></testcase></testsuite>"
        )
        with pytest.raises(ValueError, match="External service contract skipped"):
            verify_partition(tmp_path, protected={})
    else:
        with pytest.raises(ValueError):
            verify_partition(tmp_path, protected={})


def test_affected_partition_omits_only_explicitly_unselected_suite_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.ci_postgres import module_shards, shard_for  # noqa: PLC0415
    from scripts.test_suites import verify_partition  # noqa: PLC0415

    module = "tests/domain/test_selected_integration.py"
    selected = module + "::test_selected"
    omitted = "tests/domain/test_unselected_integration.py::test_unselected"
    unit_case = "tests/test_unit.py::test_unit"
    monkeypatch.setenv("CI_POSTGRES_FILES", json.dumps([module]))
    index = module_shards().get(module, shard_for(module))
    owners = [
        "unit-tests",
        "pg-locks",
        "storage-tests",
        f"pg-suite-{index}",
        "pg-schema-1",
        "pg-schema-2",
    ]
    for name in owners:
        path = tmp_path / name
        path.mkdir()
        cases = ""
        if name == "unit-tests":
            cases = '<testcase classname="tests.test_unit" name="test_unit"/>'
        if name == f"pg-suite-{index}":
            cases = (
                '<testcase classname="tests.domain.test_selected_integration" '
                'name="test_selected"/>'
            )
            _ = (path / "collection-main.json").write_text(
                json.dumps({"collected": [selected, omitted]})
            )
        _ = (path / "report.xml").write_text(f"<testsuite>{cases}</testsuite>")
    _ = (tmp_path / "unit-tests" / "all-tests.json").write_text(
        json.dumps(
            {
                "collected": {unit_case: "unit", selected: "integration", omitted: "integration"},
                "services": [],
            }
        )
    )
    verify_partition(tmp_path, protected={})
    (tmp_path / f"pg-suite-{index}" / "collection-main.json").unlink()
    with pytest.raises(ValueError, match="dropped or duplicated"):
        verify_partition(tmp_path, protected={})


@pytest.mark.parametrize(
    ("nodeid", "suite"),
    [
        ("tests/integration/test_plain.py::TestProbe::test_new[new::parameter]", "unit"),
        ("tests/unit/test_plain_integration.py::test_new", "integration"),
        ("tests/feature/test_plain_e2e.py::test_new", "e2e"),
        ("tests/feature/test_integration_helpers.py::test_new", "unit"),
        ("tests/feature/test_plain.py::test_new[_integration.py]", "unit"),
    ],
)
def test_filename_alone_assigns_every_case(nodeid: str, suite: str) -> None:
    from scripts.test_suites import owner  # noqa: PLC0415

    assert owner(nodeid) == suite


def test_every_duration_key_resolves_to_a_test_file(tmp_path: Path) -> None:
    """Duration weights refer to real modules, independently of suite membership."""
    durations = cast(
        dict[str, dict[str, float]],
        json.loads((BACKEND / "scripts/ci_postgres_durations.json").read_text()),
    )
    assert all((BACKEND / path).is_file() for path in durations["durations"])
    _assert_calendar_stable_query_log_collection(tmp_path)


def _assert_calendar_stable_query_log_collection(tmp_path: Path) -> None:
    module = "tests/db/test_bi_query_log_postgres_integration.py"
    expected: list[str] | None = None
    # Preserve current-month database coverage while crossing a month and year
    # boundary: neither collection identity nor exact suite ownership may drift.
    for day in ("2026-10-31", "2026-11-01", "2027-01-01"):
        collection = tmp_path / f"query-log-{day}.json"
        program = (
            "import datetime as dt, importlib, json, sys\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            "import pytest\n"
            + f"sys.path.insert(0, {str(BACKEND / 'tests/db')!r})\n"
            + "probe = importlib.import_module('test_bi_query_log_postgres_integration')\n"
            + f"fixed = dt.datetime.fromisoformat({day!r}).replace(tzinfo=dt.UTC)\n"
            + "class Clock(dt.datetime):\n"
            + "    @classmethod\n"
            + "    def now(cls, tz=None):\n"
            + "        return fixed if tz is not None else fixed.replace(tzinfo=None)\n"
            + "with patch.object(dt, 'datetime', Clock):\n"
            + "    importlib.reload(probe)\n"
            + "assert probe.MONTH == fixed.date().replace(day=1)\n"
            + "class Collection:\n"
            + "    def pytest_collection_finish(self, session):\n"
            + "        nodes = [item.nodeid for item in session.items]\n"
            + f"        Path({str(collection)!r}).write_text(json.dumps(nodes))\n"
            + f"arguments = ['--collect-only', '-q', {module!r}]\n"
            + "sys.exit(pytest.main(arguments, plugins=[Collection()]))\n"
        )
        environment = {
            **os.environ,
            "PYTHONPATH": str(BACKEND),
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        for name in ("CI_UNIT_REPORT_DIR", "TEST_DATABASE_URL"):
            _ = environment.pop(name, None)
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=BACKEND,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, f"{day}: {result.stdout}{result.stderr}"
        collected = cast(list[str], json.loads(collection.read_text()))
        assert collected, day
        if expected is None:
            expected = sorted(collected)
        assert sorted(collected) == expected, day


@pytest.mark.parametrize(
    ("suite", "body", "passes", "attempts"),
    [
        ("unit", "assert True", True, False),
        ("integration", "assert True", True, False),
        ("unknown", "assert True", False, False),
        ("unit", "__unclassified__", True, False),
        ("unit", "__double__", False, False),
        ("unit", "__deselect__", False, False),
        ("unit", "__duplicate__", False, False),
        ("unit", "__stale__", True, False),
        ("unit", "import pytest; pytest.skip('unexpected')", False, False),
        ("unit", "assert False", False, False),
    ],
)
def test_suite_execution_boundary(
    tmp_path: Path, suite: str, body: str, passes: bool, attempts: bool
) -> None:
    _ = (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\n" + standalone_contracts(tmp_path)
    )
    if body == "__unclassified__":
        body = "assert True"
    arguments: list[str] = []
    suffix = ""
    if body == "__deselect__":
        body = "assert True"
        suffix = "\ndef test_other(): pass\n"
        arguments = ["-k", "test_probe and not test_other"]
    if body == "__duplicate__":
        body = "assert True"
        arguments = ["--keep-duplicates", "test_probe.py"]
    if body == "__stale__":
        body = "assert True"
        suffix = "\ndef test_new_parameter(): pass\n"
    prefix = ""
    if suite == "unit" and passes:
        prefix = (
            "import os, pytest\n"
            "pytestmark = pytest.mark.skipif(os.getenv('TEST_DATABASE_URL') is None, "
            "reason='mixed-module database profile gate')\n"
        )
    if body == "__double__":
        prefix = "import pytest\npytestmark = [pytest.mark.unit, pytest.mark.integration]\n"
        body = "assert True"
    module = "test_probe_integration.py" if suite == "integration" else "test_probe.py"
    _ = (tmp_path / module).write_text(prefix + f"def test_probe():\n    {body}\n" + suffix)
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    _ = environment.pop("CI_UNIT_REPORT_DIR", None)
    _ = environment.pop("TEST_DATABASE_URL", None)
    if suite == "unit":
        environment["CI_UNIT_REPORT_DIR"] = str(tmp_path / "evidence")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--rootdir=.",
            module,
            "-p",
            "scripts.test_suites_plugin",
            "-m",
            suite,
            *arguments,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert (result.returncode == 0) == passes, result.stdout + result.stderr
    logs = (tmp_path / "evidence").glob("connection-attempts-*.txt")
    assert any(path.read_text() for path in logs) == attempts, result.stdout + result.stderr
    if suite == "unit" and passes:
        manifest = tmp_path / "evidence" / "collection.json"
        assert json.loads(manifest.read_text()) == {
            "test_probe.py::test_probe": "unit",
            **({"test_probe.py::test_new_parameter": "unit"} if suffix else {}),
        }


@pytest.mark.parametrize("caught", [False])
@pytest.mark.parametrize("ci", [False, True])
def test_caught_unit_attempt_is_a_failure_in_parallel_combined_runs(
    tmp_path: Path, caught: bool, ci: bool
) -> None:
    _ = (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\n" + standalone_contracts(tmp_path)
    )
    body = "pass"
    if caught:
        body = (
            "import sqlite3\n    try: sqlite3.connect(':memory:')\n    except AssertionError: pass"
        )
    _ = (tmp_path / "test_probe.py").write_text(f"def test_probe():\n    {body}\n")
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    _ = environment.pop("CI_UNIT_REPORT_DIR", None)
    _ = environment.pop("TEST_DATABASE_URL", None)
    arguments = []
    if ci:
        environment["CI_UNIT_REPORT_DIR"] = str(tmp_path / "evidence")
        arguments = ["-m", "unit"]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--rootdir=.",
            "test_probe.py",
            "-p",
            "scripts.test_suites_plugin",
            "-p",
            "xdist.plugin",
            "-n",
            "2",
            *arguments,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert (result.returncode != 0) == caught, result.stdout + result.stderr
    if caught:
        assert "Unit database connection guard failed" in result.stdout + result.stderr


@pytest.mark.parametrize("ignore", ["--ignore", "--ignore-glob", "collect_ignore"])
@pytest.mark.parametrize(
    ("suite", "suffix"), [("unit", ""), ("integration", "_integration"), ("e2e", "_e2e")]
)
def test_suite_only_preserves_pytest_ignore_before_imports(
    tmp_path: Path, suite: str, suffix: str, ignore: str
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    _ = (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths=tests\n")
    ignored = f"tests/test_ignored{suffix}.py"
    conftest = "from pathlib import Path\n" + standalone_contracts(tmp_path)
    arguments: list[str] = []
    if ignore == "collect_ignore":
        conftest += f"collect_ignore = [{ignored!r}]\n"
    elif ignore == "--ignore-glob":
        arguments = ["--ignore-glob=**/test_ignored*.py"]
    else:
        arguments = [f"--ignore={ignored}"]
    _ = (tmp_path / "conftest.py").write_text(conftest)
    _ = (tests / f"test_kept{suffix}.py").write_text("def test_kept(): assert True\n")
    _ = (tmp_path / ignored).write_text(
        "raise RuntimeError('Explicitly ignored module was imported')\n"
    )
    for other in ("", "_integration", "_e2e"):
        if other != suffix:
            _ = (tests / f"test_other{other}.py").write_text(
                "raise RuntimeError('Another suite was imported')\n"
            )
    report = tmp_path / "report.xml"
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    for name in ("CI_UNIT_REPORT_DIR", "CI_E2E_REPORT_DIR", "TEST_DATABASE_URL"):
        _ = environment.pop(name, None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.test_suites_plugin",
            "-q",
            "-m",
            suite,
            "--suite-only",
            f"--junitxml={report}",
            *arguments,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert [
        (case.get("classname"), case.get("name")) for case in ET.parse(report).iter("testcase")
    ] == [(f"tests.test_kept{suffix}", "test_kept")]


@pytest.mark.parametrize("selection", ["full", "filtered", "ignored", "suite-only"])
def test_full_collection_evidence_requires_every_filename_suite(
    tmp_path: Path, selection: str
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    _ = (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths=tests\n")
    _ = (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\n" + standalone_contracts(tmp_path)
    )
    for suffix in ("", "_integration", "_e2e"):
        _ = (tests / f"test_probe{suffix}.py").write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('value', [1, 2], ids=['old::value', 'new::value'])\n"
            "def test_probe(value): assert value > 0\n"
        )
    output = tmp_path / "collection.json"
    arguments = {
        "full": [],
        "filtered": ["-k", "old"],
        "ignored": ["--ignore=tests/test_probe_integration.py"],
        "suite-only": ["-m", "unit", "--suite-only"],
    }[selection]
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    _ = environment.pop("CI_UNIT_REPORT_DIR", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.test_suites_plugin",
            "--collect-only",
            "--collection-output=" + str(output),
            *arguments,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert (result.returncode == 0) == (selection == "full"), result.stdout + result.stderr
    assert output.exists() == (selection == "full")
    if selection == "full":
        assert json.loads(output.read_text()) == {
            "collected": {
                f"tests/test_probe{suffix}.py::test_probe[{value}]": suite
                for suffix, suite in (
                    ("", "unit"),
                    ("_integration", "integration"),
                    ("_e2e", "e2e"),
                )
                for value in ("old::value", "new::value")
            },
            "services": [],
        }
