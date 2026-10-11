"""Run conservative affected-module tests locally with four pytest workers."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.ci_postgres import affected_files


class Arguments(argparse.Namespace):
    base: str = "origin/main"
    collect_only: bool = False
    junitxml: Path | None = None


def run(root: Path, base: str, options: list[str]) -> int:
    files = affected_files(root, "pull_request", base, worktree=True)
    if files:
        print(f"Affected modules: {', '.join(files)}", flush=True)
    else:
        print("No safe affected-only selection; running the complete hermetic suite.", flush=True)
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-n", "4", "--dist", "loadfile", *files, *options],
        cwd=root / "backend",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Git base (default: origin/main)")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--junitxml", type=Path)
    args = parser.parse_args(namespace=Arguments())
    options: list[str] = []
    if args.collect_only:
        options.append("--collect-only")
    if args.junitxml is not None:
        options.append(f"--junitxml={args.junitxml}")
    return run(Path(__file__).resolve().parents[2], args.base, options)


if __name__ == "__main__":
    raise SystemExit(main())
