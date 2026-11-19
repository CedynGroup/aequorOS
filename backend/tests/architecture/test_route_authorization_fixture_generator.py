"""The route authorization fixture stays generated from the registered apps.

The committed catalog must equal what ``scripts.route_authorization_dump``
derives from the live route tables, byte for byte, without a database or the
network, and the freshness check must refuse a hand-edited copy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.route_authorization_dump import FIXTURE_PATH, main

COMMITTED = FIXTURE_PATH.read_bytes()
BACKEND_ROOT = Path(__file__).parents[2]


def _generate(tmp_path: Path) -> bytes:
    output = tmp_path / "route_authorization.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    assert main(["--output", str(output)]) == 0
    return output.read_bytes()


def test_generator_reproduces_the_committed_fixture(tmp_path: Path) -> None:
    assert _generate(tmp_path) == COMMITTED


def test_generator_output_is_deterministic(tmp_path: Path) -> None:
    first = _generate(tmp_path / "first")
    second = _generate(tmp_path / "second")
    assert first == second


def test_generator_runs_without_database_environment(tmp_path: Path) -> None:
    """No DATABASE_URL, no TEST_DATABASE_URL, no services: still byte-exact."""
    output = tmp_path / "route_authorization.json"
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "TEST_DATABASE_URL"}
    }
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.route_authorization_dump", "--output", str(output)],
        capture_output=True,
        cwd=BACKEND_ROOT,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert output.read_bytes() == COMMITTED


def test_freshness_check_accepts_the_committed_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--check"]) == 0
    assert "risk-service:route-authorization-fresh" not in capsys.readouterr().out


def test_freshness_check_reports_a_removed_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = tmp_path / "route_authorization.json"
    catalog = json.loads(COMMITTED)
    rule = next(
        item
        for item in catalog["rules"]
        if item["name"] == "tenant-dependencies-filing-templates-scoped"
    )
    rule["routes"] = rule["routes"][1:]
    stale.write_bytes((json.dumps(catalog, indent=2) + "\n").encode())

    assert main(["--check", "--fixture", str(stale)]) == 1
    assert "risk-service:route-authorization" in capsys.readouterr().err


def test_freshness_check_reports_a_moved_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = tmp_path / "route_authorization.json"
    catalog = json.loads(COMMITTED)
    source = next(
        item
        for item in catalog["rules"]
        if item["name"] == "tenant-dependencies-filing-templates-scoped"
    )
    moved = source["routes"][0]
    source["routes"] = source["routes"][1:]
    target = next(
        item
        for item in catalog["rules"]
        if item["name"] == "tenant-dependencies-85a162b4fc51"
    )
    target["routes"].append(moved)
    stale.write_bytes((json.dumps(catalog, indent=2) + "\n").encode())

    assert main(["--check", "--fixture", str(stale)]) == 1
    assert "risk-service:route-authorization" in capsys.readouterr().err
