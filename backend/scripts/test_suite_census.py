"""Generate or verify exact test ownership from database observation artifacts.

A skipped test proves neither database use nor database freedom. Such cases
remain integration-owned until an admissible execution proves otherwise; their
identities and reasons are printed separately, never inferred to be unit tests.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from urllib.request import urlopen

BACKEND = Path(__file__).resolve().parents[1]
CATALOGUE = Path(__file__).with_name("test_suites.json")


def _process(event: dict[str, object], key: str = "pid") -> str:
    return f"{event['namespace']}:{event[key]}"


def _fixtures(event: dict[str, object]) -> list[str]:
    return [f"{event['namespace']}:{key}" for key in cast(list[str], event.get("fixtures", []))]


def classify(directory: Path) -> tuple[dict[str, str], dict[str, str]]:  # noqa: PLR0912, PLR0915
    events: list[dict[str, object]] = []
    for path in sorted(directory.rglob("events-*.jsonl")):
        for line in path.read_text().splitlines():
            event = cast(dict[str, object], json.loads(line))
            # Artifacts preserve each CI owner's directory. PIDs and fixture
            # serials are meaningful only inside that execution owner.
            event["namespace"] = str(path.parent.resolve())
            events.append(event)
    if not events:
        raise ValueError("No database observation evidence")
    sessions = {_process(e) for e in events if e["kind"] == "session_start"}
    finished = {_process(e) for e in events if e["kind"] == "session_finish"}
    if not sessions or sessions != finished:
        raise ValueError("Incomplete pytest observation session")
    selections: dict[str, list[str]] = {}
    reports: dict[str, list[tuple[str, str]]] = defaultdict(list)
    consumers: dict[str, set[str]] = defaultdict(set)
    direct: set[str] = set()
    fixture_database: set[str] = set()
    instrumented = {_process(e) for e in events if e["kind"] == "instrumented"}
    uncertain: set[str] = set()
    uncertain_fixtures: set[str] = set()
    for event in events:
        kind = event["kind"]
        nodeid = cast(str, event.get("nodeid", ""))
        if kind == "selected":
            selections[_process(event)] = cast(list[str], event["nodeids"])
        elif kind == "report":
            reports[nodeid].append((cast(str, event["when"]), cast(str, event["outcome"])))
        elif kind == "consumes":
            consumers[nodeid].update(_fixtures(event))
        elif kind == "database":
            fixtures = _fixtures(event)
            fixture_database.update(fixtures)
            if nodeid and not fixtures:
                direct.add(nodeid)
            elif not nodeid and not fixtures:
                raise ValueError("Database use outside a test or tracked fixture")
        elif kind == "python_child" and _process(event, "child") not in instrumented:
            fixtures = _fixtures(event)
            if fixtures:
                uncertain_fixtures.update(fixtures)
            elif nodeid:
                uncertain.add(nodeid)
            else:
                raise ValueError("Uninstrumented Python child outside a test")
    if set(selections) != sessions:
        raise ValueError("Missing selected-test observation manifest")
    # xdist workers each collect the same selection. Execution reports, rather
    # than summing their identical manifests, prove that each case ran once.
    expected = set[str]().union(*(set(nodes) for nodes in selections.values()))
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
        phase_map = dict(phases)
        required = {"setup", "teardown"}
        if phase_map.get("setup") == "passed":
            required.add("call")
        if set(phase_map) != required:
            raise ValueError(f"Incomplete execution phases: {nodeid}")
        database = nodeid in direct or bool(consumers[nodeid] & fixture_database)
        if consumers[nodeid] & uncertain_fixtures:
            uncertain.add(nodeid)
        passed = dict(phases) == {"setup": "passed", "call": "passed", "teardown": "passed"}
        if not database and (not passed or nodeid in uncertain):
            inconclusive[nodeid] = (
                "unconfirmed Python child" if nodeid in uncertain else str(dict(phases))
            )
        cases[nodeid] = "integration" if database or not passed or nodeid in uncertain else "unit"
    return cases, inconclusive


def generate(directory: Path, output: Path, supplement: Path | None = None) -> None:
    cases, inconclusive = classify(directory)
    if supplement is not None:
        extra, unresolved = classify(supplement)
        if not set(extra) <= set(cases):
            raise ValueError("Supplement contains tests outside the full census")
        for nodeid, suite in extra.items():
            if nodeid in unresolved:
                continue
            if nodeid not in inconclusive and cases[nodeid] != suite:
                raise ValueError(f"Conflicting database evidence across profiles: {nodeid}")
            cases[nodeid] = suite
            _ = inconclusive.pop(nodeid, None)
    _ = output.write_text(json.dumps({"cases": cases}, indent=2, sort_keys=True) + "\n")
    print(f"Generated {len(cases)} test owners: {dict(Counter(cases.values()))}")
    print(f"{len(inconclusive)} inconclusive cases conservatively owned by integration")
    _ = (directory / "inconclusive.json").write_text(json.dumps(inconclusive, indent=2) + "\n")


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
        f"Verified observed ownership of {len(observed)} tests; "
        + f"{len(inconclusive)} inconclusive integration cases"
    )


@contextmanager
def openbao(directory: Path) -> Generator[dict[str, str]]:
    """Run the census's own disposable loopback signing service."""
    binary = os.getenv("AQS_OPENBAO_BIN") or shutil.which("bao")
    if not binary:
        raise ValueError("Install OpenBao or set AQS_OPENBAO_BIN to the verified bao binary")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = cast(int, listener.getsockname()[1])
    address = f"http://127.0.0.1:{port}"
    token = "census-" + uuid.uuid4().hex
    with (directory / "openbao.log").open("w") as log:
        process = subprocess.Popen(
            [binary, "server", "-dev", f"-dev-listen-address=127.0.0.1:{port}"],
            env={**os.environ, "BAO_DEV_ROOT_TOKEN_ID": token},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise ValueError("Census OpenBao exited during startup; inspect openbao.log")
                try:
                    with urlopen(address + "/v1/sys/health", timeout=1):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise ValueError("Census OpenBao did not become ready") from None
                    time.sleep(0.2)
            yield {"OPENBAO_TEST_ADDR": address, "OPENBAO_TEST_TOKEN": token}
        finally:
            process.terminate()
            process.wait(timeout=20)


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
        "LIVE_DATA_DATABASE_URL",
        "OPENBAO_TEST_ADDR",
        "OPENBAO_TEST_TOKEN",
        "TEST_DATABASE_URL",
        "TEST_DATABASE_ADMIN_URL",
        "CI_UNIT_REPORT_DIR",
        "CI_NO_DATABASE_ATTEMPTS",
        "CI_POSTGRES_REPORT_DIR",
        "CI_POSTGRES_SHARD",
        "CI_POSTGRES_FILES",
        "PYTEST_ADDOPTS",
    ):
        _ = environment.pop(key, None)
    environment.update(CI_DATABASE_OBSERVATIONS=str(directory.resolve()), CI_DATABASE_CENSUS="1")
    # Explicit mode provisions disposable services even if CI or a developer's
    # environment carries a database URL. Never use an external/primary URL.
    with openbao(directory) as bao_environment:
        environment.update(bao_environment)
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
    _ = parser.add_argument("action", choices=["refresh", "generate", "check"])
    _ = parser.add_argument("directory", type=Path)
    _ = parser.add_argument("--output", type=Path, default=CATALOGUE)
    _ = parser.add_argument("--supplement", type=Path)
    _ = parser.add_argument("--mode", choices=["native", "docker"], default="native")
    arguments = parser.parse_args()
    directory = cast(Path, arguments.directory)
    action = cast(str, arguments.action)
    if action == "refresh":
        return refresh(directory, cast(str, arguments.mode))
    if action == "generate":
        generate(directory, cast(Path, arguments.output), cast(Path | None, arguments.supplement))
    else:
        check(directory, cast(Path, arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
