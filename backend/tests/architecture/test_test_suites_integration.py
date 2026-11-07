"""Failure controls for filename suite ownership and the unit execution boundary.

This module's other-suite cases live in ``test_test_suites.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.architecture import test_ci_postgres as ci_commands
from tests.architecture.test_test_suites import BACKEND, standalone_contracts

selection_repo = ci_commands.selection_repo
task_checkout = ci_commands.task_checkout


@pytest.mark.parametrize("last_failed", [False, True])
def test_public_unit_command_refuses_database_access_before_module_import(
    task_checkout: tuple[Path, dict[str, str], Path], last_failed: bool
) -> None:
    root, environment, _trace = task_checkout
    mise = shutil.which("mise")
    assert mise is not None
    command = [mise, "run", "risk-service:test"]
    unit = root / "backend/tests/credit/domain/test_unit_probe.py"
    if last_failed:
        unit.write_text(unit.read_text().replace("assert True", "assert False"))
        seeded = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert seeded.returncode == 1, seeded.stdout + seeded.stderr
        command.append("--lf")
    unit.write_text(
        "import sqlite3\nsqlite3.connect('forbidden.db')\ndef test_unit_probe():\n    assert True\n"
    )
    result = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Database connections are forbidden in unit tests" in result.stdout + result.stderr
    assert not (root / "backend/forbidden.db").exists()


@pytest.mark.parametrize(
    ("suite", "body", "passes", "attempts"),
    [
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
        (
            "unit",
            "import sqlite3\n    try: sqlite3.connect(':memory:')\n    except AssertionError: pass",
            False,
            True,
        ),
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


@pytest.mark.parametrize("caught", [True])
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
