"""Exercise the pure CI owner's connection and exact-execution guard in fresh processes."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

import pytest
import yaml

from scripts.ci_pure import PURE_MODULES

BACKEND = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("body", "passes", "attempts"),
    [
        ("assert True", True, False),
        ("import pytest; pytest.skip('unexpected skip')", False, False),
        ("assert False", False, False),
        ("import sqlite3; sqlite3.connect(':memory:')", False, True),
        ("import psycopg; psycopg.connect('')", False, True),
        ("import psycopg; psycopg.Connection.connect('')", False, True),
        (
            "from sqlalchemy import create_engine; create_engine('sqlite://').connect()",
            False,
            True,
        ),
        (
            "import sqlite3\n    try: sqlite3.connect(':memory:')\n    except AssertionError: pass",
            False,
            True,
        ),
        (
            "import subprocess, sys; subprocess.run([sys.executable, '-c', "
            "\"import sqlite3; sqlite3.connect(':memory:')\"], env={}, check=False)",
            False,
            True,
        ),
    ],
)
def test_pure_owner_refuses_connections_and_requires_execution(
    tmp_path: Path, body: str, passes: bool, attempts: bool
) -> None:
    (tmp_path / "conftest.py").write_text(
        "from scripts import ci_pure_plugin\n"
        "ci_pure_plugin.PURE_MODULES = frozenset({'test_probe.py'})\n"
    )
    (tmp_path / "test_probe.py").write_text(f"def test_probe():\n    {body}\n")
    evidence = tmp_path / "evidence"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "scripts.ci_pure_plugin", "-q", "test_probe.py"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(BACKEND),
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "CI_PURE_REPORT_DIR": str(evidence),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert (result.returncode == 0) == passes, result.stdout + result.stderr
    assert bool((evidence / "connection-attempts.txt").read_text()) == attempts
    assert json.loads((evidence / "collection.json").read_text()) == ["test_probe.py::test_probe"]


def test_pure_owner_paths_and_job_are_explicit() -> None:
    tasks = cast(
        dict[str, dict[str, str]],
        tomllib.loads((BACKEND / "mise.toml").read_text())["tasks"],
    )
    command = shlex.split(tasks["risk-service:test-pure"]["run"])
    assert frozenset(token for token in command if token.startswith("tests/")) == PURE_MODULES
    assert command[:5] == ["uv", "run", "pytest", "-p", "scripts.ci_pure_plugin"]
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((BACKEND.parent / ".github/workflows/risk-service.yml").read_text()),
    )
    job = document["jobs"]["architecture"]
    steps = cast(list[dict[str, object]], job["steps"])
    pure = next(step for step in steps if step.get("run") == "mise run risk-service:test-pure")
    assert "CI_PURE_REPORT_DIR" in cast(dict[str, str], pure["env"])
    assert not any("setup-postgres" in str(step.get("uses", "")) for step in steps)
    assert job["if"] == "needs.preflight.outputs.guards == 'true'"


@pytest.mark.parametrize("arguments", [["-k", "test_kept"], ["--keep-duplicates", "test_probe.py"]])
def test_pure_owner_rejects_deselected_or_duplicated_tests(
    tmp_path: Path, arguments: list[str]
) -> None:
    (tmp_path / "conftest.py").write_text(
        "from scripts import ci_pure_plugin\n"
        "ci_pure_plugin.PURE_MODULES = frozenset({'test_probe.py'})\n"
    )
    (tmp_path / "test_probe.py").write_text("def test_kept(): pass\ndef test_other(): pass\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.ci_pure_plugin",
            "test_probe.py",
            *arguments,
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(BACKEND),
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "CI_PURE_REPORT_DIR": str(tmp_path / "evidence"),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Pure CI owner must" in result.stdout + result.stderr
