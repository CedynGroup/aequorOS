"""Plan conservative PR selection and verify the complete PostgreSQL shard reports.

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
from typing import cast

if __package__:
    from scripts.test_suites import case_key, unit_modules
else:
    from test_suites import case_key, unit_modules

SHARDS = 4
SCHEMA_SHARDS = 2
DURATIONS = Path(__file__).with_name("ci_postgres_durations.json")
BAO_MODULE = "tests/attestation/service/test_attestation_openbao.py"


def shard_for(module: str, count: int = SHARDS) -> int:
    return int(hashlib.sha256(module.encode()).hexdigest(), 16) % count + 1


def shard_count(suite: str) -> int:
    return SCHEMA_SHARDS if suite == "schema" else SHARDS


def module_shards(suite: str = "suite") -> dict[str, int]:
    """Longest-first packing keeps fixture-sharing modules on a single worker.

    Unrecorded modules use the stable hash fallback until the next main refresh.
    Assignment uses the committed baseline and ownership, never a PR subset.
    """
    if suite == "schema":
        return {}
    values = load_object(DURATIONS)["durations"]
    if not isinstance(values, dict):
        raise ValueError("Expected module durations")
    durations: dict[str, float] = {}
    for module, value in cast(dict[str, object], values).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Invalid duration for {module}")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid duration for {module}")
        if module not in unit_modules():
            durations[module] = max(float(value), 0.001)
    totals = [0.0] * SHARDS
    assignments: dict[str, int] = {}
    for module in sorted(durations, key=lambda name: (-durations[name], name)):
        index = min(range(SHARDS), key=lambda shard: (totals[shard], shard))
        assignments[module] = index + 1
        totals[index] += durations[module]
    return assignments


def selected_tests(collected: list[str], files: list[str], index: int, suite: str) -> list[str]:
    assignments = module_shards(suite)
    count = shard_count(suite)
    return [
        nodeid
        for nodeid in collected
        if (not files or nodeid.split("::")[0] in files)
        and assignments.get(nodeid.split("::")[0], shard_for(nodeid.split("::")[0], count)) == index
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


def affected_files(root: Path, event: str, base: str, *, worktree: bool = False) -> list[str]:
    """Only body or AST-neutral edits to unreferenced domain tests can narrow a run.

    Production, fixture, decorator, collection, config, addition/deletion and
    uncertain changes all run the full suite. No inferred application dependency
    graph can quietly miss an indirect consumer.
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
                or not path.startswith("backend/tests/domain/")
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
            if relative in unit_modules():
                raise ValueError("Non-Postgres owner always runs these tests")
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


def read_manifests(directory: Path, files: list[str], suite: str) -> tuple[list[str], int]:
    count = shard_count(suite)
    paths = sorted(directory.glob("*/collection-*.json"))
    if not paths:
        raise ValueError("Missing shard collection manifests")
    full: list[str] | None = None
    selected_by_shard: dict[int, list[str]] = {}
    for path in paths:
        manifest = load_object(path)
        collected = strings(manifest["collected"])
        selected = strings(manifest["selected"])
        index = manifest["shard"]
        if not isinstance(index, int) or not 1 <= index <= count:
            raise ValueError(f"Invalid shard in {path}")
        if manifest["count"] != count or manifest["files"] != files:
            raise ValueError(f"Inconsistent shard configuration in {path}")
        if full is not None and collected != full:
            raise ValueError("Workers/shards collected different test sets")
        full = collected
        expected = selected_tests(collected, files, index, suite)
        if selected != expected or not selected:
            raise ValueError(f"Incorrect or empty selection in {path}")
        if index in selected_by_shard and selected_by_shard[index] != selected:
            raise ValueError("Workers selected different test sets")
        selected_by_shard[index] = selected
    assert full is not None
    expected_all = [nodeid for nodeid in full if not files or nodeid.split("::")[0] in files]
    if Counter(nodeid for group in selected_by_shard.values() for nodeid in group) != Counter(
        expected_all
    ):
        raise ValueError("Shards dropped or duplicated collected tests")
    if files and set(files) != {nodeid.split("::")[0] for nodeid in expected_all}:
        raise ValueError("An affected module collected no tests")
    return expected_all, len(selected_by_shard)


def verify(directory: Path, files: list[str], suite: str) -> None:
    expected_all, shard_count = read_manifests(directory, files, suite)
    reports = sorted(directory.glob("*/report.xml"))
    if len(reports) != shard_count:
        raise ValueError("Missing or duplicate shard JUnit reports")
    cases = [case for report in reports for case in ET.parse(report).iter("testcase")]
    if Counter((case.get("classname", ""), case.get("name", "")) for case in cases) != Counter(
        case_key(nodeid) for nodeid in expected_all
    ):
        raise ValueError("JUnit reports dropped or duplicated selected tests")
    skipped = sum(case.find("skipped") is not None for case in cases)
    executed = len(cases) - skipped
    floor = 320 if suite == "schema" else (1 if files else 3000)
    if executed < floor or any(
        case.find("failure") is not None or case.find("error") is not None for case in cases
    ):
        raise ValueError("Tests failed, errored or did not reach the existing execution floor")
    if suite == "schema" and skipped:
        raise ValueError("Schema/RLS tests skipped")
    if suite == "suite" and not files:
        bao = [
            case
            for case in cases
            if case.get("classname", "").startswith(
                "tests.attestation.service.test_attestation_openbao"
            )
        ]
        if not bao or any(case.find("skipped") is not None for case in bao):
            raise ValueError("OpenBao tests were absent or skipped")
        print(f"OpenBao: {len(bao)} executed, 0 skipped")
    print(
        f"{suite}: {len(cases)} collected, {executed} executed, {skipped} skipped; "
        "exact shard coverage verified"
    )


def refresh_durations(directory: Path, source_run: str, output: Path) -> None:
    """Refresh only from complete, successfully executed full-suite evidence."""
    verify(directory, [], "suite")
    collected, _ = read_manifests(directory, [], "suite")
    modules = {case_key(nodeid): nodeid.split("::")[0] for nodeid in collected}
    durations: dict[str, float] = {}
    for report in sorted(directory.glob("*/report.xml")):
        for case in ET.parse(report).iter("testcase"):
            module = modules[(case.get("classname", ""), case.get("name", ""))]
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


class Arguments(argparse.Namespace):
    command: str
    root: Path
    directory: Path
    suite: str
    source_run: str
    output: Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    planning = sub.add_parser("plan")
    planning.add_argument("--root", type=Path, default=Path.cwd())
    checking = sub.add_parser("verify")
    checking.add_argument("directory", type=Path)
    checking.add_argument("--suite", choices=["suite", "schema"], required=True)
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
            args.suite,
        )


if __name__ == "__main__":
    main()
