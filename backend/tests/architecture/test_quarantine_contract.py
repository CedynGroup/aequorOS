"""Quarantine contract: a marker without an issue and reason cannot exist.

A quarantine is a temporary, issue-backed exclusion of a known-flaky test from
blocking runs, never a silent skip: the policy lives in CODEBASE_CONVENTIONS.md
§6. These tests pin the marker's shape, the deselection behaviour and the
refusal path, using the real plugin through real pytest subprocesses.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from scripts.test_suites import owner
from scripts.test_suites_plugin import quarantine_problem

BACKEND = Path(__file__).parents[2]

PROBE = """import pytest


def test_plain() -> None:
    assert True


@pytest.mark.quarantine(issue={issue}, reason={reason})
def test_quarantined() -> None:
    assert True
"""


def _pytest_runner(root: Path) -> Callable[[list[str]], subprocess.CompletedProcess[str]]:
    """Run real pytest with the suite plugin, isolated from this session's env."""
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TEST_DATABASE_URL": "postgresql+psycopg://unit:unit@127.0.0.1:1/unit_tests",
    }
    for name in (
        "CI_UNIT_REPORT_DIR",
        "CI_E2E_REPORT_DIR",
        "CI_DATABASE_OBSERVATIONS",
        "CI_POSTGRES_REPORT_DIR",
    ):
        _ = environment.pop(name, None)

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pytest", *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    return run


def test_marker_registered(tmp_path: Path) -> None:
    _ = (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -p scripts.test_suites_plugin\n")
    result = _pytest_runner(tmp_path)(["--markers", "-q"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(
        line.startswith("@pytest.mark.quarantine(issue, reason)")
        for line in result.stdout.splitlines()
    ), result.stdout


def test_blocking_run_deselects_and_quarantine_only_selects(tmp_path: Path) -> None:
    _ = (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -p scripts.test_suites_plugin\n")
    _ = (tmp_path / "test_probe.py").write_text(
        PROBE.format(issue=847, reason='"flake reproduction in flight"')
    )
    run = _pytest_runner(tmp_path)

    blocking = run(["--junitxml", str(tmp_path / "blocking.xml")])
    assert blocking.returncode == 0, blocking.stdout + blocking.stderr
    assert "1 deselected" in blocking.stdout, blocking.stdout
    assert [case.get("name") for case in ET.parse(tmp_path / "blocking.xml").iter("testcase")] == [
        "test_plain"
    ]

    only_quarantined = run(["--quarantine-only", "--junitxml", str(tmp_path / "only.xml")])
    assert only_quarantined.returncode == 0, only_quarantined.stdout + only_quarantined.stderr
    assert [case.get("name") for case in ET.parse(tmp_path / "only.xml").iter("testcase")] == [
        "test_quarantined"
    ]


@pytest.mark.parametrize(
    ("filename", "marker", "complaint"),
    [
        ("test_probe.py", "@pytest.mark.quarantine(reason='scroll race')", "positive int issue"),
        (
            "test_probe.py",
            "@pytest.mark.quarantine(issue=0, reason='scroll race')",
            "positive int issue",
        ),
        (
            "test_probe.py",
            "@pytest.mark.quarantine(issue=-3, reason='scroll race')",
            "positive int issue",
        ),
        ("test_probe.py", "@pytest.mark.quarantine(issue=847)", "non-empty reason"),
        (
            "test_probe.py",
            "@pytest.mark.quarantine(issue=847, reason='   ')",
            "non-empty reason",
        ),
        (
            "test_probe_integration.py",
            "@pytest.mark.database_contract\n"
            "@pytest.mark.quarantine(issue=847, reason='scroll race')",
            "cannot be quarantined",
        ),
    ],
)
def test_invalid_marker_fails_collection_at_every_run(
    tmp_path: Path, filename: str, marker: str, complaint: str
) -> None:
    _ = (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -p scripts.test_suites_plugin\n")
    _ = (tmp_path / filename).write_text(
        "import pytest\n\n\n" + f"{marker}\n" + "def test_quarantined() -> None:\n    assert True\n"
    )
    result = _pytest_runner(tmp_path)(["-q"])
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert complaint in result.stderr, result.stderr


def test_quarantine_problem_decides_every_shape() -> None:
    assert quarantine_problem(847, "scroll race", "unit") is None
    assert quarantine_problem(1, " MinIO stall ", "integration") is None
    assert "positive int issue" in cast(str, quarantine_problem(None, "race", "unit"))
    assert "positive int issue" in cast(str, quarantine_problem(0, "race", "unit"))
    assert "positive int issue" in cast(str, quarantine_problem(-1, "race", "integration"))
    # A bool is not an acceptable issue number even though isinstance(True, int).
    assert "positive int issue" in cast(str, quarantine_problem(True, "race", "unit"))
    assert "non-empty reason" in cast(str, quarantine_problem(847, "", "unit"))
    assert "non-empty reason" in cast(str, quarantine_problem(847, None, "unit"))
    assert "e2e tests cannot be quarantined" in cast(str, quarantine_problem(847, "race", "e2e"))
    assert "cannot be quarantined" in cast(
        str, quarantine_problem(847, "race", "integration", protected=True)
    )
    assert quarantine_problem(847, "scroll race", "integration", protected=False) is None


def _quarantine_marker_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        mark = node.func.value
        if (
            node.func.attr == "quarantine"
            and isinstance(mark, ast.Attribute)
            and mark.attr == "mark"
            and isinstance(mark.value, ast.Name)
            and mark.value.id == "pytest"
        ):
            calls.append(node)
    return calls


def _literal(node: ast.expr) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    raise AssertionError(f"quarantine arguments must be literals, found {ast.dump(node)}")


def test_every_committed_quarantine_marker_is_valid() -> None:
    """The static ratchet beside the runtime refusal in the collection plugin."""
    modules = sorted((BACKEND / "tests").rglob("test_*.py"))
    found = 0
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _quarantine_marker_calls(tree):
            found += 1
            issue: object = None
            reason: object = None
            for index, argument in enumerate(call.args):
                if index == 0:
                    issue = _literal(argument)
                elif index == 1:
                    reason = _literal(argument)
            for keyword in call.keywords:
                if keyword.arg == "issue":
                    issue = _literal(keyword.value)
                elif keyword.arg == "reason":
                    reason = _literal(keyword.value)
            problem = quarantine_problem(issue, reason, owner(str(path)))
            assert problem is None, f"{path}: {problem}"
    assert found >= 0  # the scan stays honest when the quarantine is empty
