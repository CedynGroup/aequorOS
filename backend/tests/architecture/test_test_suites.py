"""Failure controls for explicit suite ownership and the unit execution boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

BACKEND = Path(__file__).parents[2]


def _standalone_contracts(tmp_path: Path) -> str:
    """Standalone ownership probes supply both of their catalogue inputs."""
    path = tmp_path / "contracts.json"
    _ = path.write_text(json.dumps({"contracts": []}))
    return (
        "from scripts import database_contracts\n"
        f"database_contracts.CATALOGUE = Path({str(path)!r})\n"
    )


@pytest.mark.parametrize(
    ("suite", "body", "passes", "attempts"),
    [
        ("unit", "assert True", True, False),
        ("integration", "assert True", True, False),
        ("unknown", "assert True", False, False),
        ("unit", "import sqlite3; sqlite3.connect(':memory:')", False, True),
        ("unit", "import psycopg; psycopg.connect('')", False, True),
        ("unit", "import psycopg; psycopg.Connection.connect('')", False, True),
        (
            "unit",
            "from sqlalchemy import create_engine; create_engine('sqlite://').connect()",
            False,
            True,
        ),
        (
            "unit",
            "from sqlalchemy import create_engine; create_engine('sqlite://').raw_connection()",
            False,
            True,
        ),
        ("unit", "import oracledb; oracledb.connect(dsn='localhost/unused')", False, True),
        ("unit", "__unclassified__", False, False),
        ("unit", "__double__", False, False),
        ("unit", "__deselect__", False, False),
        ("unit", "__duplicate__", False, False),
        ("unit", "__stale__", False, False),
        (
            "unit",
            "import sqlite3\n    try: sqlite3.connect(':memory:')\n    except AssertionError: pass",
            False,
            True,
        ),
        ("unit", "import pytest; pytest.skip('unexpected')", False, False),
        ("unit", "assert False", False, False),
        (
            "unit",
            "import subprocess, sys; subprocess.run([sys.executable, '-c', "
            "\"import sqlite3; sqlite3.connect(':memory:')\"], env={}, check=False)",
            False,
            True,
        ),
    ],
)
def test_suite_execution_boundary(
    tmp_path: Path, suite: str, body: str, passes: bool, attempts: bool
) -> None:
    catalogue = tmp_path / "suites.json"
    _ = catalogue.write_text(
        json.dumps(
            {
                "cases": {"test_probe.py::test_probe": suite, "test_probe.py::test_other": suite}
                if body == "__deselect__"
                else {"test_probe.py::test_probe": suite}
            }
        )
    )
    _ = (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\nfrom scripts import test_suites\n"
        + f"test_suites.CATALOGUE = Path({str(catalogue)!r})\n"
        + "test_suites.catalogue.cache_clear()\n"
        + _standalone_contracts(tmp_path)
    )
    if body == "__unclassified__":
        _ = catalogue.write_text(json.dumps({"cases": {}}))
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
        _ = catalogue.write_text(
            json.dumps(
                {
                    "cases": {
                        "test_probe.py::test_probe": suite,
                        "test_probe.py::test_absent": "unit",
                    },
                }
            )
        )
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
    _ = (tmp_path / "test_probe.py").write_text(
        prefix + f"def test_probe():\n    {body}\n" + suffix
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    _ = environment.pop("CI_UNIT_REPORT_DIR", None)
    _ = environment.pop("CI_DATABASE_CENSUS", None)
    _ = environment.pop("TEST_DATABASE_URL", None)
    if suite == "unit":
        environment["CI_UNIT_REPORT_DIR"] = str(tmp_path / "evidence")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--rootdir=.",
            "test_probe.py",
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
        assert json.loads(manifest.read_text()) == {"test_probe.py::test_probe": "unit"}


@pytest.mark.parametrize("mutation", ["none", "missing", "duplicate", "wrong-suite"])
def test_ci_partition_requires_exactly_one_suite_execution(
    tmp_path: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import test_suites  # noqa: PLC0415
    from scripts.test_suites import verify_partition  # noqa: PLC0415

    e2e_node = "tests/test_browser.py::test_browser"
    catalogue = tmp_path / "suites.json"
    _ = catalogue.write_text(json.dumps({"services": {e2e_node: "e2e"}}))
    monkeypatch.setattr(test_suites, "CATALOGUE", catalogue)
    unit = tmp_path / "unit-tests"
    unit.mkdir()
    _ = (unit / "collection.json").write_text(
        json.dumps(
            {
                "tests/test_unit.py::test_unit": "unit",
                "tests/test_db.py::test_db": "integration",
                e2e_node: "e2e",
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
            cases = '<testcase classname="tests.test_browser" name="test_browser"/>'
        if name == "unit-tests":
            cases = '<testcase classname="tests.test_unit" name="test_unit"/>'
        if name == "pg-suite-1" and mutation != "missing":
            cases = '<testcase classname="tests.test_db" name="test_db"/>'
        if name == "pg-suite-2" and mutation == "duplicate":
            cases = '<testcase classname="tests.test_db" name="test_db"/>'
        if name == "pg-suite-2" and mutation == "wrong-suite":
            cases = '<testcase classname="tests.test_unit" name="test_unit"/>'
        _ = (path / "report.xml").write_text(f"<testsuite>{cases}</testsuite>")
    if mutation == "none":
        verify_partition(tmp_path, protected={})
        _ = (tmp_path / "e2e-tests/report.xml").write_text(
            '<testsuite><testcase classname="tests.test_browser" name="test_browser">'
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

    module = "tests/domain/test_selected.py"
    selected = module + "::test_selected"
    omitted = "tests/domain/test_unselected.py::test_unselected"
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
            cases = '<testcase classname="tests.domain.test_selected" name="test_selected"/>'
            _ = (path / "collection-main.json").write_text(
                json.dumps({"collected": [selected, omitted]})
            )
        _ = (path / "report.xml").write_text(f"<testsuite>{cases}</testsuite>")
    _ = (tmp_path / "unit-tests" / "collection.json").write_text(
        json.dumps({unit_case: "unit", selected: "integration", omitted: "integration"})
    )
    verify_partition(tmp_path, protected={})
    (tmp_path / f"pg-suite-{index}" / "collection-main.json").unlink()
    with pytest.raises(ValueError, match="dropped or duplicated"):
        verify_partition(tmp_path, protected={})


@pytest.mark.parametrize("caught", [False, True])
@pytest.mark.parametrize("ci", [False, True])
def test_caught_unit_attempt_is_a_failure_in_parallel_combined_runs(
    tmp_path: Path, caught: bool, ci: bool
) -> None:
    catalogue = tmp_path / "suites.json"
    _ = catalogue.write_text(json.dumps({"cases": {"test_probe.py::test_probe": "unit"}}))
    _ = (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\nfrom scripts import test_suites\n"
        + f"test_suites.CATALOGUE = Path({str(catalogue)!r})\n"
        + "test_suites.catalogue.cache_clear()\n"
        + _standalone_contracts(tmp_path)
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
    _ = environment.pop("CI_DATABASE_CENSUS", None)
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


def test_mixed_module_overrides_assign_one_suite_at_node_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import test_suites  # noqa: PLC0415

    def classification() -> tuple[dict[str, test_suites.Suite], dict[str, test_suites.Suite]]:
        return {"tests/test_mixed.py": "integration"}, {
            "tests/test_mixed.py::test_db[value::nested]": "integration",
            "tests/test_mixed.py::test_db_helper": "unit",
        }

    monkeypatch.setattr(test_suites, "catalogue", classification)
    assert test_suites.owner("tests/test_mixed.py::test_db[value::nested]") == "integration"
    assert test_suites.owner("tests/test_mixed.py::test_db_helper") == "unit"
    assert not test_suites.unit_modules()
    with pytest.raises(ValueError, match="Unclassified"):
        test_suites.owner("tests/test_new.py::test_new")
    with pytest.raises(ValueError, match="Unclassified"):
        test_suites.owner("tests/test_mixed.py::test_db[new-parameter]")


def test_every_catalogue_key_resolves_to_a_test_file(tmp_path: Path) -> None:
    """A catalogue key whose file no longer exists is stale: the codemod renames keys
    on a move, so anything left over points at a path that should still be there."""
    from scripts import database_contracts  # noqa: PLC0415

    suites = cast(
        "dict[str, object]",
        json.loads((BACKEND / "scripts" / "test_suites.json").read_text(encoding="utf-8")),
    )
    durations_path = BACKEND / "scripts" / "ci_postgres_durations.json"
    durations = cast(
        "dict[str, object]",
        json.loads(durations_path.read_text(encoding="utf-8")),
    )
    sections = cast(
        "tuple[dict[str, str], dict[str, str], dict[str, str]]",
        (suites["cases"], suites["services"], durations["durations"]),
    )
    database_contracts.check_classification(sections[0])
    paths = {node_id.split("::", 1)[0] for section in sections for node_id in section}
    missing = sorted(path for path in paths if not (BACKEND / path).is_file())
    assert missing == []
    _assert_calendar_stable_query_log_collection(tmp_path, sections[0])


def _assert_calendar_stable_query_log_collection(tmp_path: Path, cases: dict[str, str]) -> None:
    module = "tests/db/test_bi_query_log_postgres.py"
    expected = sorted(node for node in cases if node.startswith(module + "::"))
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
            + "probe = importlib.import_module('test_bi_query_log_postgres')\n"
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
        for name in ("CI_UNIT_REPORT_DIR", "CI_DATABASE_CENSUS", "TEST_DATABASE_URL"):
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
        assert sorted(collected) == expected, day
