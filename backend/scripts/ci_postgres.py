"""Plan conservative PR selection and verify the complete PostgreSQL shard reports.

The CLI is stdlib-only: preflight and aggregate checks must work before mise.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import cast

SHARDS = 2
BAO_MODULE = "tests/services/test_attestation_openbao.py"


def shard_for(module: str, count: int = SHARDS) -> int:
    return int(hashlib.sha256(module.encode()).hexdigest(), 16) % count + 1


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


def affected_files(root: Path, event: str, base: str) -> list[str]:
    """Only existing, unreferenced domain-test bodies can safely narrow a PR.

    Production, fixture, decorator, collection, config, addition/deletion and
    uncertain changes all run the full suite. No inferred application dependency
    graph can quietly miss an indirect consumer.
    """
    try:
        if event != "pull_request" or not base:
            raise ValueError("Full suite required outside a known PR diff")
        if (
            subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD^2"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        ):
            base = git(root, "rev-parse", "HEAD^1").strip()
        changes = git(root, "diff", "--name-status", "--no-renames", base, "HEAD").splitlines()
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
            selected.append(module.relative_to("backend").as_posix())
        return sorted(selected)
    except (OSError, subprocess.CalledProcessError, SyntaxError, ValueError):
        return []


def plan(root: Path, event: str, base: str) -> dict[str, object]:
    files = affected_files(root, event, base)
    indices = sorted({shard_for(path) for path in files}) if files else list(range(1, SHARDS + 1))
    return {
        "mode": "affected" if files else "full",
        "files": files,
        "matrix": {
            "include": [
                {"shard": index, "openbao": not files and index == shard_for(BAO_MODULE)}
                for index in indices
            ]
        },
    }


def case_key(nodeid: str) -> tuple[str, str]:
    address, bracket, params = nodeid.partition("[")
    module, *names = address.split("::")
    names[-1] += bracket + params
    return ".".join([module.removesuffix(".py").replace("/", "."), *names[:-1]]), names[-1]


def read_manifests(directory: Path, files: list[str]) -> tuple[list[str], int]:
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
        if not isinstance(index, int) or not 1 <= index <= SHARDS:
            raise ValueError(f"Invalid shard in {path}")
        if manifest["count"] != SHARDS or manifest["files"] != files:
            raise ValueError(f"Inconsistent shard configuration in {path}")
        if full is not None and collected != full:
            raise ValueError("Workers/shards collected different test sets")
        full = collected
        expected = [
            nodeid
            for nodeid in collected
            if (not files or nodeid.split("::")[0] in files)
            and shard_for(nodeid.split("::")[0]) == index
        ]
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
    expected_all, shard_count = read_manifests(directory, files)
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
            if case.get("classname", "").startswith("tests.services.test_attestation_openbao")
        ]
        if not bao or any(case.find("skipped") is not None for case in bao):
            raise ValueError("OpenBao tests were absent or skipped")
        print(f"OpenBao: {len(bao)} executed, 0 skipped")
    print(
        f"{suite}: {len(cases)} collected, {executed} executed, {skipped} skipped; "
        "exact shard coverage verified"
    )


class Arguments(argparse.Namespace):
    command: str
    root: Path
    directory: Path
    suite: str


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    planning = sub.add_parser("plan")
    planning.add_argument("--root", type=Path, default=Path.cwd())
    checking = sub.add_parser("verify")
    checking.add_argument("directory", type=Path)
    checking.add_argument("--suite", choices=["suite", "schema"], required=True)
    args = parser.parse_args(namespace=Arguments())
    if args.command == "plan":
        result = plan(args.root, os.getenv("GITHUB_EVENT_NAME", ""), os.getenv("PR_BASE_SHA", ""))
        print(json.dumps(result))
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a") as stream:
                for key, value in result.items():
                    print(f"{key}={json.dumps(value) if key != 'mode' else value}", file=stream)
    else:
        verify(
            args.directory,
            strings(cast(object, json.loads(os.getenv("CI_POSTGRES_FILES", "[]")))),
            args.suite,
        )


if __name__ == "__main__":
    main()
