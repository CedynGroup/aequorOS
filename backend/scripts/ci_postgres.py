"""Plan conservative PR selection and verify the complete PostgreSQL pool reports.

The CLI is stdlib-only: preflight and aggregate checks must work before mise.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Literal, cast

if __package__:
    from scripts.test_suites import case_key, owner
else:
    from test_suites import case_key, owner

SHARDS = 6
# Worker count of the pool's parallel (suite) phase; the schema phase is serial.
WORKERS = 4
# The schema phase's scope: it runs serially, so its modules weigh on a shard's
# wall at full duration while suite modules divide across the workers.
SCHEMA_PREFIX = "tests/db/"
DURATIONS = Path(__file__).with_name("ci_postgres_durations.json")
BAO_MODULE = "tests/attestation/service/test_attestation_openbao_integration.py"
# Owned by the dedicated concurrency-lock job, never by a pool shard.
LOCK_FILES = frozenset(
    {
        "tests/case/api/test_calculations_integration.py",
        "tests/case/api/test_liquidity_integration.py",
        "tests/case/api/test_scenarios_integration.py",
        "tests/storage/test_key_rotation_concurrency_integration.py",
    }
)
# Report families whose shards carry collection manifests, keyed by manifest
# presence. Pool shards always carry them; earlier report families stored only
# JUnit reports, and the refresh step maps their cases through module prefixes.
MANIFEST_FAMILIES = ("pg-pool",)
LEGACY_FAMILIES = ("pg-suite", "pg-schema", "storage-tests")
# Execution floors recorded from full main-branch runs, one per report family:
# a whole test module vanishing must trip them. The suite family floor assumes
# the largest module (the authorization coverage matrix) disappeared.
SCHEMA_FLOOR = 320
SUITE_FLOOR = 3000
POOL_FLOOR = 8789
# One shard runs its schema/RLS slice serially (schema tests grant function
# privileges to concurrently existing roles, so migration and role-probe
# fixtures may never overlap in one database) and its remaining
# integration/storage scope with workers.
PHASES = ("schema", "suite")


def shard_for(module: str, count: int = SHARDS) -> int:
    return int(hashlib.sha256(module.encode()).hexdigest(), 16) % count + 1


def module_shards() -> dict[str, int]:
    """Pack modules to balance each shard's wall: serial schema plus suite/4.

    Longest-first packing keeps fixture-sharing modules on a single worker. A
    shard's wall is its schema slice's full duration (serial) plus its suite
    slice divided across the workers, so packing weighs the two phases
    differently; unrecorded modules use the stable hash fallback until the next
    main refresh. Assignment uses the committed baseline and ownership, never a
    PR subset.
    """
    values = load_object(DURATIONS)["durations"]
    if not isinstance(values, dict):
        raise ValueError("Expected module durations")
    durations: dict[str, float] = {}
    for module, value in cast(dict[str, object], values).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Invalid duration for {module}")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid duration for {module}")
        if owner(module) == "integration" and module not in LOCK_FILES:
            weight = float(value)
            if not module.startswith(SCHEMA_PREFIX):
                weight /= WORKERS
            durations[module] = max(weight, 0.001)
    totals = [0.0] * SHARDS
    assignments: dict[str, int] = {}
    for module in sorted(durations, key=lambda name: (-durations[name], name)):
        index = min(range(SHARDS), key=lambda shard: (totals[shard], shard))
        assignments[module] = index + 1
        totals[index] += durations[module]
    return assignments


def selected_tests(collected: list[str], files: list[str], index: int) -> list[str]:
    assignments = module_shards()
    return [
        nodeid
        for nodeid in collected
        if (not files or nodeid.split("::")[0] in files)
        and assignments.get(nodeid.split("::")[0], shard_for(nodeid.split("::")[0])) == index
    ]


def strings(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Expected a list of strings")
    if not all(isinstance(item, str) for item in cast(list[object], value)):
        raise ValueError("Expected a list of strings")
    return cast(list[str], value)


def load_object(path: Path) -> dict[str, object]:
    value = cast(object, json.loads(path.read_text()))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return cast(dict[str, object], value)


def test_structure(source: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            node.body = [ast.Pass()]
    return ast.dump(tree, include_attributes=False)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True)


def affected_files(
    root: Path,
    event: str,
    base: str,
    *,
    worktree: bool = False,
    suite: Literal["unit", "integration"] = "integration",
) -> list[str]:
    """Only body or AST-neutral edits to unreferenced domain tests can narrow a run.

    Production, fixture, decorator, collection, config, addition/deletion and
    uncertain changes return no narrowed selection. Callers choose the fallback:
    full coverage in CI, guarded units for local feedback. No inferred application
    dependency graph can quietly miss an indirect consumer.
    """
    try:
        if event != "pull_request" or not base:
            raise ValueError("Full suite required outside a known PR diff")
        if worktree:
            base = git(root, "merge-base", base, "HEAD").strip()
            if git(root, "ls-files", "--others", "--exclude-standard").strip():
                raise ValueError("Untracked changes require the full suite")
        elif (
            subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD^2"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        ):
            base = git(root, "rev-parse", "HEAD^1").strip()
        target = [] if worktree else ["HEAD"]
        changes = git(root, "diff", "--name-status", "--no-renames", base, *target).splitlines()
        if not changes:
            raise ValueError("Empty change set")
        selected: list[str] = []
        tracked = git(root, "ls-files", "*.py").splitlines()
        for change in changes:
            status, path = change.split("\t")
            module = Path(path)
            if (
                status != "M"
                or not path.startswith("backend/tests/")
                or "domain" not in module.parts[2:-1]
                or not module.name.startswith("test_")
                or module.suffix != ".py"
            ):
                raise ValueError("Change is outside leaf test bodies")
            before = git(root, "show", f"{base}:{path}")
            after = (root / path).read_text()
            if test_structure(before) != test_structure(after):
                raise ValueError("Change is outside leaf test bodies")
            # Tests occasionally import other test modules as fixture libraries.
            # Any textual reference elsewhere is enough to reject narrowing.
            if any(module.stem in (root / other).read_text() for other in tracked if other != path):
                raise ValueError("Change is outside leaf test bodies")
            relative = module.relative_to("backend").as_posix()
            if owner(relative) != suite:
                raise ValueError("Change belongs to another suite owner")
            selected.append(relative)
        return sorted(selected)
    except (OSError, subprocess.CalledProcessError, SyntaxError, ValueError):
        return []


def plan(root: Path, event: str, base: str) -> dict[str, object]:
    files = affected_files(root, event, base)
    assignments = module_shards()
    indices = (
        sorted({assignments.get(path, shard_for(path)) for path in files})
        if files
        else list(range(1, SHARDS + 1))
    )
    return {
        "mode": "affected" if files else "full",
        "files": files,
        "matrix": {
            "include": [
                {
                    "shard": index,
                    "openbao": not files
                    and index == assignments.get(BAO_MODULE, shard_for(BAO_MODULE)),
                }
                for index in indices
            ]
        },
    }


def read_manifests(directory: Path, files: list[str], prefix: str) -> tuple[list[str], int]:
    """Verify every pool shard manifest set and return the exact expected selection.

    Each shard runs two phases: the schema slice serially (schema tests grant
    function privileges to concurrently existing roles, so two migration or
    role-probe fixtures may never overlap in one database) and the remaining
    integration/storage scope with workers. Phase lives in the manifest and
    report file names; the two phases' collections must partition the tree.
    """
    paths = sorted(directory.glob(f"{prefix}-*/collection-*.json"))
    if not paths:
        raise ValueError("Missing shard collection manifests")
    collected_by_phase: dict[str, list[str]] = {}
    selected_by_phase_shard: dict[tuple[str, int], list[str]] = {}
    shards: set[int] = set()
    for path in paths:
        phase = path.stem.removeprefix("collection-").split("-")[0]
        if phase not in PHASES:
            raise ValueError(f"Unknown pool phase in {path.name}")
        manifest = load_object(path)
        collected = strings(manifest["collected"])
        selected = strings(manifest["selected"])
        index = manifest["shard"]
        if not isinstance(index, int) or not 1 <= index <= SHARDS:
            raise ValueError(f"Invalid shard in {path}")
        if manifest["count"] != SHARDS or manifest["files"] != files:
            raise ValueError(f"Inconsistent shard configuration in {path}")
        if phase in collected_by_phase and collected != collected_by_phase[phase]:
            raise ValueError("Workers/shards collected different test sets")
        collected_by_phase[phase] = collected
        expected = selected_tests(collected, files, index)
        # An empty selection is a sanctioned no-op only when the command
        # collected real scope and the shard owns none of it; an empty
        # collection means the suite moved and the step ran nothing.
        if selected != expected or (not selected and not collected):
            raise ValueError(f"Incorrect or empty selection in {path}")
        key = (phase, index)
        if key in selected_by_phase_shard and selected_by_phase_shard[key] != selected:
            raise ValueError("Workers selected different test sets")
        selected_by_phase_shard[key] = selected
        shards.add(index)
    missing = [phase for phase in PHASES if phase not in collected_by_phase]
    if missing:
        raise ValueError(f"Missing {missing[0]}-phase collection manifests")
    overlap = set(collected_by_phase["schema"]) & set(collected_by_phase["suite"])
    if overlap:
        raise ValueError(f"Both phases collected {sorted(overlap)[0]}")
    full = [nodeid for phase in PHASES for nodeid in collected_by_phase[phase]]
    expected_all = [nodeid for nodeid in full if not files or nodeid.split("::")[0] in files]
    if Counter(
        nodeid for group in selected_by_phase_shard.values() for nodeid in group
    ) != Counter(expected_all):
        raise ValueError("Shards dropped or duplicated collected tests")
    if files and set(files) != {nodeid.split("::")[0] for nodeid in expected_all}:
        raise ValueError("An affected module collected no tests")
    return expected_all, len(shards)


def verify(directory: Path, files: list[str]) -> None:
    expected_all, shard_count = read_manifests(directory, files, "pg-pool")
    reports = sorted(directory.glob("pg-pool-*/report-*.xml"))
    if len(reports) != len(PHASES) * shard_count:
        raise ValueError("Missing or duplicate shard JUnit reports")
    node_by_case = {case_key(nodeid): nodeid for nodeid in expected_all}
    cases = [case for report in reports for case in ET.parse(report).iter("testcase")]
    if Counter((case.get("classname", ""), case.get("name", "")) for case in cases) != Counter(
        node_by_case.keys()
    ):
        raise ValueError("JUnit reports dropped or duplicated selected tests")
    skipped = sum(case.find("skipped") is not None for case in cases)
    executed = len(cases) - skipped
    floor = 1 if files else POOL_FLOOR
    if executed < floor or any(
        case.find("failure") is not None or case.find("error") is not None for case in cases
    ):
        raise ValueError("Tests failed, errored or did not reach the existing execution floor")
    abstaining = [
        node_by_case[(case.get("classname", ""), case.get("name", ""))]
        for case in cases
        if case.find("skipped") is not None
    ]
    if any(
        module.split("::")[0].startswith(("tests/db/", "tests/storage/")) for module in abstaining
    ):
        raise ValueError("Schema/RLS or storage contract tests skipped")
    if not files and any(nodeid.split("::")[0] == BAO_MODULE for nodeid in expected_all):
        bao = [
            case
            for case in cases
            if case.get("classname", "").startswith(
                BAO_MODULE.removesuffix(".py").replace("/", ".")
            )
        ]
        if not bao or any(case.find("skipped") is not None for case in bao):
            raise ValueError("OpenBao tests were absent or skipped")
        print(f"OpenBao: {len(bao)} executed, 0 skipped")
    print(
        f"pool: {len(cases)} collected, {executed} executed, {skipped} skipped; "
        "exact shard coverage verified"
    )


def module_index() -> dict[str, str]:
    """Map dotted module-name prefixes onto integration-owned test files."""
    # Same package-versus-standalone handling as the top of this file: the
    # documented refresh command runs `python3 backend/scripts/ci_postgres.py`
    # from the repository root, where `scripts` is not an importable package.
    if __package__:
        from scripts.test_suites import selected_modules  # noqa: PLC0415
    else:
        from test_suites import selected_modules  # noqa: PLC0415

    return {
        module.removesuffix(".py").replace("/", "."): module
        for module in selected_modules("integration")
    }


def report_module(classname: str, index: dict[str, str]) -> str | None:
    parts = classname.split(".")
    while parts:
        module = index.get(".".join(parts))
        if module is not None:
            return module
        parts.pop()
    return None


def _select_duration_evidence(
    directory: Path,
) -> tuple[list[Path], dict[tuple[str, str], str]]:
    """Verify pool manifests or a complete recorded run; return its JUnit reports."""
    pool = sorted(directory.glob("pg-pool-*/collection-*.json"))
    recorded = [
        family
        for family in LEGACY_FAMILIES
        if list(directory.glob(f"{family}-*")) or list(directory.glob(family))
    ]
    case_modules: dict[tuple[str, str], str] = {}
    if pool:
        verify(directory, [])
        collected, _ = read_manifests(directory, [], "pg-pool")
        case_modules = {case_key(nodeid): nodeid.split("::")[0] for nodeid in collected}
    elif sorted(LEGACY_FAMILIES) != sorted(recorded):
        raise ValueError(
            "Refresh requires either pg-pool shard reports or the complete "
            "pg-suite, pg-schema and storage-tests families of one recorded run"
        )
    else:
        for prefix in ("pg-suite", "pg-schema"):
            for manifest in sorted(directory.glob(f"{prefix}-*/collection-*.json")):
                if load_object(manifest)["files"] != []:
                    raise ValueError("Recorded-run refresh requires a full-suite selection")
        _verify_recorded_families(directory)
    reports = [
        report
        for report in sorted(directory.glob("*/report.xml"))
        + sorted(directory.glob("pg-pool-*/report-*.xml"))
        if not report.parent.name.startswith("pg-locks")
    ]
    return reports, case_modules


def refresh_durations(directory: Path, source_run: str, output: Path) -> None:
    """Refresh only from complete, successfully executed full-suite evidence.

    Pool shards carry collection manifests and are verified for exact coverage.
    Report families without manifests are accepted only as a complete set from
    one recorded run, whose own aggregates verified coverage at that revision.
    """
    reports, case_modules = _select_duration_evidence(directory)
    index = module_index()
    durations: dict[str, float] = {}
    owners: dict[str, str] = {}
    for report in reports:
        for case in ET.parse(report).iter("testcase"):
            identity = (case.get("classname", ""), case.get("name", ""))
            module = case_modules.get(identity) or report_module(identity[0], index)
            if module is None:
                raise ValueError(f"Unmapped JUnit classname {identity[0]!r}")
            if module in owners and owners[module] != report.parent.name:
                raise ValueError(f"Module {module} reported by two execution owners")
            owners[module] = report.parent.name
            if case.find("skipped") is not None and module.startswith(
                ("tests/db/", "tests/storage/")
            ):
                raise ValueError(f"Skipped case in {module}")
            duration = float(case.get("time", "0"))
            if not math.isfinite(duration) or duration < 0:
                raise ValueError("Invalid JUnit duration")
            durations[module] = durations.get(module, 0.0) + duration
    output.write_text(
        json.dumps(
            {
                "source_run": source_run,
                "durations": {k: round(v, 6) for k, v in sorted(durations.items())},
            },
            indent=2,
        )
        + "\n"
    )


def _verify_recorded_families(directory: Path) -> None:
    """Reject failed, errored or skipped-short evidence before it can refresh.

    A recorded run's own aggregates verified coverage at its revision, but its
    reports can still carry failed or errored cases from a premature
    termination; timing evidence from failed executions would distort the
    packing, so they are rejected exactly as the pool path rejects them.
    """
    counts: dict[str, int] = {
        "pg-schema-*/report.xml": 0,
        "pg-suite-*/report.xml": 0,
        "storage-tests/report.xml": 0,
    }
    for pattern in counts:
        for report in sorted(directory.glob(pattern)):
            for case in ET.parse(report).iter("testcase"):
                if case.find("skipped") is not None:
                    continue
                if case.find("failure") is not None or case.find("error") is not None:
                    raise ValueError(f"Failed or errored case in {report}")
                counts[pattern] += 1
    if (
        counts["pg-schema-*/report.xml"] < SCHEMA_FLOOR
        or counts["pg-suite-*/report.xml"] < SUITE_FLOOR
        or counts["storage-tests/report.xml"] < 1
    ):
        raise ValueError("Recorded-run reports did not reach the recorded execution floors")


class Arguments(argparse.Namespace):
    command: str
    root: Path
    directory: Path
    source_run: str
    output: Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    planning = sub.add_parser("plan")
    planning.add_argument("--root", type=Path, default=Path.cwd())
    checking = sub.add_parser("verify")
    checking.add_argument("directory", type=Path)
    refresh = sub.add_parser("refresh-durations")
    refresh.add_argument("directory", type=Path)
    refresh.add_argument("--source-run", required=True)
    refresh.add_argument("--output", type=Path, default=DURATIONS)
    args = parser.parse_args(namespace=Arguments())
    if args.command == "plan":
        result = plan(args.root, os.getenv("GITHUB_EVENT_NAME", ""), os.getenv("PR_BASE_SHA", ""))
        print(json.dumps(result))
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a") as stream:
                for key, value in result.items():
                    print(f"{key}={json.dumps(value) if key != 'mode' else value}", file=stream)
    elif args.command == "refresh-durations":
        refresh_durations(args.directory, args.source_run, args.output)
    else:
        verify(
            args.directory,
            strings(cast(object, json.loads(os.getenv("CI_POSTGRES_FILES", "[]")))),
        )


if __name__ == "__main__":
    main()
