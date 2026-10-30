"""Explicit unit/integration/e2e ownership without moving feature-local test files.

The generated catalogue declares every parametrized test by its exact node ID.
There is no module default or implicit fallback for new tests.
This module is stdlib-only so CI planning and aggregation share the same owner.
"""

from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Literal, cast

Suite = Literal["unit", "integration", "e2e"]
CATALOGUE = Path(__file__).with_name("test_suites.json")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Double-classified catalogue entry")
    return result


@cache
def catalogue() -> tuple[dict[str, Suite], dict[str, Suite]]:
    raw = cast(
        dict[str, dict[str, str]],
        json.loads(CATALOGUE.read_text(), object_pairs_hook=_unique_object),
    )
    cases = raw["cases"]
    if not cases or any(value not in ("unit", "integration", "e2e") for value in cases.values()):
        raise ValueError("Test suite must be unit, integration or e2e")
    modules: dict[str, Suite] = {}
    for nodeid, suite in cases.items():
        if "::" not in nodeid:
            raise ValueError("Generated catalogue requires exact test node IDs")
        module = nodeid.split("::", 1)[0]
        previous = modules.get(module, "unit")
        modules[module] = (
            "integration"
            if "integration" in (previous, suite)
            else "e2e"
            if "e2e" in (previous, suite)
            else "unit"
        )
    return modules, cast(dict[str, Suite], cases)


def owner(nodeid: str) -> Suite:
    _, cases = catalogue()
    try:
        return cases[nodeid]
    except KeyError as error:
        raise ValueError(f"Unclassified test: {nodeid}; refresh the database census") from error


def unit_modules() -> frozenset[str]:
    modules, _ = catalogue()
    return frozenset(module for module, suite in modules.items() if suite == "unit")


def selected_modules(suite: str) -> list[str]:
    return sorted(
        {nodeid.split("::")[0] for nodeid, assigned in catalogue()[1].items() if assigned == suite}
    )


def verify_module_inventory(root: Path, paths: list[str], patterns: list[str]) -> None:
    """Inventory files without importing integration-only test modules.

    Actual collection checks every case in selected modules. The aggregate joins
    the other owners' collected identities, so a new, empty, deleted or excluded
    module cannot be hidden by the optimized unit selection.
    """
    discovered = {
        path.relative_to(root).as_posix()
        for directory in paths
        for pattern in patterns
        for path in (root / directory).rglob(pattern)
        if "__pycache__" not in path.parts
    }
    if discovered != set(catalogue()[0]):
        raise ValueError("Test modules on disk differ from the generated catalogue")


def case_key(nodeid: str) -> tuple[str, str]:
    address, bracket, params = nodeid.partition("[")
    module, *names = address.split("::")
    names[-1] += bracket + params
    return ".".join([module.removesuffix(".py").replace("/", "."), *names[:-1]]), names[-1]


def _verify_service_execution(reports: list[Path]) -> None:
    service_nodes = cast(dict[str, dict[str, str]], json.loads(CATALOGUE.read_text())).get(
        "services", {}
    )
    service_keys = {case_key(nodeid) for nodeid in service_nodes}
    if any(
        case.find("skipped") is not None
        for report in reports
        for case in ET.parse(report).iter("testcase")
        if (case.get("classname", ""), case.get("name", "")) in service_keys
    ):
        raise ValueError("External service contract skipped across CI owners")


def verify_partition(directory: Path) -> None:
    """Join unit, e2e, schema, suite, lock and storage reports against full collection."""
    classified = cast(
        dict[str, str], json.loads((directory / "unit-tests" / "collection.json").read_text())
    )
    if not classified or not {"unit", "integration"} <= set(classified.values()) <= {
        "unit",
        "integration",
        "e2e",
    }:
        raise ValueError("Missing unit/integration/e2e classification evidence")
    files = cast(list[str], json.loads(os.getenv("CI_POSTGRES_FILES", "[]")))
    indices = set(range(1, 5))
    if files:
        if __package__:
            from scripts.ci_postgres import module_shards, shard_for  # noqa: PLC0415
        else:
            from ci_postgres import module_shards, shard_for  # noqa: PLC0415
        assignments = module_shards()
        indices = {assignments.get(module, shard_for(module)) for module in files}
    reports = sorted(directory.glob("*/report.xml"))
    expected_reports = {
        "unit-tests",
        "pg-locks",
        "storage-tests",
        *(f"pg-suite-{index}" for index in indices),
        *(f"pg-schema-{index}" for index in range(1, 3)),
    }
    expected_reports.update({"e2e-tests"} if "e2e" in classified.values() else set())
    if {report.parent.name for report in reports} != expected_reports:
        raise ValueError("Missing or unexpected CI execution owner report")
    actual: Counter[tuple[str, str]] = Counter()
    for report in reports:
        suite = {"unit-tests": "unit", "e2e-tests": "e2e"}.get(report.parent.name, "integration")
        cases = list(ET.parse(report).iter("testcase"))
        identities = [(case.get("classname", ""), case.get("name", "")) for case in cases]
        allowed = {
            case_key(nodeid)
            for nodeid, assigned_suite in classified.items()
            if assigned_suite == suite
        }
        if not set(identities) <= allowed:
            raise ValueError(f"Wrong suite execution owner in {report}")
        if any(case.find(tag) is not None for case in cases for tag in ("error", "failure")):
            raise ValueError(f"Failed CI execution in {report}")
        actual.update(identities)
    _verify_service_execution(reports)
    omitted: set[str] = set()
    if files:
        for manifest in directory.glob("pg-suite-*/collection-*.json"):
            collected = cast(dict[str, list[str]], json.loads(manifest.read_text()))["collected"]
            omitted.update(nodeid for nodeid in collected if nodeid.split("::")[0] not in files)
    expected = Counter(case_key(nodeid) for nodeid in classified if nodeid not in omitted)
    if actual != expected:
        raise ValueError("CI owners dropped or duplicated classified tests")
    bao_keys = {
        case_key(nodeid)
        for nodeid in classified
        if nodeid.startswith("tests/attestation/service/test_attestation_openbao.py::")
        and nodeid not in omitted
    }
    bao_cases = [
        case
        for report in reports
        for case in ET.parse(report).iter("testcase")
        if (case.get("classname", ""), case.get("name", "")) in bao_keys
    ]
    if any(case.find("skipped") is not None for case in bao_cases):
        raise ValueError("OpenBao tests skipped across unit/integration owners")

    print(
        f"{sum(expected.values())} selected tests: exactly one unit/integration CI owner verified"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("reports", type=Path)
    arguments = parser.parse_args()
    verify_partition(cast(Path, arguments.reports))
