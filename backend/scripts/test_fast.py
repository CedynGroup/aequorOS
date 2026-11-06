"""Local feedback only: affected modules or guarded units; CI owns full coverage."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from scripts.ci_postgres import affected_files, git


class Arguments(argparse.Namespace):
    full: bool = False
    lf: bool = False


def unchanged(root: Path) -> bool:
    try:
        base = git(root, "merge-base", "origin/main", "HEAD").strip()
        return not (
            git(root, "diff", "--name-only", base).strip()
            or git(root, "ls-files", "--others", "--exclude-standard").strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return False


def run(root: Path, *, full: bool = False, last_failed: bool = False) -> int:
    parallel = ["-n", "auto", "--maxprocesses=2", "--max-worker-restart=0"]
    if full:
        print(
            "Explicit full hermetic suite, serial. CI owns PostgreSQL/service coverage.", flush=True
        )
        arguments = ["-n", "0"]
    elif last_failed:
        print(
            "Last-failed fix loop, serial; no cached failures means no tests, not a full run.",
            flush=True,
        )
        arguments = ["-n", "0", "--lf", "--last-failed-no-failures=none"]
    else:
        files = affected_files(root, "pull_request", "origin/main", worktree=True)
        if files:
            print(
                f"Affected integration modules (at most two workers): {', '.join(files)}. "
                "Not complete coverage; CI remains authoritative.",
                flush=True,
            )
            arguments = [*parallel, "--dist", "loadfile", *files]
        elif unchanged(root):
            print(
                "No changes against origin/main's merge base; no tests selected. "
                "This is not full validation. Use risk-service:test-full explicitly.",
                flush=True,
            )
            return 0
        else:
            print(
                "No safe affected-only selection (unknown, production, fixture or shared changes). "
                "Fallback: guarded unit suite ONLY, at most two workers. "
                "Integration/service coverage stays in CI; use risk-service:test-full explicitly.",
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--full", action="store_true", help="Run the complete hermetic suite serially"
    )
    modes.add_argument(
        "--lf", action="store_true", help="Rerun only pytest's cached failures, serially"
    )
    args = parser.parse_args(namespace=Arguments())
    return run(Path(__file__).resolve().parents[2], full=args.full, last_failed=args.lf)


if __name__ == "__main__":
    raise SystemExit(main())
