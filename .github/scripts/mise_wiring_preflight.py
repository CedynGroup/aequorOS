#!/usr/bin/env python3
"""Prove the mise task wiring is intact BEFORE anything tries to use it.

Backend CI is driven entirely through `mise run risk-service:<task>` from the
repository root. Each root task is a proxy: `dir = "backend"` plus
`run = "mise run risk-service:<the same name>"`, which lands in
`backend/mise.toml` where the real command lives.

Commit `f33e869` (2026-08-20) moved `backend/mise.toml` to
`backend/docs/mise.toml`. Every proxy then re-resolved to itself and recursed:
the `Risk Service CI` run of 2026-08-21 emitted 440 `mise run
risk-service:sync` lines in its first step and failed there, so lint,
typecheck, the hermetic suite and both Postgres suites executed nothing. From
the log it looked like a hang, not a missing file.

`backend/tests/architecture/test_ci_task_wiring.py` asserts the same contract,
but it cannot be the only guard: reaching it means running
`mise run risk-service:test-architecture`, which is one of the proxies that
recurses. A guard that only runs when the thing it guards is healthy is not a
guard. Hence this script — stdlib only, no mise, no uv, no dependency install —
run as the first job of the workflow, with every other job needing it.

Exit 0 = wiring intact. Exit 1 = a named, specific defect.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - runner regression, not a code path
    print(
        "::error::This preflight needs Python 3.11+ for tomllib; the runner has "
        f"{sys.version.split()[0]}. It must not be skipped — it is the only check "
        "that still works when the mise task wiring does not."
    )
    raise SystemExit(1) from None

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_CONFIG = REPO_ROOT / "mise.toml"
BACKEND_CONFIG = REPO_ROOT / "backend" / "mise.toml"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# Where the file went the last time this broke. Named so the failure message can
# point at the actual mistake instead of "file not found".
KNOWN_MISLOCATIONS = (
    REPO_ROOT / "backend" / "docs" / "mise.toml",
    REPO_ROOT / "backend" / "app" / "mise.toml",
)

_DELEGATION = re.compile(r"^mise\s+run\s+(\S+)")
_WORKFLOW_INVOCATION = re.compile(r"mise\s+run\s+([A-Za-z0-9_.:-]+)")

failures: list[str] = []

# Full-line comments are stripped before scanning. A workflow header that
# DESCRIBES a task invocation is documentation, not a gate, and treating the two
# alike forces every comment about this machinery to be written in circumlocution
# — which is exactly how the machinery stopped being explained.
_COMMENT_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)


def _without_comments(path: Path) -> str:
    return _COMMENT_LINE.sub("", path.read_text(encoding="utf-8"))



def fail(message: str) -> None:
    failures.append(message)
    print(f"::error::{message}")


def tasks(path: Path) -> dict[str, dict[str, object]]:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    table = document.get("tasks", {})
    if not isinstance(table, dict):
        return {}
    return {name: body for name, body in table.items() if isinstance(body, dict)}


def run_commands(body: dict[str, object]) -> list[str]:
    run = body.get("run")
    if isinstance(run, str):
        return [run]
    if isinstance(run, list):
        return [item for item in run if isinstance(item, str)]
    return []


def main() -> int:
    if not ROOT_CONFIG.is_file():
        fail(f"{ROOT_CONFIG.relative_to(REPO_ROOT)} is missing; CI has no tasks to run.")
        return 1

    if not BACKEND_CONFIG.is_file():
        moved_to = next((p for p in KNOWN_MISLOCATIONS if p.is_file()), None)
        detail = (
            f" It appears to have been moved to {moved_to.relative_to(REPO_ROOT)} —"
            f" move it back with `git mv {moved_to.relative_to(REPO_ROOT)} backend/mise.toml`."
            if moved_to
            else ""
        )
        fail(
            "backend/mise.toml is missing. Every root task proxies into it with "
            "`mise run risk-service:<same name>`; without it each task invokes "
            "ITSELF and recurses until the runner gives up, having run no lint, "
            "no typecheck and no test." + detail
        )
        return 1

    root_tasks = tasks(ROOT_CONFIG)
    backend_tasks = tasks(BACKEND_CONFIG)

    # 1. Every proxy must reach a real command in the backend file.
    for name, body in sorted(root_tasks.items()):
        if body.get("dir") != "backend":
            continue
        for command in run_commands(body):
            match = _DELEGATION.match(command.strip())
            if match is None:
                continue
            target = match.group(1)
            if target not in backend_tasks:
                fail(
                    f"Root task `{name}` delegates to `{target}`, which "
                    "backend/mise.toml does not define. It will resolve back to "
                    "itself and recurse."
                )
                continue
            for inner in run_commands(backend_tasks[target]):
                if _DELEGATION.match(inner.strip()) and inner.strip().split()[2:3] == [target]:
                    fail(
                        f"backend/mise.toml's `{target}` delegates to itself; "
                        "the proxy chain has no real command at the end of it."
                    )

    # 2. Every task a workflow names must exist in the ROOT file. CI runs
    #    `mise run` from the repository root, so a task defined only in the
    #    backend file is "task not found" there while working locally.
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for task in sorted(set(_WORKFLOW_INVOCATION.findall(_without_comments(workflow)))):
            if task not in root_tasks:
                fail(
                    f"{workflow.name} invokes `mise run {task}`, which the root "
                    "mise.toml does not define."
                )

    # 3. A backend task must not be a bare proxy with nothing behind it.
    for name, body in sorted(backend_tasks.items()):
        if not run_commands(body) and not body.get("depends"):
            fail(f"backend/mise.toml's `{name}` has neither a command nor dependencies.")

    if failures:
        print(f"\nmise wiring preflight FAILED with {len(failures)} defect(s).")
        return 1

    print(
        f"mise wiring preflight OK: {len(root_tasks)} root tasks, "
        f"{len(backend_tasks)} backend tasks, every proxy reaches a real command."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
