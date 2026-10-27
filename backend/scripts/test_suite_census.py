"""Generate or verify exact test ownership from database observation artifacts.

A skipped test proves neither database use nor database freedom. Such cases
remain integration-owned until an admissible execution proves otherwise; their
identities and reasons are printed separately, never inferred to be unit tests.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import cast

BACKEND = Path(__file__).resolve().parents[1]
CATALOGUE = Path(__file__).with_name("test_suites.json")


def classify(directory: Path) -> tuple[dict[str, str], dict[str, str]]:
    events: list[dict[str, object]] = []
    for path in sorted(directory.rglob("events-*.jsonl")):
        events.extend(
            cast(dict[str, object], json.loads(line)) for line in path.read_text().splitlines()
        )
    if not events:
        raise ValueError("No database observation evidence")
    sessions = {cast(int, e["pid"]) for e in events if e["kind"] == "session_start"}
    finished = {cast(int, e["pid"]) for e in events if e["kind"] == "session_finish"}
    if not sessions or sessions != finished:
        raise ValueError("Incomplete pytest observation session")
    selections: dict[int, list[str]] = {}
    reports: dict[str, list[tuple[str, str]]] = defaultdict(list)
    consumers: dict[str, set[str]] = defaultdict(set)
    direct: set[str] = set()
    fixture_database: set[str] = set()
    instrumented = {cast(int, e["pid"]) for e in events if e["kind"] == "instrumented"}
    uncertain: set[str] = set()
    for event in events:
        kind = event["kind"]
        nodeid = cast(str, event.get("nodeid", ""))
        if kind == "selected":
            selections[cast(int, event["pid"])] = cast(list[str], event["nodeids"])
        elif kind == "report":
            reports[nodeid].append((cast(str, event["when"]), cast(str, event["outcome"])))
        elif kind == "consumes":
            consumers[nodeid].update(cast(list[str], event["fixtures"]))
        elif kind == "database":
            fixtures = cast(list[str], event.get("fixtures", []))
            fixture_database.update(fixtures)
            if nodeid:
                direct.add(nodeid)
            elif not fixtures:
                raise ValueError("Database use outside a test or tracked fixture")
        elif kind == "python_child" and event["child"] not in instrumented:
            if not nodeid:
                raise ValueError("Uninstrumented Python child outside a test")
            uncertain.add(nodeid)
    if set(selections) != sessions:
        raise ValueError("Missing selected-test observation manifest")
    # xdist workers each collect the same selection. Execution reports, rather
    # than summing their identical manifests, prove that each case ran once.
    expected = set().union(*(set(nodes) for nodes in selections.values()))
    if set(reports) != expected:
        raise ValueError("Observation reports dropped or added selected test identities")
    if any(len(nodes) != len(set(nodes)) for nodes in selections.values()):
        raise ValueError("Duplicate selected test identity")
    cases: dict[str, str] = {}
    inconclusive: dict[str, str] = {}
    for nodeid in sorted(expected):
        phases = reports[nodeid]
        counts = Counter(when for when, _ in phases)
        if any(count > 1 for count in counts.values()):
            raise ValueError(f"Duplicate execution observation: {nodeid}")
        database = nodeid in direct or bool(consumers[nodeid] & fixture_database)
        passed = dict(phases) == {"setup": "passed", "call": "passed", "teardown": "passed"}
        if not passed or nodeid in uncertain:
            inconclusive[nodeid] = (
                "unconfirmed Python child" if nodeid in uncertain else str(dict(phases))
            )
        cases[nodeid] = "integration" if database or not passed or nodeid in uncertain else "unit"
    return cases, inconclusive


def generate(directory: Path, output: Path) -> None:
    cases, inconclusive = classify(directory)
    output.write_text(json.dumps({"cases": cases}, indent=2, sort_keys=True) + "\n")
    print(f"Generated {len(cases)} test owners: {dict(Counter(cases.values()))}")
    print(f"{len(inconclusive)} inconclusive cases conservatively owned by integration")
    (directory / "inconclusive.json").write_text(json.dumps(inconclusive, indent=2) + "\n")


def check(directory: Path, catalogue: Path = CATALOGUE) -> None:
    observed, inconclusive = classify(directory)
    expected = cast(dict[str, dict[str, str]], json.loads(catalogue.read_text()))["cases"]
    # An affected-module CI run checks exactly its observed selection. The
    # independent partition guard already proves which cases may be omitted.
    differences = {
        nodeid: (expected.get(nodeid), suite)
        for nodeid, suite in observed.items()
        if expected.get(nodeid) != suite
    }
    if differences:
        raise ValueError(
            "Database ownership drift; refresh the census:\n" + json.dumps(differences, indent=2)
        )
    print(
        f"Verified observed ownership of {len(observed)} tests; {len(inconclusive)} inconclusive integration cases"
    )


def refresh(directory: Path, mode: str) -> int:
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Refresh requires an empty observation directory")
    directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    for key in (
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "WORKER_DATABASE_URL",
        "REAL_DATA_DATABASE_URL",
        "TEST_DATABASE_URL",
        "TEST_DATABASE_ADMIN_URL",
        "CI_UNIT_REPORT_DIR",
        "CI_NO_DATABASE_ATTEMPTS",
        "CI_POSTGRES_REPORT_DIR",
        "CI_POSTGRES_SHARD",
        "CI_POSTGRES_FILES",
        "PYTEST_ADDOPTS",
    ):
        environment.pop(key, None)
    environment.update(CI_DATABASE_OBSERVATIONS=str(directory.resolve()), CI_DATABASE_CENSUS="1")
    # Explicit mode provisions disposable services even if CI or a developer's
    # environment carries a database URL. Never use an external/primary URL.
    result = subprocess.run(
        [
            sys.executable,
            str(BACKEND / "scripts/local_services.py"),
            "run",
            "--mode",
            mode,
            "--postgres",
            "--storage",
            "--role-admin",
            "--",
            sys.executable,
            "-m",
            "pytest",
            "--junitxml=" + str(directory / "report.xml"),
        ],
        cwd=BACKEND,
        env=environment,
        check=False,
    )
    # Failed/skipped cases retain integration ownership; a collection error or
    # interruption has no complete evidence and classify() refuses generation.
    if result.returncode not in (0, 1):
        return result.returncode
    generate(directory, CATALOGUE)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["refresh", "generate", "check"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, default=CATALOGUE)
    parser.add_argument("--mode", choices=["native", "docker"], default="native")
    arguments = parser.parse_args()
    directory = cast(Path, arguments.directory)
    if arguments.action == "refresh":
        return refresh(directory, cast(str, arguments.mode))
    if arguments.action == "generate":
        generate(directory, cast(Path, arguments.output))
    else:
        check(directory, cast(Path, arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
