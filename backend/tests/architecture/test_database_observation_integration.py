"""Behavioral controls for independent runtime resource observation.

This module's other-suite cases live in ``test_database_observation.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.database_observation_check import check, observations
from tests.support.database import isolated_database

BACKEND = Path(__file__).parents[2]


def test_observer_attributes_shared_setup_teardown_and_fresh_environment_children(
    tmp_path: Path,
) -> None:
    _ = (tmp_path / "pytest.ini").write_text("[pytest]\n")
    # Exercise the same SQLite entry point in the parent as well as in the
    # child so this observer regression is itself database-using evidence.
    with (
        isolated_database(f"sqlite:///{tmp_path / 'observer.db'}") as database,
        database.sqlite_connection() as connection,
    ):
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
    cases = observations(evidence)
    assert {nodeid: item.database for nodeid, item in cases.items()} == {
        **{f"test_probe.py::test_{name}": True for name in ("a", "b", "c", "d", "child")},
        "test_probe.py::test_pure[1]": False,
        "test_probe.py::test_pure[2]": False,
        "test_probe.py::test_transition[0]": True,
        "test_probe.py::test_transition[1]": False,
        "test_probe.py::test_skipped": False,
    }
    assert not cases["test_probe.py::test_skipped"].passed
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
    assert {node: item.database for node, item in observations(jobs).items()} == {
        "test_unit.py::test_case": False,
        "test_integration.py::test_case": True,
    }


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
            "PYTHONPATH": os.pathsep.join([str(BACKEND / "scripts/ci_no_database"), str(BACKEND)]),
            "CI_DATABASE_OBSERVATIONS": str(evidence),
            "CI_DATABASE_CONTEXT": "{}",
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {node: item.database for node, item in observations(evidence).items()} == {
        "test_probe.py::test_origin": True,
        "test_probe.py::test_unrelated": False,
    }


def _assert_failed_fixture_consumers(tmp_path: Path) -> None:
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
        evidence_dir = root / "observations"
        program = (
            "import os,sys; "
            + f"os.environ['CI_DATABASE_OBSERVATIONS']={str(evidence_dir)!r}; "
            + "os.environ['CI_DATABASE_CONTEXT']='{}'; "
            + f"os.environ['FIXTURE_PROFILE']={mode!r}; "
            + "os.environ.pop('CI_UNIT_REPORT_DIR',None); "
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
        cases = observations(evidence_dir)
        assert {node: item.database for node, item in cases.items()} == dict.fromkeys(
            nodes, mode != "unit"
        )
        assert all(item.confirmed_children for item in cases.values())
        if mode != "unit":
            for scope in scopes:
                count = len((root / f"setups-{scope}").read_text().splitlines())
                assert count == (3 if scope == "function" else 1)


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
        fixtures = "import pytest, sqlite3\nfrom pathlib import Path\n"
        for scope in ("function", "class", "module", "package", "session"):
            marker = root / f"cleaned-{scope}"
            cleanup = "sqlite3.connect(':memory:').close()"
            if scenario.endswith("caught") or scenario == "shared":
                cleanup = "try: " + cleanup + "\n    except AssertionError: return"
            resource = "    yield\n    " + cleanup + f"\n    Path({str(marker)!r}).touch()\n"
            if scenario.startswith("unit-setup"):
                resource = "    sqlite3.connect(':memory:').close()\n    yield\n"
                if scenario.endswith("caught"):
                    resource = (
                        "    try: sqlite3.connect(':memory:').close()\n"
                        "    except AssertionError: pass\n    yield\n"
                    )
            fixture = f"resource_{scope}"
            fixtures += f"@pytest.fixture(scope={scope!r})\ndef {fixture}():\n" + resource
            for suite in ("unit", "integration"):
                consumes = (suite == "integration" and not scenario.startswith("unit")) or (
                    suite == "unit" and scenario != "integration"
                )
                argument = fixture if consumes else ""
                body = "pass"
                if scenario == "shared" and suite == "unit":
                    argument = "request"
                    body = f"request.getfixturevalue({fixture!r})"
                order = (
                    "a"
                    if (suite == "unit" if scenario.startswith("unit") else suite == "integration")
                    else "b"
                )
                suffix = "_integration" if suite == "integration" else ""
                _ = (tests / f"test_{order}_{scope}{suffix}.py").write_text(
                    "class TestProbe:\n"
                    + f"    def test_{suite}(self{', ' if argument else ''}{argument}): {body}\n"
                )
        _ = (root / "conftest.py").write_text(fixtures)
        observations_dir = root / "observations"
        program = (
            "import os,sys; "
            + f"os.environ['CI_DATABASE_OBSERVATIONS']={str(observations_dir)!r}; "
            + "os.environ['CI_DATABASE_CONTEXT']='{}'; "
            + "os.environ.pop('CI_UNIT_REPORT_DIR',None); "
            + "import pytest; sys.exit(pytest.main(['--rootdir=.', '-p', "
            + "'scripts.test_suites_plugin', '-p', "
            + "'scripts.database_observation_plugin', 'tests']))"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(BACKEND),
                "PYTEST_ADDOPTS": "",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "CI_DATABASE_OBSERVATIONS": str(observations_dir),
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
            else {"function", "class", "module"}
            if scenario == "shared"
            else set[str]()
        )
        assert cleaned == expected, result.stdout + result.stderr
        if scenario == "integration":
            check(observations_dir)
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
    _ = (scope_root / "tests/storage/test_contract_integration.py").write_text(
        "import os\ndef test_contract(): assert os.environ['S3_ENDPOINT'] == 'http://managed.test'\n"
    )
    scope_program = (
        "import os,sys; "
        + f"os.environ['CI_DATABASE_OBSERVATIONS']={str(scope_root / 'observations')!r}; "
        + "os.environ['CI_DATABASE_CONTEXT']='{}'; "
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
