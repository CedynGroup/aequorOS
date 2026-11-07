"""Keep local feedback bounded without weakening CI suite ownership."""

from __future__ import annotations

import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

import pytest

from scripts import test_fast

ROOT = Path(__file__).resolve().parents[3]


def test_check_has_only_static_owners() -> None:
    tasks = cast(
        dict[str, dict[str, object]],
        tomllib.loads((ROOT / "backend/mise.toml").read_text())["tasks"],
    )
    check = tasks["risk-service:check"]
    assert check["depends"] == ["risk-service:lint", "risk-service:typecheck"]
    assert shlex.split(cast(str, check["run"])) == [
        "python3",
        "../.github/scripts/mise_wiring_preflight.py",
    ]


def selection(monkeypatch: pytest.MonkeyPatch, files: list[str], *, clean: bool = False) -> None:
    def affected(
        _root: Path, _event: str, _base: str, *, worktree: bool = False, suite: str = ""
    ) -> list[str]:
        assert worktree
        assert suite == "unit"
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


def test_affected_unit_modules_keep_guarded_scheduler_and_worker_cap(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    files = ["tests/credit/domain/test_leaf.py"]
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


def test_uncertain_selection_uses_only_guarded_units(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    selection(monkeypatch, [])
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


def test_last_failed_never_uses_selection_or_parallel_workers(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> list[str]:
        raise AssertionError("Explicit modes must not depend on Git selection")

    monkeypatch.setattr(test_fast, "affected_files", forbidden)
    monkeypatch.setattr(sys, "argv", ["test_fast", "--lf"])
    assert test_fast.main() == 0
    expected = [
        sys.executable,
        "-m",
        "pytest",
        "-n",
        "0",
        "-m",
        "unit",
        "--suite-only",
        "-p",
        "scripts.test_fast",
        "--lf",
        "--last-failed-no-failures=none",
    ]
    assert calls == [expected]


def test_pytest_failures_reach_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_command: list[str], *, cwd: Path, env: dict[str, str]) -> int:
        return 1

    monkeypatch.setattr(test_fast.subprocess, "call", fail)
    assert test_fast.run(ROOT, last_failed=True) == 1


def test_removed_full_mode_is_rejected(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]
) -> None:
    monkeypatch.setattr(sys, "argv", ["test_fast", "--full"])
    with pytest.raises(SystemExit) as error:
        _ = test_fast.main()
    assert error.value.code == 2
    assert calls == []


def test_unknown_git_base_is_not_an_unchanged_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_root: Path, *_args: str) -> str:
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(test_fast, "git", fail)
    assert not test_fast.unchanged(ROOT)


def test_root_and_backend_tasks_expose_only_check_and_test() -> None:
    root_tasks = cast(
        dict[str, dict[str, object]], tomllib.loads((ROOT / "mise.toml").read_text())["tasks"]
    )
    tasks = cast(
        dict[str, dict[str, object]],
        tomllib.loads((ROOT / "backend/mise.toml").read_text())["tasks"],
    )
    for name in ("check", "test"):
        key = f"risk-service:{name}"
        assert root_tasks[key]["dir"] == "backend"
        assert root_tasks[key]["run"] == f"mise run {key}"
    assert tasks["risk-service:test"]["run"] == "uv run python -m scripts.test_fast"
    retired = {
        "unit",
        "integration",
        "risk-service:smoke",
        *(
            f"risk-service:test-{name}"
            for name in (
                "fast",
                "full",
                "lf",
                "postgres",
                "postgres-migrations",
                "postgres-locks",
                "postgres-schema",
                "postgres-suite",
                "unit",
                "integration",
                "e2e",
                "architecture",
                "real-data",
                "storage",
            )
        ),
    }
    assert len(retired) == 17
    assert not retired.intersection(root_tasks)
    assert not retired.intersection(tasks)
