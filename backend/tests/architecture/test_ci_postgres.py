"""Exercise CI planning, pytest sharding and coverage guards through their interfaces."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

import pytest
import yaml

from scripts import ci_postgres
from scripts.ci_postgres import BAO_MODULE, SHARDS, module_shards, plan, shard_for, verify
from scripts.test_fast import run as run_fast
from scripts.test_fast import unchanged as local_unchanged

BACKEND = Path(__file__).parents[2]
REPO = BACKEND.parent


def run_git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def selection_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> tuple[Path, str, Path]:
    report_context = cast(str | None, getattr(request, "param", None))
    if report_context is not None:
        monkeypatch.setenv(report_context, str(tmp_path / "outer-ci-reports"))
    # Synthetic local runs own their reports, independently of the outer CI job.
    for variable in ("CI_UNIT_REPORT_DIR", "CI_E2E_REPORT_DIR"):
        monkeypatch.delenv(variable, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    _ = run_git(root, "init", "-q", "-b", "main")
    _ = run_git(root, "config", "user.email", "ci@example.invalid")
    _ = run_git(root, "config", "user.name", "CI fixture")
    test = root / "backend/tests/credit/domain/test_leaf_example_integration.py"
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
    assert selected["files"] == ["tests/credit/domain/test_leaf_example_integration.py"]
    assert selected["matrix"] == {
        "include": [
            {
                "shard": shard_for("tests/credit/domain/test_leaf_example_integration.py"),
                "openbao": False,
            }
        ]
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
        (root / "backend/tests/credit/domain/test_new.py").write_text("def test_new():\n    pass\n")
    if state == "fixture":
        test.write_text("VALUE = 2\ndef test_leaf():\n    assert VALUE == 2\n")
    assert ci_postgres.affected_files(root, "pull_request", base, worktree=True) == (
        []
        if state in {"untracked", "fixture"}
        else ["tests/credit/domain/test_leaf_example_integration.py"]
    )


@pytest.mark.parametrize(
    "state", ["unchanged", "unstaged", "staged", "committed", "untracked", "missing-base"]
)
def test_local_unchanged_requires_a_known_clean_merge_base(
    selection_repo: tuple[Path, str, Path], state: str
) -> None:
    root, base, test = selection_repo
    if state != "missing-base":
        _ = run_git(root, "update-ref", "refs/remotes/origin/main", base)
    if state in {"unstaged", "staged", "committed"}:
        test.write_text("def test_leaf():\n    assert 2 == 2\n")
    if state in {"staged", "committed"}:
        _ = run_git(root, "add", "-A")
    if state == "committed":
        _ = run_git(root, "commit", "-qm", "test change")
    if state == "untracked":
        test.with_name("test_new.py").write_text("def test_new():\n    assert True\n")
    assert local_unchanged(root) == (state == "unchanged")


@pytest.mark.parametrize(
    "module",
    [
        "tests/credit/domain/test_changed_integration.py",
        "tests/db/test_contract_integration.py",
        "tests/storage/test_contract_integration.py",
        "tests/credit/domain/test_browser_e2e.py",
    ],
)
def test_default_never_imports_non_unit_modules(
    selection_repo: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    module: str,
) -> None:
    root, _base, _test = selection_repo
    target = root / "backend" / module
    target.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "from pathlib import Path\n"
        "Path('non-unit-import').write_text('imported')\n"
        "def test_changed():\n    assert {value} == {value}\n"
    )
    target.write_text(body.format(value=1))
    unit = root / "backend/tests/credit/domain/test_ordinary.py"
    unit.write_text("def test_unit():\n    assert True\n")
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "mixed suite baseline")
    base = run_git(root, "rev-parse", "HEAD")
    _ = run_git(root, "update-ref", "refs/remotes/origin/main", base)
    target.write_text(body.format(value=2))
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "non-unit body edit")
    ci = plan(root, "pull_request", base)
    if module == "tests/credit/domain/test_changed_integration.py":
        assert ci["files"] == [module]
        assert ci["mode"] == "affected"
    else:
        assert ci["mode"] == "full"
    report = root / "result.xml"
    monkeypatch.setenv("PYTEST_ADDOPTS", f"-p scripts.test_suites_plugin --junitxml={report}")
    monkeypatch.setenv("PYTHONPATH", str(BACKEND))
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "1")
    assert run_fast(root) == 0
    assert [case.get("name") for case in ET.parse(report).iter("testcase")] == ["test_unit"]
    assert not (root / "backend/non-unit-import").exists()


@pytest.fixture
def task_checkout(
    selection_repo: tuple[Path, str, Path], tmp_path: Path
) -> tuple[Path, dict[str, str], Path]:
    """Use the real mise tasks and runner; replace only external static tools."""
    root, _base, leaf = selection_repo
    shutil.copytree(
        BACKEND / "scripts", root / "backend/scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    for relative in ("mise.toml", "backend/mise.toml"):
        tasks = cast(
            dict[str, dict[str, object]], tomllib.loads((REPO / relative).read_text())["tasks"]
        )
        lines = ["[settings]", "auto_install = false"]
        for name, body in tasks.items():
            lines.append(f"[tasks.{json.dumps(name)}]")
            for key in ("dir", "depends", "run"):
                if key in body:
                    lines.append(f"{key} = {json.dumps(body[key])}")
            for key, value in cast(dict[str, str], body.get("env", {})).items():
                lines.append(f"env.{key} = {json.dumps(value)}")
        (root / relative).write_text("\n".join(lines) + "\n")
    preflight = root / ".github/scripts/mise_wiring_preflight.py"
    preflight.parent.mkdir(parents=True)
    shutil.copyfile(REPO / ".github/scripts/mise_wiring_preflight.py", preflight)
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n*.call\n")
    (root / "backend/pytest.ini").write_text(
        "[pytest]\ntestpaths=tests\naddopts=-p scripts.test_suites_plugin\n"
    )
    (leaf.parent / "test_unit_probe.py").write_text(
        "from pathlib import Path\n"
        "def test_unit_probe():\n    Path('unit.call').write_text('ran')\n    assert True\n"
    )
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "public task baseline")
    _ = run_git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    leaf.write_text("def test_leaf():\n    assert 2 == 2\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    trace = tmp_path / "commands.jsonl"
    for name in ("uv", "pnpm"):
        tool = tools / name
        tool.write_text(
            f"#!{sys.executable}\n"
            "import json, os, subprocess, sys\nfrom pathlib import Path\n"
            "with Path(os.environ['TASK_TRACE']).open('a') as log:\n"
            "    log.write(json.dumps(sys.argv) + '\\n')\n"
            "if sys.argv[1:5] == ['run','python','-m','scripts.test_fast']:\n"
            "    raise SystemExit(subprocess.call([sys.executable, *sys.argv[3:]]))\n"
        )
        tool.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("MISE_")}
    environment.update(
        {
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "MISE_TRUSTED_CONFIG_PATHS": str(root),
            "MISE_GLOBAL_CONFIG_FILE": str(tmp_path / "empty.toml"),
            "MISE_SYSTEM_CONFIG_DIR": str(tmp_path / "system"),
            "MISE_CONFIG_DIR": str(tmp_path / "config"),
            "MISE_DATA_DIR": str(tmp_path / "data"),
            "MISE_STATE_DIR": str(tmp_path / "state"),
            "MISE_CACHE_DIR": str(tmp_path / "cache"),
            "PYTHONPATH": str(root / "backend"),
            "PYTEST_XDIST_AUTO_NUM_WORKERS": "1",
            "PYTEST_ADDOPTS": "",
            "TASK_TRACE": str(trace),
        }
    )
    return root, environment, trace


@pytest.mark.parametrize("directory", ["", "backend"])
def test_public_check_executes_only_static_tools(
    task_checkout: tuple[Path, dict[str, str], Path], directory: str
) -> None:
    root, environment, trace = task_checkout
    mise = shutil.which("mise")
    assert mise is not None
    result = subprocess.run(
        [mise, "run", "risk-service:check"],
        cwd=root / directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [cast(list[str], json.loads(line))[1:] for line in trace.read_text().splitlines()]
    assert ["run", "ruff", "check", "."] in commands
    assert ["run", "python", "scripts/type_check_baseline.py", "check"] in commands
    assert ["run", "python", "-m", "scripts.test_fast"] not in commands
    assert not (root / "backend/unit.call").exists()


@pytest.mark.parametrize("directory", ["", "backend"])
def test_public_test_forwards_last_failed_and_never_runs_cached_non_units(
    task_checkout: tuple[Path, dict[str, str], Path], directory: str
) -> None:
    root, environment, trace = task_checkout
    mise = shutil.which("mise")
    assert mise is not None
    unit = root / "backend/tests/credit/domain/test_unit_probe.py"
    unit.write_text(unit.read_text().replace("assert True", "assert False"))
    report = root.parent / "public-result.xml"
    environment["PYTEST_ADDOPTS"] = f"--junitxml={report}"

    def dispatch(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [mise, "run", "risk-service:test", *arguments],
            cwd=root / directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    first = dispatch()
    assert first.returncode == 1, first.stdout + first.stderr
    assert {case.get("name") for case in ET.parse(report).iter("testcase")} == {"test_unit_probe"}
    integration = root / "backend/tests/credit/domain/test_leaf_example_integration.py"
    integration.write_text(
        "from pathlib import Path\n"
        "Path('non-unit.call').write_text('imported')\n"
        "def test_leaf():\n    assert False\n"
    )
    seeded = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-n",
            "0",
            "-m",
            "integration",
            str(integration.relative_to(root / "backend")),
        ],
        cwd=root / "backend",
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert seeded.returncode == 1, seeded.stdout + seeded.stderr
    cache = root / "backend/.pytest_cache/v/cache/lastfailed"
    assert len(cast(dict[str, bool], json.loads(cache.read_text()))) == 2
    (root / "backend/non-unit.call").unlink()
    unit.write_text(unit.read_text().replace("assert False", "assert True"))
    fixed = dispatch("--lf")
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert [case.get("name") for case in ET.parse(report).iter("testcase")] == ["test_unit_probe"]
    assert not (root / "backend/non-unit.call").exists()
    commands = [cast(list[str], json.loads(line))[1:] for line in trace.read_text().splitlines()]
    assert ["run", "python", "-m", "scripts.test_fast", "--lf"] in commands
    empty = dispatch("--lf")
    assert empty.returncode == 5, empty.stdout + empty.stderr
    assert list(ET.parse(report).iter("testcase")) == []
    assert not (root / "backend/non-unit.call").exists()
    unit.write_text(unit.read_text().replace("assert True", "assert False"))
    assert dispatch().returncode == 1
    (root / "backend/unit.call").unlink()
    unit.write_text(
        unit.read_text()
        .replace("test_unit_probe", "test_replacement")
        .replace(
            "assert False",
            "assert True",
        )
    )
    stale = dispatch("--lf")
    assert stale.returncode == 5, stale.stdout + stale.stderr
    assert list(ET.parse(report).iter("testcase")) == []
    assert not (root / "backend/unit.call").exists()
    assert not (root / "backend/non-unit.call").exists()


@pytest.mark.parametrize("route", ["api-fresh", "pre-push"])
@pytest.mark.parametrize("inject_test", [False, True])
def test_api_freshness_and_pre_push_remain_static(
    task_checkout: tuple[Path, dict[str, str], Path], route: str, inject_test: bool
) -> None:
    root, environment, trace = task_checkout
    document = cast(
        dict[str, list[dict[str, object]]],
        yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text()),
    )
    hook = next(
        hook
        for repo in document["repos"]
        for hook in cast(list[dict[str, object]], repo["hooks"])
        if hook["id"] == "risk-service-api-client-fresh"
    )
    assert hook["language"] == "system"
    assert hook["pass_filenames"] is False
    assert hook["stages"] == ["pre-push"]
    if inject_test:
        if route == "pre-push":
            hook["entry"] = "mise run risk-service:test"
        else:
            config = root / "mise.toml"
            original = "pnpm --filter @aequoros/risk-service-api run type-check"
            replacement = "mise run risk-service:test"
            assert json.dumps(original) in config.read_text()
            config.write_text(
                config.read_text().replace(json.dumps(original), json.dumps(replacement))
            )
    command = (
        shlex.split(cast(str, hook["entry"]))
        if route == "pre-push"
        else ["mise", "run", "risk-service:api-fresh"]
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
    assert result.returncode == 0, result.stdout + result.stderr

    def assert_static() -> None:
        commands = [
            cast(list[str], json.loads(line))[1:] for line in trace.read_text().splitlines()
        ]
        assert not any(
            "pytest" in command or "scripts.test_fast" in command for command in commands
        )
        assert ["--filter", "@aequoros/risk-service-api", "run", "type-check"] in commands
        assert not (root / "backend/unit.call").exists()

    if inject_test:
        assert (root / "backend/unit.call").exists()
        with pytest.raises(AssertionError):
            assert_static()
    else:
        assert_static()


@pytest.mark.parametrize("auto_workers", [1, 8])
@pytest.mark.parametrize(
    "selection_repo", [None, "CI_UNIT_REPORT_DIR", "CI_E2E_REPORT_DIR"], indirect=True
)
def test_local_profile_executes_selected_modules_and_guarded_unit_fallback(
    selection_repo: tuple[Path, str, Path],
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    auto_workers: int,
) -> None:
    root, base, test = selection_repo
    test = test.with_name("test_leaf_example.py")
    leaf_body = (
        "def test_leaf():\n"
        "    import os\n"
        "    from pathlib import Path\n"
        "    _ = Path('worker-count.txt').write_text(os.environ['PYTEST_XDIST_WORKER_COUNT'])\n"
        "    assert {value} == {value}\n"
    )
    test.write_text(leaf_body.format(value=1))
    other = test.with_name("test_other.py")
    other.write_text("def test_other():\n    assert True\n")
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "second module")
    base = run_git(root, "rev-parse", "HEAD")
    _ = run_git(root, "update-ref", "refs/remotes/origin/main", base)
    report = root / "result.xml"
    monkeypatch.setenv("PYTEST_ADDOPTS", f"-p scripts.test_suites_plugin --junitxml={report}")
    monkeypatch.setenv("PYTHONPATH", str(BACKEND))
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", str(auto_workers))
    test.write_text(leaf_body.format(value=2))
    assert run_fast(root) == 0
    assert [case.get("name") for case in ET.parse(report).iter("testcase")] == ["test_leaf"]
    assert int((root / "backend/worker-count.txt").read_text()) == min(auto_workers, 2)
    report.unlink()
    # The local fallback must not turn a production edit into the complete suite.
    (root / "backend/app.py").write_text("VALUE = 2\n")
    other.write_text("def test_other():\n    assert False\n")
    assert run_fast(root) == 1
    cases = list(ET.parse(report).iter("testcase"))
    assert {case.get("name") for case in cases} == {"test_leaf", "test_other"}
    assert sum(case.find("failure") is not None for case in cases) == 1
    assert "guarded unit suite ONLY" in capfd.readouterr().out


@pytest.mark.parametrize(
    "module",
    [
        "tests/db/test_roles_integration.py",
        "tests/storage/test_storage_integration.py",
        "tests/case/api/test_calculations_integration.py",
        "tests/case/api/test_liquidity_integration.py",
        "tests/case/api/test_scenarios_integration.py",
    ],
)
def test_serial_owner_edits_use_guarded_unit_fallback(
    selection_repo: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    module: str,
) -> None:
    root, _base, _test = selection_repo
    target = root / "backend" / module
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def test_owner():\n    assert True\n")
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "serial owner baseline")
    base = run_git(root, "rev-parse", "HEAD")
    _ = run_git(root, "update-ref", "refs/remotes/origin/main", base)
    target.write_text("def test_owner():\n    assert False\n")
    commands: list[list[str]] = []

    def execute(command: list[str], *, cwd: Path, env: dict[str, str]) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(subprocess, "call", execute)
    assert run_fast(root) == 0
    assert len(commands) == 1
    arguments = commands[0][3:]
    assert arguments == [
        "-n",
        "auto",
        "--maxprocesses=2",
        "--max-worker-restart=0",
        "--dist",
        "load",
        "-m",
        "unit",
        "--suite-only",
    ]


@pytest.mark.parametrize(
    "selection_repo", [None, "CI_UNIT_REPORT_DIR", "CI_E2E_REPORT_DIR"], indirect=True
)
def test_unit_last_failed_uses_real_pytest_cache_without_non_unit_imports(
    selection_repo: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _base, test = selection_repo
    report = root / "result.xml"
    monkeypatch.setenv("PYTEST_ADDOPTS", f"-p scripts.test_suites_plugin --junitxml={report}")
    monkeypatch.setenv("PYTHONPATH", str(BACKEND))
    assert run_fast(root, last_failed=True) == 5
    assert list(ET.parse(report).iter("testcase")) == []
    other = test.with_name("test_other.py")
    other.write_text("def test_other():\n    assert False\n")
    test.write_text("raise RuntimeError('integration imported')\n")
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "1")
    assert run_fast(root) == 1
    assert {case.get("name") for case in ET.parse(report).iter("testcase")} == {"test_other"}
    other.write_text("def test_other():\n    assert True\n")
    assert run_fast(root, last_failed=True) == 0
    assert [case.get("name") for case in ET.parse(report).iter("testcase")] == ["test_other"]
    assert run_fast(root, last_failed=True) == 5
    assert list(ET.parse(report).iter("testcase")) == []


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
        consumer.write_text(
            "from tests.credit.domain.test_leaf_example_integration import test_leaf\n"
        )
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
        suffix = next(n for n in range(100) if shard_for(f"tests/test_{n}_integration.py") == index)
        module = f"tests/test_{suffix}_integration.py"
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
    for index in range(1, SHARDS + 1):
        directory = reports / f"pg-pool-{index}"
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
    verify(reports, modules)


@pytest.mark.parametrize(
    "damage",
    [
        "missing-shard",
        "missing-case",
        "duplicate-case",
        "different-collection",
        "missing-module",
    ],
)
def test_coverage_guard_rejects_incomplete_or_inconsistent_results(
    shard_reports: tuple[Path, list[str]], damage: str
) -> None:
    reports, modules = shard_reports
    if damage == "missing-shard":
        for path in (reports / "pg-pool-2").iterdir():
            path.unlink()
    elif damage in {"missing-case", "duplicate-case"}:
        report = reports / "pg-pool-1/report.xml"
        tree = ET.parse(report)
        suite = next(tree.iter("testsuite"))
        case = next(suite.iter("testcase"))
        if damage == "missing-case":
            suite.remove(case)
        else:
            suite.append(case)
        tree.write(report)
    elif damage == "different-collection":
        manifest = next((reports / "pg-pool-2").glob("collection-*.json"))
        manifest.write_text('{"count":2,"shard":2,"files":[],"collected":[],"selected":[]}')
    elif damage == "missing-module":
        modules.append("tests/test_missing.py")
    with pytest.raises(ValueError):
        verify(reports, modules)


def test_workflow_preserves_required_names_and_joins_all_postgres_jobs() -> None:
    # YAML is a machine-consumed contract; assert its graph and configuration.
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.safe_load((REPO / ".github/workflows/risk-service.yml").read_text()),
    )
    jobs = document["jobs"]
    assert jobs["postgres-pool"]["name"] == "Postgres tests shard ${{ matrix.shard }}/6"
    assert jobs["gate"]["name"] == "Risk service gate"
    # The gate joins every real job directly; no chained summary job may sit
    # between the gate and the shards it vouches for.
    assert not {"postgres", "postgres-suite", "backend-e2e", "api-fresh"} & set(jobs)
    assert set(cast(list[str], jobs["gate"]["needs"])) == {
        "preflight",
        "static",
        "architecture",
        "postgres-pool",
        "postgres-locks",
        "real-data",
    }
    pool_strategy = cast(dict[str, object], jobs["postgres-pool"]["strategy"])
    matrix = cast(dict[str, object], pool_strategy["matrix"])
    assert matrix == "${{ fromJSON(needs.preflight.outputs.postgres_matrix) }}"
    assert pool_strategy["fail-fast"] is False
    assert cast(dict[str, object], jobs["postgres-locks"])["name"] == "Postgres concurrency locks"


def test_recorded_durations_balance_modules_and_break_ties_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "durations.json"
    weights = {n: n for n in range(1, 13)}
    baseline.write_text(
        json.dumps(
            {
                "durations": {
                    **{f"tests/test_{n}_integration.py": n for n in weights},
                    "tests/case/api/test_liquidity_integration.py": 500,
                }
            }
        )
    )
    monkeypatch.setattr(ci_postgres, "DURATIONS", baseline)
    assignments = module_shards()
    lock_free = [n for n in range(1, 13)]
    totals = [
        sum(n for n in lock_free if assignments[f"tests/test_{n}_integration.py"] == index)
        for index in range(1, SHARDS + 1)
    ]
    assert totals == [13, 13, 13, 13, 13, 13]
    assert assignments["tests/test_12_integration.py"] == 1
    # The lock job owns its files: they take no pool weight or shard.
    assert "tests/case/api/test_liquidity_integration.py" not in assignments
    collected = [f"tests/test_{n}_integration.py::test_example" for n in range(1, 13)]
    partitions = [ci_postgres.selected_tests(collected, [], index) for index in range(1, SHARDS + 1)]
    assert sorted(node for partition in partitions for node in partition) == sorted(collected)
    assert ci_postgres.selected_tests(collected, ["tests/test_12_integration.py"], 1) == [
        collected[11]
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
    base, remainder = divmod(ci_postgres.POOL_FLOOR, SHARDS)
    modules = [
        next(
            f"tests/test_duration_{n}_integration.py"
            for n in range(100)
            if shard_for(f"tests/test_duration_{n}_integration.py") == index
        )
        for index in range(1, SHARDS + 1)
    ]
    collected = [
        nodeid
        for offset, module in enumerate(modules)
        for nodeid in (
            f"{module}::test_example[{index}]"
            for index in range(base + (1 if offset < remainder else 0))
        )
    ] + [f"{BAO_MODULE}::test_live"]
    for index in range(1, SHARDS + 1):
        selected = ci_postgres.selected_tests(collected, [], index)
        directory = tmp_path / f"pg-pool-{index}"
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
        counts = iter(
            [
                *(base + (1 if offset < remainder else 0) for offset in range(SHARDS)),
            ]
        )
        assert result["durations"] == {
            **{module: float(next(counts)) for module in modules},
            BAO_MODULE: 1.0,
        }


def test_refresh_accepts_recorded_runs_without_collection_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded run's JUnit-only families refresh the baseline without manifests."""
    monkeypatch.setattr(ci_postgres, "SCHEMA_FLOOR", 0)
    monkeypatch.setattr(ci_postgres, "SUITE_FLOOR", 0)
    monkeypatch.setattr(
        ci_postgres,
        "module_index",
        lambda: {
            "tests.db.test_probe_integration": "tests/db/test_probe_integration.py",
            "tests.credit.api.test_probe_integration": (
                "tests/credit/api/test_probe_integration.py"
            ),
            "tests.storage.test_s3_compatible_contract_integration": (
                "tests/storage/test_s3_compatible_contract_integration.py"
            ),
            "tests.case.api.test_probe_integration": "tests/case/api/test_probe_integration.py",
        },
    )
    schema = tmp_path / "pg-schema-1"
    suite = tmp_path / "pg-suite-1"
    storage = tmp_path / "storage-tests"
    for directory in (schema, suite, storage):
        directory.mkdir()
    _ = (schema / "report.xml").write_text(
        '<testsuite><testcase classname="tests.db.test_probe_integration" '
        'name="test_probe" time="2.0"/></testsuite>'
    )
    _ = (suite / "report.xml").write_text(
        '<testsuite><testcase classname="tests.credit.api.test_probe_integration" '
        'name="test_probe" time="3.0"/>'
        '<testcase classname="tests.credit.api.test_probe_integration.TestSuite" '
        'name="test_probe" time="1.0"/></testsuite>'
    )
    _ = (storage / "report.xml").write_text(
        '<testsuite><testcase '
        'classname="tests.storage.test_s3_compatible_contract_integration.TestS3CompatibleStorageContract" '
        'name="test_probe" time="1.5"/></testsuite>'
    )
    output = tmp_path / "durations.json"
    ci_postgres.refresh_durations(tmp_path, "recorded-run", output)
    result = ci_postgres.load_object(output)
    assert result["durations"] == {
        "tests/db/test_probe_integration.py": 2.0,
        "tests/credit/api/test_probe_integration.py": 4.0,
        "tests/storage/test_s3_compatible_contract_integration.py": 1.5,
    }
    _ = (tmp_path / "pg-locks" / "report.xml").parent.mkdir()
    _ = (tmp_path / "pg-locks" / "report.xml").write_text(
        '<testsuite><testcase classname="tests.case.api.test_probe_integration" '
        'name="test_probe" time="1.0"/></testsuite>'
    )
    # pg-locks belongs to the lock job, not the pool scope: its presence neither
    # completes an incomplete refresh nor adds weight.
    _ = shutil.copyfile(output, tmp_path / "with-locks.json")
    ci_postgres.refresh_durations(tmp_path, "recorded-run", output)
    assert ci_postgres.load_object(output) == ci_postgres.load_object(tmp_path / "with-locks.json")
    _ = shutil.rmtree(storage)
    with pytest.raises(ValueError, match="complete"):
        ci_postgres.refresh_durations(tmp_path, "recorded-run", output)


def test_protected_pool_modules_may_neither_skip_nor_vanish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema/RLS and storage cases abstain in no environment (audit A360-4)."""
    monkeypatch.setattr(ci_postgres, "POOL_FLOOR", 0)
    module = "tests/db/test_probe_integration.py"
    other = next(
        f"tests/credit/api/test_probe_{n}_integration.py"
        for n in range(100)
        if shard_for(f"tests/credit/api/test_probe_{n}_integration.py") != shard_for(module)
    )
    collected = [f"{module}::test_a", f"{module}::test_b", f"{other}::test_c"]
    shards = sorted({shard_for(module), shard_for(other)})
    for shard in shards:
        directory = tmp_path / f"pg-pool-{shard}"
        directory.mkdir()
        selected = ci_postgres.selected_tests(collected, [], shard)
        (directory / "collection-main.json").write_text(
            json.dumps(
                {
                    "count": SHARDS,
                    "shard": shard,
                    "files": [],
                    "collected": collected,
                    "selected": selected,
                }
            )
        )
        suite = ET.Element("testsuite")
        for nodeid in selected:
            classname, name = ci_postgres.case_key(nodeid)
            ET.SubElement(suite, "testcase", classname=classname, name=name, time="1.0")
        ET.ElementTree(suite).write(directory / "report.xml")
    ci_postgres.verify(tmp_path, [])
    # A skipped schema case trips the protected zero-skip rule...
    report = ET.parse(tmp_path / f"pg-pool-{shard_for(module)}" / "report.xml")
    suite = next(report.iter("testsuite"))
    case = next(suite.iter("testcase"))
    _ = ET.SubElement(case, "skipped")
    report.write(tmp_path / f"pg-pool-{shard_for(module)}" / "report.xml")
    with pytest.raises(ValueError, match="Schema/RLS or storage contract tests skipped"):
        ci_postgres.verify(tmp_path, [])


def test_pure_owner_change_does_not_select_an_empty_postgres_shard(
    selection_repo: tuple[Path, str, Path],
) -> None:
    root, _, test = selection_repo
    pure = root / "backend/tests/credit/domain/test_credit_pd.py"
    pure.parent.mkdir(parents=True, exist_ok=True)
    test.rename(pure)
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "pure owner baseline")
    base = run_git(root, "rev-parse", "HEAD")
    pure.write_text("def test_leaf():\n    assert 2 == 2\n")
    _ = run_git(root, "add", "-A")
    _ = run_git(root, "commit", "-qm", "pure leaf body")
    assert plan(root, "pull_request", base)["mode"] == "full"
