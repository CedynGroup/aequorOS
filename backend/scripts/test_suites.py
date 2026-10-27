"""Explicit unit/integration ownership without moving feature-local test files.

The catalogue declares every module. Mixed modules can override a test function
(or class) by its unparametrized node ID. There is no default for new modules.
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

Suite = Literal["unit", "integration"]
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
    modules = raw["modules"]
    overrides = raw["overrides"]
    if any(
        value not in ("unit", "integration") for value in [*modules.values(), *overrides.values()]
    ):
        raise ValueError("Test suite must be unit or integration")
    if any(key.split("::")[0] not in modules or "::" not in key for key in overrides):
        raise ValueError("Test override must name a classified module and test")
    return cast(dict[str, Suite], modules), cast(dict[str, Suite], overrides)


def owner(nodeid: str) -> Suite:
    modules, overrides = catalogue()
    parts = nodeid.split("[", 1)[0].split("::")
    for length in range(len(parts), 1, -1):
        key = "::".join(parts[:length])
        if key in overrides:
            return overrides[key]
    try:
        return modules[parts[0]]
    except KeyError as error:
        raise ValueError(
            f"Unclassified test module: {parts[0]}; update scripts/test_suites.json"
        ) from error


def unit_modules() -> frozenset[str]:
    modules, overrides = catalogue()
    mixed = {nodeid.split("::")[0] for nodeid, suite in overrides.items() if suite == "integration"}
    return frozenset(
        module for module, suite in modules.items() if suite == "unit" and module not in mixed
    )


def case_key(nodeid: str) -> tuple[str, str]:
    address, bracket, params = nodeid.partition("[")
    module, *names = address.split("::")
    names[-1] += bracket + params
    return ".".join([module.removesuffix(".py").replace("/", "."), *names[:-1]]), names[-1]


def verify_partition(directory: Path) -> None:
    """Join unit, schema, suite, lock and storage reports against full collection."""
    classified = cast(
        dict[str, str], json.loads((directory / "unit-tests" / "collection.json").read_text())
    )
    if not classified or set(classified.values()) != {"unit", "integration"}:
        raise ValueError("Missing unit/integration classification evidence")
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
    if {report.parent.name for report in reports} != expected_reports:
        raise ValueError("Missing or unexpected CI execution owner report")
    actual: Counter[tuple[str, str]] = Counter()
    for report in reports:
        suite = "unit" if report.parent.name == "unit-tests" else "integration"
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
    omitted: set[str] = set()
    if files:
        for manifest in directory.glob("pg-suite-*/collection-*.json"):
            collected = cast(dict[str, list[str]], json.loads(manifest.read_text()))["collected"]
            omitted.update(nodeid for nodeid in collected if nodeid.split("::")[0] not in files)
    expected = Counter(case_key(nodeid) for nodeid in classified if nodeid not in omitted)
    if actual != expected:
        raise ValueError("CI owners dropped or duplicated classified tests")
    print(
        f"{sum(expected.values())} selected tests: exactly one unit/integration CI owner verified"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("reports", type=Path)
    arguments = parser.parse_args()
    verify_partition(cast(Path, arguments.reports))
