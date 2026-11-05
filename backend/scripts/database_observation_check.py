"""Validate observed database/service safety independently of suite membership."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

if __package__:
    from scripts.test_suites import owner
else:
    from test_suites import owner


@dataclass(frozen=True)
class Observation:
    database: bool
    services: frozenset[str]
    passed: bool
    confirmed_children: bool


def _process(event: dict[str, object], key: str = "pid") -> str:
    return f"{event['namespace']}:{event[key]}"


def _fixtures(event: dict[str, object]) -> list[str]:
    return [f"{event['namespace']}:{key}" for key in cast(list[str], event.get("fixtures", []))]


def _events(directory: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for path in sorted(directory.rglob("events-*.jsonl")):
        for line in path.read_text().splitlines():
            event = cast(dict[str, object], json.loads(line))
            # PIDs and fixture serials are local to each execution owner.
            event["namespace"] = str(path.parent.resolve())
            events.append(event)
    sessions = {_process(e) for e in events if e["kind"] == "session_start"}
    finished = {_process(e) for e in events if e["kind"] == "session_finish"}
    if not events:
        raise ValueError("No database observation evidence")
    if not sessions or sessions != finished:
        raise ValueError("Incomplete pytest observation session")
    return events


@dataclass
class _Acquisitions:
    consumers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    direct: set[str] = field(default_factory=set)
    fixture_database: set[str] = field(default_factory=set)
    services: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    fixture_services: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    uncertain: set[str] = field(default_factory=set)
    uncertain_fixtures: set[str] = field(default_factory=set)

    def record(self, event: dict[str, object], instrumented: set[str]) -> None:
        kind = event["kind"]
        nodeid = cast(str, event.get("nodeid", ""))
        fixtures = _fixtures(event)
        if kind == "consumes":
            self.consumers[nodeid].update(fixtures)
        elif kind == "database":
            self.fixture_database.update(fixtures)
            if nodeid and not fixtures:
                self.direct.add(nodeid)
            elif not nodeid and not fixtures:
                raise ValueError("Database use outside a test or tracked fixture")
        elif kind == "service":
            self.record_service(nodeid, fixtures, cast(str, event["service"]))
        elif kind == "python_child" and _process(event, "child") not in instrumented:
            if fixtures:
                self.uncertain_fixtures.update(fixtures)
            elif nodeid:
                self.uncertain.add(nodeid)
            else:
                raise ValueError("Uninstrumented Python child outside a test")

    def record_service(self, nodeid: str, fixtures: list[str], service: str) -> None:
        if fixtures:
            for fixture in fixtures:
                self.fixture_services[fixture].add(service)
        elif nodeid:
            self.services[nodeid].add(service)
        else:
            raise ValueError("Service use outside a test or tracked fixture")

    def summarize(self, nodeid: str, phases: list[tuple[str, str]]) -> Observation:
        if any(count > 1 for count in Counter(when for when, _ in phases).values()):
            raise ValueError(f"Duplicate execution observation: {nodeid}")
        phase_map = dict(phases)
        required = {"setup", "teardown"}
        if phase_map.get("setup") == "passed":
            required.add("call")
        if set(phase_map) != required:
            raise ValueError(f"Incomplete execution phases: {nodeid}")
        external = set(self.services[nodeid])
        for fixture in self.consumers[nodeid]:
            external.update(self.fixture_services[fixture])
        return Observation(
            database=nodeid in self.direct or bool(self.consumers[nodeid] & self.fixture_database),
            services=frozenset(external),
            passed=phase_map == {"setup": "passed", "call": "passed", "teardown": "passed"},
            confirmed_children=not (
                nodeid in self.uncertain or self.consumers[nodeid] & self.uncertain_fixtures
            ),
        )


def observations(directory: Path) -> dict[str, Observation]:
    events = _events(directory)
    acquisitions = _Acquisitions()
    instrumented = {_process(e) for e in events if e["kind"] == "instrumented"}
    selections: dict[str, list[str]] = {}
    reports: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for event in events:
        acquisitions.record(event, instrumented)
        if event["kind"] == "selected":
            selections[_process(event)] = cast(list[str], event["nodeids"])
        elif event["kind"] == "report":
            reports[cast(str, event["nodeid"])].append(
                (cast(str, event["when"]), cast(str, event["outcome"]))
            )
    if set(selections) != {_process(e) for e in events if e["kind"] == "session_start"}:
        raise ValueError("Missing selected-test observation manifest")
    # Workers repeat the collection; execution reports prove every case ran once.
    expected = set[str]().union(*(set(nodes) for nodes in selections.values()))
    if set(reports) != expected:
        raise ValueError("Observation reports dropped or added selected test identities")
    if any(len(nodes) != len(set(nodes)) for nodes in selections.values()):
        raise ValueError("Duplicate selected test identity")
    return {nodeid: acquisitions.summarize(nodeid, reports[nodeid]) for nodeid in sorted(expected)}


def check(directory: Path) -> None:
    """Check runtime resource safety, never generate or change suite membership."""
    observed = observations(directory)
    for nodeid, evidence in observed.items():
        if owner(nodeid) == "unit" and (
            evidence.database or evidence.services or not evidence.confirmed_children
        ):
            raise ValueError(f"Unit resource boundary violated: {nodeid}")
        if evidence.services and not evidence.passed:
            raise ValueError(f"External service contract did not execute successfully: {nodeid}")
    print(f"Verified runtime resource boundaries for {len(observed)} tests")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    check(cast(Path, arguments.directory))
