"""Keep local feedback bounded without weakening CI suite ownership."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

import pytest

from scripts import test_fast

ROOT = Path(__file__).resolve().parents[3]


def selection(monkeypatch: pytest.MonkeyPatch, files: list[str], *, clean: bool = False) -> None:
    def affected(_root: Path, _event: str, _base: str, *, worktree: bool = False) -> list[str]:
        assert worktree
        return files

    def unchanged(_root: Path) -> bool:
        return clean

    monkeypatch.setattr(test_fast, "affected_files", affected)
    monkeypatch.setattr(test_fast, "unchanged", unchanged)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    commands: list[list[str]] = []

    def execute(command: list[str], *, cwd: Path, env: dict[str, str]) -> int:
        assert cwd == ROOT / "backend"
        for variable in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
        ):
            assert env[variable] == "1"
        commands.append(command)
        return 0

    monkeypatch.setenv("OMP_NUM_THREADS", "32")
    monkeypatch.setattr(test_fast.subprocess, "call", execute)
    return commands


def test_affected_integration_modules_keep_fixture_affinity_and_worker_cap(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    files = ["tests/credit/domain/test_leaf_integration.py"]
    selection(monkeypatch, files)
    assert test_fast.run(ROOT) == 0
    assert calls == [
        [
            sys.executable,
            "-m",
            "pytest",
            "-n",
            "auto",
            "--maxprocesses=2",
            "--max-worker-restart=0",
            "--dist",
            "loadfile",
            *files,
        ]
    ]
    assert "Not complete coverage" in capsys.readouterr().out


def test_unchanged_worktree_selects_nothing_and_says_so(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    selection(monkeypatch, [], clean=True)
    assert test_fast.run(ROOT) == 0
    assert calls == []
    assert "no tests selected" in capsys.readouterr().out


@pytest.mark.parametrize(
    "files",
    [
        [],
        ["tests/db/domain/test_role_integration.py"],
        ["tests/storage/domain/test_storage_integration.py"],
        ["tests/case/api/test_calculations_integration.py"],
        ["tests/case/api/test_liquidity_integration.py"],
        ["tests/case/api/test_scenarios_integration.py"],
    ],
)
def test_uncertain_or_serial_owner_uses_only_guarded_units(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[list[str]],
    files: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    selection(monkeypatch, files)
    assert test_fast.run(ROOT) == 0
    assert calls == [
        [
            sys.executable,
            "-m",
            "pytest",
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
    ]
    assert "guarded unit suite ONLY" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["--full", "--lf"])
def test_explicit_modes_never_use_selection_or_parallel_workers(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], mode: str
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> list[str]:
        raise AssertionError("Explicit modes must not depend on Git selection")

    monkeypatch.setattr(test_fast, "affected_files", forbidden)
    monkeypatch.setattr(sys, "argv", ["test_fast", mode])
    assert test_fast.main() == 0
    expected = [sys.executable, "-m", "pytest", "-n", "0"]
    if mode == "--lf":
        expected += ["--lf", "--last-failed-no-failures=none"]
    assert calls == [expected]


def test_pytest_failures_reach_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_command: list[str], *, cwd: Path, env: dict[str, str]) -> int:
        return 1

    monkeypatch.setattr(test_fast.subprocess, "call", fail)
    assert test_fast.run(ROOT, full=True) == 1


def test_full_and_last_failed_cannot_be_combined(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]
) -> None:
    monkeypatch.setattr(sys, "argv", ["test_fast", "--full", "--lf"])
    with pytest.raises(SystemExit) as error:
        _ = test_fast.main()
    assert error.value.code == 2
    assert calls == []


def test_unknown_git_base_is_not_an_unchanged_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_root: Path, *_args: str) -> str:
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(test_fast, "git", fail)
    assert not test_fast.unchanged(ROOT)


def test_root_and_backend_tasks_expose_the_same_three_profiles() -> None:
    root_tasks = cast(
        dict[str, dict[str, object]], tomllib.loads((ROOT / "mise.toml").read_text())["tasks"]
    )
    tasks = cast(
        dict[str, dict[str, object]],
        tomllib.loads((ROOT / "backend/mise.toml").read_text())["tasks"],
    )
    for name in ("test", "test-fast", "test-full", "test-lf"):
        key = f"risk-service:{name}"
        assert root_tasks[key]["dir"] == "backend"
        assert root_tasks[key]["run"] == f"mise run {key}"
        expected = "uv run python -m scripts.test_fast"
        if name == "test-full":
            expected += " --full"
        elif name == "test-lf":
            expected += " --lf"
        assert tasks[key]["run"] == expected
    assert tasks["risk-service:check"]["depends"] == [
        "risk-service:lint",
        "risk-service:typecheck",
        "risk-service:test",
    ]
    assert tasks["unit"]["run"] == (
        "uv run pytest -m unit --suite-only -n auto --dist load --durations=20"
    )
    assert "-n" not in cast(str, tasks["risk-service:test-postgres-schema"]["run"])
    assert "-n" not in cast(str, tasks["risk-service:test-postgres-locks"]["run"])
    assert "-n" not in cast(str, tasks["risk-service:test-storage"]["run"])
