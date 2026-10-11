"""Run conservative affected-module tests locally with four pytest workers."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.ci_postgres import affected_files


def run(root: Path) -> int:
    files = affected_files(root, "pull_request", "origin/main", worktree=True)
    if files:
        print(f"Affected modules: {', '.join(files)}", flush=True)
    else:
        print("No safe affected-only selection; running the complete hermetic suite.", flush=True)
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-n", "4", "--dist", "loadfile", *files],
        cwd=root / "backend",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.parse_args()
    return run(Path(__file__).resolve().parents[2])


if __name__ == "__main__":
    raise SystemExit(main())
