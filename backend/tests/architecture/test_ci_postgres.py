"""Exercise CI planning, pytest sharding and coverage guards through their interfaces."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

import pytest
import yaml

from scripts.ci_postgres import BAO_MODULE, plan, shard_for, verify

BACKEND = Path(__file__).parents[2]
REPO = BACKEND.parent


def run_git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def selection_repo(tmp_path: Path) -> tuple[Path, str, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    _ = run_git(root, "init", "-q", "-b", "main")
    _ = run_git(root, "config", "user.email", "ci@example.invalid")
    _ = run_git(root, "config", "user.name", "CI fixture")
    test = root / "backend/tests/domain/test_leaf_example.py"
    test.parent.mkdir(parents=True)
    test.write_text("def test_leaf():\n    assert 1 == 1\n")
    _ = run_git(root, "add", ".")
    _ = run_git(root, "commit", "-qm", "baseline")
    return root, run_git(root, "rev-parse", "HEAD"), test


def test_leaf_body_change_selects_only_its_module(selection_repo: tuple[Path, str, Path]) -> None:
    root, base, test = selection_repo
    test.write_text("def test_leaf():\n    assert 2 == 2\n")
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "test change")
    selected = plan(root, "pull_request", base)
    assert selected["mode"] == "affected"
    assert selected["files"] == ["tests/domain/test_leaf_example.py"]
    assert selected["matrix"] == {
        "include": [{"shard": shard_for("tests/domain/test_leaf_example.py"), "openbao": False}]
    }


@pytest.mark.parametrize(
    "kind",
    [
        "main",
        "missing-base",
        "unknown-base",
        "empty",
        "fixture",
        "decorator",
        "production",
        "new-test",
        "deleted",
        "imported",
    ],
)
def test_uncertain_changes_run_every_shard(
    selection_repo: tuple[Path, str, Path], kind: str
) -> None:
    root, base, test = selection_repo
    event = "push" if kind == "main" else "pull_request"
    test.write_text("def test_leaf():\n    assert 2 == 2\n")
    if kind == "missing-base":
        base = ""
    elif kind == "unknown-base":
        base = "missing"
    elif kind == "empty":
        test.write_text("def test_leaf():\n    assert 1 == 1\n")
    elif kind == "fixture":
        test.write_text("VALUE = 2\ndef test_leaf():\n    assert VALUE == 2\n")
    elif kind == "decorator":
        test.write_text("@some_marker\ndef test_leaf():\n    assert 2 == 2\n")
    elif kind == "production":
        (root / "backend/app.py").write_text("VALUE = 2\n")
        _ = run_git(root, "add", ".")
    elif kind == "new-test":
        test.write_text("def test_leaf():\n    assert 1 == 1\ndef test_new():\n    assert True\n")
    elif kind == "deleted":
        test.unlink()
    elif kind == "imported":
        consumer = root / "backend/tests/consumer.py"
        consumer.write_text("from tests.domain.test_leaf_example import test_leaf\n")
        _ = run_git(root, "add", "-A")
        _ = run_git(root, "commit", "-qm", "existing consumer")
        base = run_git(root, "rev-parse", "HEAD")
        test.write_text("def test_leaf():\n    assert 3 == 3\n")
    if kind != "empty":
        _ = run_git(root, "add", "-A")
        _ = run_git(root, "commit", "-qm", "test change")
    result = plan(root, event, base)
    assert result["mode"] == "full"
    assert result["files"] == []
    assert result["matrix"] == {
        "include": [{"shard": index, "openbao": index == shard_for(BAO_MODULE)} for index in (1, 2)]
    }


@pytest.fixture(scope="module")
def built_reports(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    tmp_path = tmp_path_factory.mktemp("shard-run")
    # Run the real plugin under xdist, including parametrized ids containing ::.
    # The two files deliberately hash to different shards.
    modules: list[str] = []
    for index in (1, 2):
        suffix = next(n for n in range(100) if shard_for(f"tests/test_{n}.py") == index)
        module = f"tests/test_{suffix}.py"
        path = tmp_path / module
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('value', range(1, 162), ids=lambda n: f'value::{n}')\n"
            "def test_pass(value):\n    assert value > 0\n"
            "@pytest.mark.skip(reason='existing opt-in fixture')\n"
            "def test_skip():\n    pass\n"
        )
        modules.append(module)
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    reports = tmp_path / "reports"
    for index in (1, 2):
        directory = reports / f"pg-suite-{index}"
        env = {
            **os.environ,
            "PYTHONPATH": str(BACKEND),
            "PYTEST_ADDOPTS": "",
            "CI_POSTGRES_SHARD": str(index),
            "CI_POSTGRES_FILES": json.dumps(modules),
            "CI_POSTGRES_REPORT_DIR": str(directory),
        }
        _ = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                str(config),
                "-p",
                "scripts.ci_postgres_plugin",
                "-n",
                "2",
                "--dist",
                "loadfile",
                f"--junitxml={directory}/report.xml",
                "tests",
            ],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    return reports, modules


@pytest.fixture
def shard_reports(built_reports: tuple[Path, list[str]], tmp_path: Path) -> tuple[Path, list[str]]:
    reports, modules = built_reports
    copy = tmp_path / "reports"
    _ = shutil.copytree(reports, copy)
    return copy, modules.copy()


def test_real_workers_partition_every_test_and_preserve_junit_names(
    shard_reports: tuple[Path, list[str]],
) -> None:
    reports, modules = shard_reports
    verify(reports, modules, "suite")


@pytest.mark.parametrize(
    "damage",
    [
        "missing-shard",
        "missing-case",
        "duplicate-case",
        "different-collection",
        "missing-module",
        "schema-skips",
    ],
)
def test_coverage_guard_rejects_incomplete_or_inconsistent_results(
    shard_reports: tuple[Path, list[str]], damage: str
) -> None:
    reports, modules = shard_reports
    if damage == "missing-shard":
        for path in (reports / "pg-suite-2").iterdir():
            path.unlink()
    elif damage in {"missing-case", "duplicate-case"}:
        report = reports / "pg-suite-1/report.xml"
        tree = ET.parse(report)
        suite = next(tree.iter("testsuite"))
        case = next(suite.iter("testcase"))
        if damage == "missing-case":
            suite.remove(case)
        else:
            suite.append(case)
        tree.write(report)
    elif damage == "different-collection":
        manifest = next((reports / "pg-suite-2").glob("collection-*.json"))
        manifest.write_text('{"count":2,"shard":2,"files":[],"collected":[],"selected":[]}')
    elif damage == "missing-module":
        modules.append("tests/test_missing.py")
    with pytest.raises(
        ValueError, match="Schema/RLS tests skipped" if damage == "schema-skips" else None
    ):
        verify(reports, modules, "schema" if damage == "schema-skips" else "suite")


def test_workflow_preserves_required_names_and_joins_all_postgres_jobs() -> None:
    # YAML is a machine-consumed contract; assert its graph and configuration.
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((REPO / ".github/workflows/risk-service.yml").read_text()),
    )
    jobs = document["jobs"]
    assert jobs["postgres-suite"]["name"] == "Full suite on Postgres"
    assert jobs["postgres"]["name"] == "Postgres schema, RLS and locks"
    assert jobs["gate"]["name"] == "Risk service gate"
    assert set(cast(list[str], jobs["postgres"]["needs"])) == {
        "preflight",
        "postgres-schema",
        "postgres-locks",
    }
    assert set(cast(list[str], jobs["postgres-suite"]["needs"])) == {"preflight", "postgres-shard"}
    schema_strategy = cast(dict[str, object], jobs["postgres-schema"]["strategy"])
    matrix = cast(dict[str, object], schema_strategy["matrix"])
    assert matrix["shard"] == [1, 2]
    assert schema_strategy["fail-fast"] is False
    assert cast(dict[str, object], jobs["postgres-shard"]["strategy"])["fail-fast"] is False
