"""Filename-owned suites and runtime CI collection/execution coverage.

Feature folders locate tests; only the filename suffix assigns their suite.
This module stays stdlib-only for CI planning and report aggregation.
"""

from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, cast

if __package__:
    from scripts.database_contracts import check_classification, verify_execution
else:
    from database_contracts import check_classification, verify_execution

Suite = Literal["unit", "integration", "e2e"]
BACKEND = Path(__file__).resolve().parents[1]


def owner(nodeid: str) -> Suite:
    """Assign every case, including new parametrizations, from its module name."""
    filename = Path(nodeid.split("::", 1)[0]).name
    if filename.endswith("_integration.py"):
        return "integration"
    if filename.endswith("_e2e.py"):
        return "e2e"
    return "unit"


def selected_modules(
    suite: str,
    root: Path = BACKEND,
    paths: Sequence[str] = ("tests",),
    patterns: Sequence[str] = ("test_*.py", "*_test.py"),
) -> list[str]:
    return sorted(
        {
            path.relative_to(root).as_posix()
            for directory in paths
            for pattern in patterns
            for path in (root / directory).rglob(pattern)
            if "__pycache__" not in path.parts and path.is_file() and owner(str(path)) == suite
        }
    )


def case_key(nodeid: str) -> tuple[str, str]:
    address, bracket, params = nodeid.partition("[")
    module, *names = address.split("::")
    names[-1] += bracket + params
    return ".".join([module.removesuffix(".py").replace("/", "."), *names[:-1]]), names[-1]


def _verify_service_execution(reports: list[Path], service_nodes: list[str]) -> None:
    service_keys = {case_key(nodeid) for nodeid in service_nodes}
    if any(
        case.find("skipped") is not None
        for report in reports
        for case in ET.parse(report).iter("testcase")
        if (case.get("classname", ""), case.get("name", "")) in service_keys
    ):
        raise ValueError("External service contract skipped across CI owners")


def _collection(directory: Path) -> tuple[dict[str, object], dict[str, str]]:
    evidence = cast(
        dict[str, object],
        json.loads((directory / "unit-tests" / "all-tests.json").read_text()),
    )
    classified = cast(dict[str, str], evidence["collected"])
    if not classified or not {"unit", "integration"} <= set(classified.values()) <= {
        "unit",
        "integration",
        "e2e",
    }:
        raise ValueError("Missing unit/integration/e2e classification evidence")
    if any(owner(nodeid) != suite for nodeid, suite in classified.items()):
        raise ValueError("Collection disagrees with filename suite")
    return evidence, classified


def verify_partition(directory: Path, *, protected: Mapping[str, str] | None = None) -> None:
    """Join every CI report against this run's independent full-tree collection."""
    evidence, classified = _collection(directory)
    check_classification(classified, protected=protected)
    files = cast(list[str], json.loads(os.getenv("CI_POSTGRES_FILES", "[]")))
    if __package__:
        from scripts.ci_postgres import SHARDS, module_shards, shard_for  # noqa: PLC0415
    else:
        from ci_postgres import SHARDS, module_shards, shard_for  # noqa: PLC0415
    if files:
        assignments = module_shards()
        indices = {assignments.get(module, shard_for(module)) for module in files}
    else:
        indices = set(range(1, SHARDS + 1))
    # Pool shards write one report per phase (report-schema.xml / report-suite.xml);
    # every other owner writes report.xml.
    reports = sorted(directory.glob("*/report*.xml"))
    expected_reports = {
        "unit-tests",
        "pg-locks",
        *(f"pg-pool-{index}" for index in indices),
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
    _verify_service_execution(reports, cast(list[str], evidence["services"]))
    omitted: set[str] = set()
    if files:
        for manifest in directory.glob("pg-pool-*/collection-*.json"):
            collected = cast(dict[str, list[str]], json.loads(manifest.read_text()))["collected"]
            omitted.update(nodeid for nodeid in collected if nodeid.split("::")[0] not in files)
    expected = Counter(case_key(nodeid) for nodeid in classified if nodeid not in omitted)
    if actual != expected:
        raise ValueError("CI owners dropped or duplicated classified tests")
    verify_execution(
        reports,
        set(classified) - omitted,
        case_key,
        protected=protected,
    )
    bao_keys = {
        case_key(nodeid)
        for nodeid in classified
        if nodeid.startswith("tests/attestation/service/test_attestation_openbao_integration.py::")
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

    print(f"{sum(expected.values())} selected tests: exactly one unit/integration/e2e CI owner")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("reports", type=Path)
    arguments = parser.parse_args()
    verify_partition(cast(Path, arguments.reports))
