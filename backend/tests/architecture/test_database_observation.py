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
    _ = (tmp_path / "pytest.ini").write_text("[pytest]\n")
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
    raw["cases"].update({"test_probe.py::test_pure[1]": "unit", "test_probe.py::test_a": "unit"})
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
    _assert_normal_child_refusals(tmp_path)
    _assert_managed_service_scope(tmp_path)
    _assert_combined_fixture_ownership(tmp_path)
    _assert_failed_fixture_consumers(tmp_path)
    _assert_deferred_thread_ownership(tmp_path)
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


def _assert_deferred_thread_ownership(tmp_path: Path) -> None:
    # One worker runs during the next test, the other after pytest clears its
    # context. Both must retain their origin, including their Python children.
    root = tmp_path / "deferred-threads"
    root.mkdir()
    _ = (root / "pytest.ini").write_text("[pytest]\n")
    _ = (root / "test_probe.py").write_text(
        "import sqlite3, subprocess, sys\n"
        "from threading import Event, Thread\n"
        "class Deferred(Thread):\n"
        "    def __init__(self):\n"
        "        super().__init__(); self.release = Event(); self.complete = Event()\n"
        "    def run(self):\n"
        "        assert self.release.wait(30)\n"
        "        sqlite3.connect(':memory:').close()\n"
        "        subprocess.run([sys.executable, '-c',\n"
        "            \"import sqlite3; sqlite3.connect(':memory:').close()\"],\n"
        "            env={}, check=True)\n"
        "        self.complete.set()\n"
        "workers = [Deferred(), Deferred()]\n"
        "def test_origin():\n"
        "    for worker in workers: worker.start()\n"
        "def test_unrelated():\n"
        "    workers[0].release.set()\n"
        "    workers[0].join(20)\n"
        "    assert workers[0].complete.is_set()\n"
    )
    evidence = root / "evidence"
    program = (
        "import os,sys; "
        + f"os.environ['CI_DATABASE_OBSERVATIONS']={str(evidence)!r}; "
        + "os.environ['CI_DATABASE_CONTEXT']='{}'; "
        + "os.environ.pop('CI_UNIT_REPORT_DIR',None); "
        + "os.environ.pop('CI_DATABASE_CENSUS',None); "
        + "import pytest; result=pytest.main(['--rootdir=.', '-p', "
        + "'scripts.database_observation_plugin', 'test_probe.py']); "
        + "import test_probe; worker=test_probe.workers[1]; worker.release.set(); "
        + "worker.join(20); assert worker.complete.is_set(); sys.exit(result)"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=root,
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
    assert classify(evidence) == (
        {
            "test_probe.py::test_origin": "integration",
            "test_probe.py::test_unrelated": "unit",
        },
        {},
    )


def _assert_failed_fixture_consumers(tmp_path: Path) -> None:
    profiles: dict[str, Path] = {}
    scopes = ("function", "class", "module", "package", "session")
    for mode in ("unit", "connect", "refused", "finalizer"):
        root = tmp_path / f"failed-fixtures-{mode}"
        root.mkdir()
        _ = (root / "pytest.ini").write_text("[pytest]\n")
        for scope in scopes:
            _ = (root / f"test_{scope}.py").write_text(
                "import os, pytest, sqlite3\nfrom pathlib import Path\n"
                + f"@pytest.fixture(scope={scope!r})\n"
                + "def failing(request):\n"
                + "    mode = os.environ['FIXTURE_PROFILE']\n"
                + "    if mode == 'unit': return\n"
                + f"    with Path({str(root / ('setups-' + scope))!r}).open('a') as log:\n"
                + "        log.write('setup\\n')\n"
                + "    if mode == 'finalizer':\n"
                + "        request.addfinalizer(lambda: sqlite3.connect(':memory:').close())\n"
                + "    elif mode == 'refused':\n"
                + f"        sqlite3.connect({str(root / 'missing/database.sqlite')!r})\n"
                + "    else: sqlite3.connect(':memory:').close()\n"
                + "    raise RuntimeError('fixture setup failed')\n"
                + f"@pytest.fixture(scope={scope!r})\n"
                + "def middle(failing): return failing\n"
                + f"@pytest.fixture(scope={scope!r})\n"
                + "def outer(middle): return middle\n"
                + "class TestProbe:\n"
                + "    def test_first(self, failing): pass\n"
                + "    def test_nested(self, outer): pass\n"
                + "    def test_dynamic(self, request): request.getfixturevalue('outer')\n"
            )
        observations = root / "observations"
        program = (
            "import os,sys; "
            + f"os.environ['CI_DATABASE_OBSERVATIONS']={str(observations)!r}; "
            + "os.environ['CI_DATABASE_CONTEXT']='{}'; "
            + f"os.environ['FIXTURE_PROFILE']={mode!r}; "
            + "os.environ.pop('CI_UNIT_REPORT_DIR',None); "
            + "os.environ.pop('CI_DATABASE_CENSUS',None); "
            + "import pytest; sys.exit(pytest.main(['--rootdir=.', '-p', "
            + "'scripts.database_observation_plugin', '.']))"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=root,
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
        assert result.returncode == (0 if mode == "unit" else 1), result.stdout + result.stderr
        nodes = [
            f"test_{scope}.py::TestProbe::test_{name}"
            for scope in scopes
            for name in ("first", "nested", "dynamic")
        ]
        cases, inconclusive = classify(observations)
        assert cases == dict.fromkeys(nodes, "unit" if mode == "unit" else "integration")
        assert inconclusive == {}
        if mode != "unit":
            for scope in scopes:
                count = len((root / f"setups-{scope}").read_text().splitlines())
                assert count == (3 if scope == "function" else 1)
        profiles[mode] = observations
    output = tmp_path / "failed-fixtures-catalogue.json"
    generate(profiles["unit"], output)
    for mode in ("connect", "refused", "finalizer"):
        with pytest.raises(ValueError, match="ownership drift"):
            check(profiles[mode], output)
        for full, supplement in (
            (profiles[mode], profiles["unit"]),
            (profiles["unit"], profiles[mode]),
        ):
            with pytest.raises(ValueError, match="Conflicting database evidence"):
                generate(full, output, supplement)


def _assert_combined_fixture_ownership(tmp_path: Path) -> None:
    for scenario in (
        "integration",
        "unit",
        "unit-caught",
        "unit-setup",
        "unit-setup-caught",
        "shared",
    ):
        root = tmp_path / scenario
        tests = root / "tests"
        tests.mkdir(parents=True)
        _ = (root / "pytest.ini").write_text("[pytest]\n")
        _ = (tests / "__init__.py").write_text("")
        cases: dict[str, str] = {}
        for scope in ("function", "class", "module", "package", "session"):
            path = tests / f"test_{scope}.py"
            marker = root / f"cleaned-{scope}"
            cleanup = "sqlite3.connect(':memory:').close()"
            if scenario.endswith("caught") or scenario == "shared":
                cleanup = "try: " + cleanup + "\n    except AssertionError: return"
            integration = "    def test_integration(self, resource): pass\n"
            unit = "    def test_unit(self): pass\n"
            if scenario == "shared":
                unit = "    def test_unit(self, request): request.getfixturevalue('resource')\n"
            elif scenario.startswith("unit"):
                integration = "    def test_integration(self): pass\n"
                unit = "    def test_unit(self, resource): pass\n"
            methods = unit + integration if scenario.startswith("unit") else integration + unit
            resource = "    yield\n    " + cleanup + f"\n    Path({str(marker)!r}).touch()\n"
            if scenario.startswith("unit-setup"):
                resource = "    sqlite3.connect(':memory:').close()\n    yield\n"
                if scenario.endswith("caught"):
                    resource = (
                        "    try: sqlite3.connect(':memory:').close()\n"
                        "    except AssertionError: pass\n    yield\n"
                    )
            _ = path.write_text(
                "import pytest, sqlite3\nfrom pathlib import Path\n"
                + f"@pytest.fixture(scope={scope!r})\n"
                + "def resource():\n"
                + resource
                + "class TestProbe:\n"
                + methods
            )
            for suite in ("unit", "integration"):
                cases[f"tests/test_{scope}.py::TestProbe::test_{suite}"] = suite
        if scenario == "integration":
            _ = (tests / "test_transition.py").write_text(
                "import pytest, sqlite3\n"
                "@pytest.fixture(scope='module', params=[0, 1])\n"
                "def resource(request):\n    yield request.param\n"
                "    if request.param == 0: sqlite3.connect(':memory:').close()\n"
                "def test_transition(resource): assert resource in (0, 1)\n"
            )
            cases.update(
                {
                    "tests/test_transition.py::test_transition[0]": "integration",
                    "tests/test_transition.py::test_transition[1]": "unit",
                }
            )
        catalogue = root / "suites.json"
        _ = catalogue.write_text(json.dumps({"cases": cases}))
        _ = (root / "conftest.py").write_text(
            "from pathlib import Path\nfrom scripts import test_suites\n"
            + f"test_suites.CATALOGUE = Path({str(catalogue)!r})\n"
            + "test_suites.catalogue.cache_clear()\n"
        )
        observations = root / "observations"
        program = (
            "import os,sys; "
            + f"os.environ['CI_DATABASE_OBSERVATIONS']={str(observations)!r}; "
            + "os.environ['CI_DATABASE_CONTEXT']='{}'; "
            "os.environ.pop('CI_UNIT_REPORT_DIR',None); "
            "os.environ.pop('CI_DATABASE_CENSUS',None); "
            "import pytest; sys.exit(pytest.main(['--rootdir=.', '-p', "
            "'scripts.test_suites_plugin', '-p', 'scripts.database_observation_plugin', 'tests']))"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(BACKEND),
                "PYTEST_ADDOPTS": "",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "CI_DATABASE_OBSERVATIONS": str(observations),
                "TMPDIR": str(root),
            },
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert (result.returncode == 0) == (scenario == "integration"), (
            result.stdout + result.stderr
        )
        attempts = "".join(
            path.read_text() for path in root.glob("unit-tests-*/connection-attempts-*.txt")
        )
        assert bool(attempts) == (scenario != "integration"), result.stdout + result.stderr
        cleaned = {path.name.removeprefix("cleaned-") for path in root.glob("cleaned-*")}
        expected = (
            {"function", "class", "module", "package", "session"}
            if scenario == "integration"
            else {"function"}
            if scenario == "shared"
            else set[str]()
        )
        assert cleaned == expected, result.stdout + result.stderr
        if scenario == "integration":
            assert classify(observations)[0] == cases
        else:
            assert "Unit database connection guard failed" in result.stdout + result.stderr


def _assert_normal_child_refusals(tmp_path: Path) -> None:
    attempts = tmp_path / "normal-attempts.txt"
    programs = [
        "import sqlite3; sqlite3.connect(':memory:')",
        "import _sqlite3; _sqlite3.connect(':memory:')",
        "import psycopg; psycopg.connect('host=127.0.0.1 port=1 dbname=unused')",
        "import psycopg; psycopg.Connection.connect('host=127.0.0.1 port=1 dbname=unused')",
        "import asyncio,psycopg; "
        "asyncio.run(psycopg.AsyncConnection.connect('host=127.0.0.1 port=1 dbname=unused'))",
        "from sqlalchemy import create_engine; create_engine('sqlite://').connect()",
        "from sqlalchemy import create_engine; create_engine('sqlite://').raw_connection()",
        "import oracledb; oracledb.connect(dsn='127.0.0.1:1/unused')",
    ]
    environment = {
        **os.environ,
        "CI_NO_DATABASE_ATTEMPTS": str(attempts),
        "PYTHONPATH": os.pathsep.join([str(BACKEND / "scripts/ci_no_database"), str(BACKEND)]),
    }
    for program in programs:
        _ = attempts.write_text("")
        code = (
            "import sys\nassert 'psycopg' not in sys.modules and 'sqlalchemy' not in sys.modules\n"
            + "try:\n    "
            + program
            + "\nexcept AssertionError: pass\nelse: raise RuntimeError('child bypassed refusal')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert attempts.read_text(), program
    # The light bootstrap must propagate a fresh environment without waiting
    # for any driver import in the launching process.
    code = (
        "import subprocess,sys\n"
        "result=subprocess.run([sys.executable,'-c',"
        "\"import psycopg; psycopg.connect('host=127.0.0.1 port=1 dbname=unused')\"],"
        "env={},capture_output=True,text=True)\n"
        "assert result.returncode != 0, result.stderr\n"
        "assert 'Database connections are forbidden' in result.stderr, result.stderr\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    threaded = (
        "from concurrent.futures import ThreadPoolExecutor\nimport importlib\n"
        "def connect(name):\n"
        "    try:\n"
        "        driver=importlib.import_module(name)\n"
        "        if name=='sqlalchemy': driver.create_engine('sqlite://').connect()\n"
        "        else: driver.connect(':memory:' if name=='sqlite3' else "
        "'host=127.0.0.1 port=1 dbname=unused')\n"
        "    except AssertionError: return True\n"
        "    return False\n"
        "with ThreadPoolExecutor(max_workers=3) as pool:\n"
        "    assert all(pool.map(connect,['psycopg','sqlalchemy','sqlite3']))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", threaded],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _assert_managed_service_scope(tmp_path: Path) -> None:
    scope_root = tmp_path / "service_scope"
    (scope_root / "tests/storage").mkdir(parents=True)
    _ = (scope_root / "tests/test_baseline.py").write_text(
        "import os\ndef test_baseline(): assert os.getenv('S3_ENDPOINT') is None\n"
    )
    _ = (scope_root / "tests/storage/test_contract.py").write_text(
        "import os\ndef test_contract(): assert os.environ['S3_ENDPOINT'] == 'http://managed.test'\n"
    )
    scope_program = (
        "import os,sys; "
        + f"os.environ['CI_DATABASE_OBSERVATIONS']={str(scope_root / 'observations')!r}; "
        + "os.environ['CI_DATABASE_CONTEXT']='{}'; os.environ['CI_DATABASE_CENSUS']='1'; "
        + "os.environ['S3_ENDPOINT']='http://managed.test'; "
        + "os.environ.pop('CI_UNIT_REPORT_DIR',None); "
        + "import pytest; sys.exit(pytest.main(['--rootdir=.', '-p', "
        + "'scripts.test_suites_plugin', '-p', 'scripts.database_observation_plugin', 'tests']))"
    )
    scoped = subprocess.run(
        [sys.executable, "-c", scope_program],
        cwd=scope_root,
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
    assert scoped.returncode == 0, scoped.stdout + scoped.stderr


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
        _assert_supplement_preserves_acquisitions(tmp_path)
    else:
        with pytest.raises(ValueError):
            classify(tmp_path)


def _write_census_profile(directory: Path, source: str, outcome: str) -> list[str]:
    directory.mkdir()
    nodes = ["tests/test_probe.py::test_first", "tests/test_probe.py::test_second"]
    events: list[dict[str, object]] = [
        {"kind": "session_start", "pid": 1},
        {"kind": "selected", "pid": 1, "nodeids": nodes},
        {"kind": "session_finish", "pid": 1},
    ]
    for node in nodes:
        for phase in ("setup", "call", "teardown"):
            if phase == "call" and outcome in {"setup", "skipped"}:
                continue
            status = "failed" if phase == outcome else "passed"
            if phase == "setup" and outcome == "skipped":
                status = "skipped"
            events.append(
                {"kind": "report", "pid": 1, "nodeid": node, "when": phase, "outcome": status}
            )
        if source == "fixture":
            events.append({"kind": "consumes", "pid": 1, "nodeid": node, "fixtures": ["shared"]})
        elif source in {"direct", "child"}:
            events.append(
                {
                    "kind": "database",
                    "pid": 2 if source == "child" else 1,
                    "nodeid": node,
                    "entrypoint": "psycopg.connect",
                }
            )
        if outcome == "child":
            events.append({"kind": "python_child", "pid": 1, "nodeid": node, "child": 3})
    if source == "fixture":
        events.append({"kind": "database", "pid": 1, "fixtures": ["shared"]})
    elif source == "child":
        events.extend(
            [
                {"kind": "python_child", "pid": 1, "nodeid": nodes[0], "child": 2},
                {"kind": "instrumented", "pid": 2},
            ]
        )
    _ = (directory / "events-1.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n"
    )
    return nodes


def _assert_supplement_preserves_acquisitions(tmp_path: Path) -> None:
    unit = tmp_path / "proven-unit"
    nodes = _write_census_profile(unit, "none", "passed")
    units = dict.fromkeys(nodes, "unit")
    integrations = dict.fromkeys(nodes, "integration")
    output = tmp_path / "generated.json"
    assert classify(unit) == (units, {})
    for suite in ("integration", "e2e"):
        for outcome in ("passed", "skipped"):
            service = tmp_path / f"service-{suite}-{outcome}"
            _ = _write_census_profile(service, "none", outcome)
            with (service / "events-1.jsonl").open("a") as stream:
                for node in nodes:
                    stream.write(
                        json.dumps(
                            {"kind": "consumes", "pid": 1, "nodeid": node, "fixtures": ["service"]}
                        )
                        + "\n"
                    )
                stream.write(
                    json.dumps(
                        {"kind": "service", "pid": 1, "fixtures": ["service"], "suite": suite}
                    )
                    + "\n"
                )
            owners = dict.fromkeys(nodes, suite)
            assert classify(service) == (owners, {})
            generate(unit, output, services=service)
            assert json.loads(output.read_text()) == {"cases": owners, "services": owners}
            check(service, output)
            generate(unit, output)
            with pytest.raises(ValueError, match="ownership drift"):
                check(service, output)
    extension = tmp_path / "additional-tests"
    _ = _write_census_profile(extension, "direct", "passed")
    events = extension / "events-1.jsonl"
    _ = events.write_text(events.read_text().replace("test_probe.py", "test_added.py"))
    added = {node.replace("test_probe.py", "test_added.py"): "integration" for node in nodes}
    generate(unit, output, extension=extension)
    assert json.loads(output.read_text()) == {"cases": units | added}
    with pytest.raises(ValueError, match="Extension overlaps"):
        generate(unit, output, extension=unit)
    for source in ("direct", "fixture", "child"):
        for outcome in ("passed", "setup", "call", "teardown", "skipped", "child"):
            positive = tmp_path / f"positive-{source}-{outcome}"
            _ = _write_census_profile(positive, source, outcome)
            assert classify(positive) == (integrations, {})
            unresolved = tmp_path / f"unresolved-{source}-{outcome}"
            _ = _write_census_profile(
                unresolved, "none", outcome if outcome != "passed" else "child"
            )
            assert classify(unresolved)[0] == integrations
            assert set(classify(unresolved)[1]) == set(nodes)
            for full, supplement in ((positive, unit), (unit, positive)):
                with pytest.raises(ValueError, match="Conflicting database evidence"):
                    generate(full, output, supplement)
            for full, supplement in ((positive, unresolved), (unresolved, positive)):
                generate(full, output, supplement)
                assert json.loads(output.read_text()) == {"cases": integrations}
                assert json.loads((full / "inconclusive.json").read_text()) == {}
            for full, supplement in ((unit, unresolved), (unresolved, unit)):
                generate(full, output, supplement)
                assert json.loads(output.read_text()) == {"cases": units}
            generate(positive, output)
            check(positive, output)
            generate(unit, output)
            with pytest.raises(ValueError, match="ownership drift"):
                check(positive, output)
