"""Unit-only local feedback; CI owns integration and service coverage."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

from scripts.ci_postgres import affected_files, git


class Arguments(argparse.Namespace):
    lf: bool = False


class _FailureCache(Protocol):
    def get(self, key: str, default: dict[str, bool]) -> dict[str, bool]: ...


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Pytest otherwise reruns every selected unit when only non-selected failures remain.
    # This plugin is loaded only for --lf, leaving its real failure cache untouched.
    assert config.cache is not None
    cache = cast(_FailureCache, config.cache)
    failed = cache.get("cache/lastfailed", {})
    deselected = [item for item in items if item.nodeid not in failed]
    items[:] = [item for item in items if item.nodeid in failed]
    config.hook.pytest_deselected(items=deselected)


def unchanged(root: Path) -> bool:
    try:
        base = git(root, "merge-base", "origin/main", "HEAD").strip()
        return not (
            git(root, "diff", "--name-only", base).strip()
            or git(root, "ls-files", "--others", "--exclude-standard").strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return False


def run(root: Path, *, last_failed: bool = False) -> int:
    parallel = ["-n", "auto", "--maxprocesses=2", "--max-worker-restart=0"]
    if last_failed:
        print(
            "Unit-only last-failed fix loop, serial; no cached failures means no tests.",
            flush=True,
        )
        arguments = [
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
    else:
        files = affected_files(root, "pull_request", "origin/main", worktree=True, suite="unit")
        if files:
            print(
                f"Affected unit modules (at most two workers): {', '.join(files)}. "
                "Not complete coverage; integration and service tests stay in CI.",
                flush=True,
            )
            arguments = [*parallel, "--dist", "load", "-m", "unit", *files]
        elif unchanged(root):
            print(
                "No changes against origin/main's merge base; no tests selected. "
                "This is not full validation; CI owns integration and service coverage.",
                flush=True,
            )
            return 0
        else:
            print(
                "No safe affected-only selection (unknown, production, fixture or shared changes). "
                "Fallback: guarded unit suite ONLY, at most two workers. "
                "Integration/service coverage stays in CI.",
                flush=True,
            )
            arguments = [*parallel, "--dist", "load", "-m", "unit", "--suite-only"]
    environment = dict(os.environ)
    for variable in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        environment[variable] = "1"
    return subprocess.call(
        [sys.executable, "-m", "pytest", *arguments],
        cwd=root / "backend",
        env=environment,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lf", action="store_true", help="Rerun only cached unit failures, serially"
    )
    args = parser.parse_args(namespace=Arguments())
    return run(Path(__file__).resolve().parents[2], last_failed=args.lf)


if __name__ == "__main__":
    raise SystemExit(main())
