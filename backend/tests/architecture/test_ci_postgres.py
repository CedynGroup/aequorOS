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

from scripts import ci_postgres
from scripts.ci_postgres import BAO_MODULE, SHARDS, module_shards, plan, shard_for, verify
from scripts.test_fast import run as run_fast

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


@pytest.mark.parametrize("state", ["unstaged", "staged", "committed", "untracked", "fixture"])
def test_local_selection_includes_worktree_changes(
    selection_repo: tuple[Path, str, Path], state: str
) -> None:
    root, base, test = selection_repo
    test.write_text("def test_leaf():\n    assert 2 == 2\n")
    if state in {"staged", "committed"}:
        _ = run_git(root, "add", "-A")
    if state == "committed":
        _ = run_git(root, "commit", "-qm", "test change")
    if state == "untracked":
        (root / "backend/tests/domain/test_new.py").write_text("def test_new():\n    pass\n")
    if state == "fixture":
        test.write_text("VALUE = 2\ndef test_leaf():\n    assert VALUE == 2\n")
    assert ci_postgres.affected_files(root, "pull_request", base, worktree=True) == (
        [] if state in {"untracked", "fixture"} else ["tests/domain/test_leaf_example.py"]
    )


def test_local_profile_executes_selected_modules_and_full_fallback(
    selection_repo: tuple[Path, str, Path],
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, test = selection_repo
    other = test.with_name("test_other.py")
    other.write_text("def test_other():\n    assert True\n")
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "second module")
    base = run_git(root, "rev-parse", "HEAD")
    _ = run_git(root, "update-ref", "refs/remotes/origin/main", base)
    report = root / "result.xml"
    monkeypatch.setenv("PYTEST_ADDOPTS", f"--junitxml={report}")
    test.write_text("def test_leaf():\n    assert 2 == 2\n")
    assert run_fast(root) == 0
    assert [case.get("name") for case in ET.parse(report).iter("testcase")] == ["test_leaf"]
    assert "4 workers" in capfd.readouterr().out
    report.unlink()
    # A production change requires all modules; pytest failure must reach the caller.
    (root / "backend/app.py").write_text("VALUE = 2\n")
    other.write_text("def test_other():\n    assert False\n")
    assert run_fast(root) == 1
    cases = list(ET.parse(report).iter("testcase"))
    assert {case.get("name") for case in cases} == {"test_leaf", "test_other"}
    assert sum(case.find("failure") is not None for case in cases) == 1
    assert "complete hermetic suite" in capfd.readouterr().out


@pytest.mark.parametrize(
    "kind",
    [
        "main",
        "merge-group",
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
    event = {"main": "push", "merge-group": "merge_group"}.get(kind, "pull_request")
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
        "include": [
            {
                "shard": index,
                "openbao": index == module_shards().get(BAO_MODULE, shard_for(BAO_MODULE)),
            }
            for index in range(1, SHARDS + 1)
        ]
    }


@pytest.fixture(scope="module")
def built_reports(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    tmp_path = tmp_path_factory.mktemp("shard-run")
    # Run the real plugin under xdist, including parametrized ids containing ::.
    # The files deliberately hash to different shards.
    modules: list[str] = []
    for index in range(1, SHARDS + 1):
        suffix = next(n for n in range(100) if shard_for(f"tests/test_{n}.py") == index)
        module = f"tests/test_{suffix}.py"
        path = tmp_path / module
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('value', range(1, 82), ids=lambda n: f'value::{n}')\n"
            "def test_pass(value):\n    assert value > 0\n"
            "@pytest.mark.skip(reason='existing opt-in fixture')\n"
            "def test_skip():\n    pass\n"
        )
        modules.append(module)
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    reports = tmp_path / "reports"
    for suite in ("suite", "schema"):
        for index in range(1, ci_postgres.shard_count(suite) + 1):
            directory = reports / suite / f"pg-suite-{index}"
            env = {
                **os.environ,
                "PYTHONPATH": str(BACKEND),
                "PYTEST_ADDOPTS": "",
                "CI_POSTGRES_SHARD": str(index),
                "CI_POSTGRES_SUITE": suite,
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
    _ = shutil.copytree(reports / "suite", copy)
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
    shard_reports: tuple[Path, list[str]], built_reports: tuple[Path, list[str]], damage: str
) -> None:
    reports, modules = shard_reports
    if damage == "schema-skips":
        reports = built_reports[0] / "schema"
    elif damage == "missing-shard":
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


def test_recorded_durations_balance_modules_and_break_ties_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "durations.json"
    baseline.write_text(json.dumps({"durations": {f"tests/test_{n}.py": n for n in range(1, 9)}}))
    monkeypatch.setattr(ci_postgres, "DURATIONS", baseline)
    assignments = module_shards()
    totals = [
        sum(n for n in range(1, 9) if assignments[f"tests/test_{n}.py"] == index)
        for index in range(1, SHARDS + 1)
    ]
    assert totals == [9, 9, 9, 9]
    assert assignments["tests/test_8.py"] == 1
    collected = [f"tests/test_{n}.py::test_example" for n in range(1, 10)]
    partitions = [
        ci_postgres.selected_tests(collected, [], index, "suite") for index in range(1, SHARDS + 1)
    ]
    assert sorted(node for partition in partitions for node in partition) == sorted(collected)
    assert ci_postgres.selected_tests(collected, ["tests/test_8.py"], 1, "suite") == [collected[7]]
    assert ci_postgres.shard_count("schema") == 2
    assert ci_postgres.selected_tests(collected, [], 1, "schema") == [
        node for node in collected if shard_for(node.split("::")[0], 2) == 1
    ]


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "slow"])
def test_duration_baseline_rejects_invalid_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    baseline = tmp_path / "durations.json"
    baseline.write_text(json.dumps({"durations": {"tests/test_example.py": value}}))
    monkeypatch.setattr(ci_postgres, "DURATIONS", baseline)
    with pytest.raises(ValueError, match="Invalid duration"):
        module_shards()


@pytest.mark.parametrize("skipped", [0, 2])
def test_full_suite_floor_and_refresh_use_verified_execution_evidence(
    tmp_path: Path, skipped: int
) -> None:
    modules = [
        next(
            f"tests/test_duration_{n}.py"
            for n in range(100)
            if shard_for(f"tests/test_duration_{n}.py") == index
        )
        for index in range(1, SHARDS + 1)
    ]
    collected = [f"{modules[index % 4]}::test_example[{index}]" for index in range(3000)] + [
        f"{BAO_MODULE}::test_live"
    ]
    for index in range(1, SHARDS + 1):
        selected = ci_postgres.selected_tests(collected, [], index, "suite")
        directory = tmp_path / f"pg-suite-{index}"
        directory.mkdir()
        (directory / "collection-main.json").write_text(
            json.dumps(
                {
                    "count": SHARDS,
                    "shard": index,
                    "files": [],
                    "collected": collected,
                    "selected": selected,
                }
            )
        )
        suite = ET.Element("testsuite")
        for nodeid in selected:
            classname, name = ci_postgres.case_key(nodeid)
            case = ET.SubElement(suite, "testcase", classname=classname, name=name, time="1.0")
            if nodeid in collected[:skipped]:
                _ = ET.SubElement(case, "skipped")
        ET.ElementTree(suite).write(directory / "report.xml")
    output = tmp_path / "durations.json"
    if skipped:
        with pytest.raises(ValueError, match="execution floor"):
            ci_postgres.refresh_durations(tmp_path, "fixture-run", output)
        assert not output.exists()
    else:
        ci_postgres.refresh_durations(tmp_path, "fixture-run", output)
        result = ci_postgres.load_object(output)
        assert result["source_run"] == "fixture-run"
        assert result["durations"] == {
            **{module: 750 for module in modules},
            BAO_MODULE: 1,
        }


def test_pure_owner_change_does_not_select_an_empty_postgres_shard(
    selection_repo: tuple[Path, str, Path],
) -> None:
    root, _, test = selection_repo
    pure = test.with_name("test_credit_pd.py")
    test.rename(pure)
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "pure owner baseline")
    base = run_git(root, "rev-parse", "HEAD")
    pure.write_text("def test_leaf():\n    assert 2 == 2\n")
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "pure leaf body")
    assert plan(root, "pull_request", base)["mode"] == "full"
