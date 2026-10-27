"""Behavioral controls for evidence-based suite ownership."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from scripts.test_suite_census import check, classify, generate

BACKEND = Path(__file__).parents[2]


def test_census_attributes_shared_setup_teardown_and_fresh_environment_children(
    tmp_path: Path,
) -> None:
    # Exercise the same SQLite entry point in the parent as well as in the
    # child so this observer regression is itself database-using evidence.
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    (tmp_path / "conftest.py").write_text(
        "import pytest, sqlite3\n"
        "@pytest.fixture(scope='session')\n"
        "def shared():\n    connection = sqlite3.connect(':memory:')\n"
        "    yield connection\n    connection.close()\n"
        "@pytest.fixture(scope='module')\n"
        "def finalizer_only():\n    yield\n    sqlite3.connect(':memory:').close()\n"
    )
    (tmp_path / "test_probe.py").write_text(
        "import subprocess, sys, pytest\n"
        "def test_a(shared): assert shared.execute('SELECT 2').fetchone() == (2,)\n"
        "def test_b(shared): assert shared.execute('SELECT 3').fetchone() == (3,)\n"
        "def test_c(finalizer_only): pass\n"
        "def test_d(finalizer_only): pass\n"
        "def test_child():\n"
        "    subprocess.run([sys.executable, '-c',\n"
        "        \"import sqlite3; sqlite3.connect(':memory:')\"], env={}, check=True)\n"
        "@pytest.mark.parametrize('number', [1, 2])\n"
        "def test_pure(number): assert number > 0\n"
        "@pytest.fixture(scope='module', params=[0, 1])\n"
        "def conditional(request):\n    yield request.param\n"
        "    if request.param == 0: __import__('sqlite3').connect(':memory:').close()\n"
        "def test_transition(conditional): assert conditional in (0, 1)\n"
        "def test_skipped(): pytest.skip('inapplicable profile')\n"
    )
    evidence = tmp_path / "evidence"
    # Reset the nested observation context inside the probe process. Its
    # explicit temporary root prevents repository fixture/catalogue leakage.
    program = (
        "import os,sys; "
        f"os.environ['CI_DATABASE_OBSERVATIONS']={str(evidence)!r}; "
        "os.environ['CI_DATABASE_CONTEXT']='{}'; "
        "os.environ.pop('CI_UNIT_REPORT_DIR',None); "
        "os.environ.pop('CI_DATABASE_CENSUS',None); "
        "import pytest; sys.exit(pytest.main(['--rootdir=.', '-p', "
        "'scripts.database_observation_plugin', 'test_probe.py']))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(BACKEND),
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cases, inconclusive = classify(evidence)
    assert cases == {
        **{
            f"test_probe.py::test_{name}": "integration"
            for name in ("a", "b", "c", "d", "child", "skipped")
        },
        "test_probe.py::test_pure[1]": "unit",
        "test_probe.py::test_pure[2]": "unit",
        "test_probe.py::test_transition[0]": "integration",
        "test_probe.py::test_transition[1]": "unit",
    }
    assert set(inconclusive) == {"test_probe.py::test_skipped"}
    catalogue = tmp_path / "suites.json"
    generate(evidence, catalogue)
    check(evidence, catalogue)
    raw = cast(dict[str, dict[str, str]], json.loads(catalogue.read_text()))
    raw["cases"]["test_probe.py::test_pure[1]"] = "integration"
    catalogue.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="ownership drift"):
        check(evidence, catalogue)
    raw["cases"]["test_probe.py::test_pure[1]"] = "unit"
    raw["cases"]["test_probe.py::test_a"] = "unit"
    catalogue.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="ownership drift"):
        check(evidence, catalogue)

    isolated = tmp_path / "isolated_guard.py"
    _ = isolated.write_text(
        "import os, subprocess, sys\n"
        + f"os.environ['CI_NO_DATABASE_ATTEMPTS']={str(tmp_path / 'isolated-attempts.txt')!r}\n"
        + "from scripts.ci_no_database import install\ninstall()\n"
        + "result = subprocess.run([sys.executable, '-I', '-S', '-c', "
        + "\"import sqlite3; sqlite3.connect(':memory:')\"], "
        + "env={}, capture_output=True, text=True)\n"
        + "assert result.returncode != 0, 'isolated child bypassed refusal'\n"
        + "assert 'Database connections are forbidden' in result.stderr, result.stderr\n"
    )
    isolated_result = subprocess.run(
        [sys.executable, str(isolated)],
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert isolated_result.returncode == 0, isolated_result.stdout + isolated_result.stderr
    assert (tmp_path / "isolated-attempts.txt").read_text()
    jobs = tmp_path / "jobs"
    for job, database in (("unit", False), ("integration", True)):
        job_dir = jobs / job
        job_dir.mkdir(parents=True)
        node = f"test_{job}.py::test_case"
        events: list[dict[str, object]] = [
            {"kind": "session_start", "pid": 1},
            {"kind": "selected", "pid": 1, "nodeids": [node]},
            {"kind": "consumes", "pid": 1, "nodeid": node, "fixtures": ["same-fixture"]},
            *[
                {"kind": "report", "pid": 1, "nodeid": node, "when": when, "outcome": "passed"}
                for when in ("setup", "call", "teardown")
            ],
            {"kind": "session_finish", "pid": 1},
        ]
        if database:
            events.append({"kind": "database", "pid": 1, "fixtures": ["same-fixture"]})
        _ = (job_dir / "events-1.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n"
        )
    assert classify(jobs)[0] == {
        "test_unit.py::test_case": "unit",
        "test_integration.py::test_case": "integration",
    }
    supplement = tmp_path / "supplement"
    supplement.mkdir()
    node = "test_probe.py::test_skipped"
    events = [
        {"kind": "session_start", "pid": 1},
        {"kind": "selected", "pid": 1, "nodeids": [node]},
        *[
            {"kind": "report", "pid": 1, "nodeid": node, "when": when, "outcome": "passed"}
            for when in ("setup", "call", "teardown")
        ],
        {"kind": "session_finish", "pid": 1},
    ]
    _ = (supplement / "events-1.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n"
    )
    generate(evidence, catalogue, supplement)
    assert json.loads(catalogue.read_text())["cases"][node] == "unit"


@pytest.mark.parametrize(
    "mutation", ["missing-session", "missing-report", "duplicate", "orphan", "child"]
)
def test_census_rejects_incomplete_or_ambiguous_evidence(tmp_path: Path, mutation: str) -> None:
    nodeid = "tests/test_probe.py::test_probe"
    events: list[dict[str, object]] = [
        {"kind": "session_start", "pid": 1},
        {"kind": "selected", "pid": 1, "nodeids": [nodeid]},
        *[
            {"kind": "report", "pid": 1, "nodeid": nodeid, "when": when, "outcome": "passed"}
            for when in ("setup", "call", "teardown")
        ],
        {"kind": "session_finish", "pid": 1},
    ]
    if mutation == "missing-session":
        events.pop()
    elif mutation == "missing-report":
        events = [e for e in events if e.get("when") != "call"]
    elif mutation == "duplicate":
        events.append(events[2])
    elif mutation == "orphan":
        events.append({"kind": "database", "pid": 1})
    else:
        events.append({"kind": "python_child", "pid": 1, "nodeid": nodeid, "child": 2})
    (tmp_path / "events-1.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    if mutation == "child":
        cases, inconclusive = classify(tmp_path)
        assert cases == {nodeid: "integration"}
        assert set(inconclusive) == {nodeid}
    else:
        with pytest.raises(ValueError):
            classify(tmp_path)
