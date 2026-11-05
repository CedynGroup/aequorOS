"""Behavioral controls for evidence-based suite ownership.

This module's other-suite cases live in ``test_database_observation_integration.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.test_suite_census import check, classify, generate


@pytest.mark.parametrize(
    "mutation", ["missing-session", "missing-report", "duplicate", "orphan", "child"]
)
def test_census_rejects_incomplete_or_ambiguous_evidence(tmp_path: Path, mutation: str) -> None:
    nodeid = "tests/test_probe.py::test_probe"
    events: list[dict[str, object]] = [
        {"kind": "session_start", "pid": 1},
        {"kind": "selected", "pid": 1, "nodeids": [nodeid]},
        *[
            {"kind": "report", "pid": 1, "nodeid": nodeid, "when": when, "outcome": "passed"}
            for when in ("setup", "call", "teardown")
        ],
        {"kind": "session_finish", "pid": 1},
    ]
    if mutation == "missing-session":
        events.pop()
    elif mutation == "missing-report":
        events = [e for e in events if e.get("when") != "call"]
    elif mutation == "duplicate":
        events.append(events[2])
    elif mutation == "orphan":
        events.append({"kind": "database", "pid": 1})
    else:
        events.append({"kind": "python_child", "pid": 1, "nodeid": nodeid, "child": 2})
    (tmp_path / "events-1.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    if mutation == "child":
        cases, inconclusive = classify(tmp_path)
        assert cases == {nodeid: "integration"}
        assert set(inconclusive) == {nodeid}
        _assert_supplement_preserves_acquisitions(tmp_path)
    else:
        with pytest.raises(ValueError):
            classify(tmp_path)


def _write_census_profile(directory: Path, source: str, outcome: str) -> list[str]:
    directory.mkdir()
    nodes = ["tests/test_probe.py::test_first", "tests/test_probe.py::test_second"]
    events: list[dict[str, object]] = [
        {"kind": "session_start", "pid": 1},
        {"kind": "selected", "pid": 1, "nodeids": nodes},
        {"kind": "session_finish", "pid": 1},
    ]
    for node in nodes:
        for phase in ("setup", "call", "teardown"):
            if phase == "call" and outcome in {"setup", "skipped"}:
                continue
            status = "failed" if phase == outcome else "passed"
            if phase == "setup" and outcome == "skipped":
                status = "skipped"
            events.append(
                {"kind": "report", "pid": 1, "nodeid": node, "when": phase, "outcome": status}
            )
        if source == "fixture":
            events.append({"kind": "consumes", "pid": 1, "nodeid": node, "fixtures": ["shared"]})
        elif source in {"direct", "child"}:
            events.append(
                {
                    "kind": "database",
                    "pid": 2 if source == "child" else 1,
                    "nodeid": node,
                    "entrypoint": "psycopg.connect",
                }
            )
        if outcome == "child":
            events.append({"kind": "python_child", "pid": 1, "nodeid": node, "child": 3})
    if source == "fixture":
        events.append({"kind": "database", "pid": 1, "fixtures": ["shared"]})
    elif source == "child":
        events.extend(
            [
                {"kind": "python_child", "pid": 1, "nodeid": nodes[0], "child": 2},
                {"kind": "instrumented", "pid": 2},
            ]
        )
    _ = (directory / "events-1.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n"
    )
    return nodes


def _assert_supplement_preserves_acquisitions(tmp_path: Path) -> None:
    unit = tmp_path / "proven-unit"
    nodes = _write_census_profile(unit, "none", "passed")
    units = dict.fromkeys(nodes, "unit")
    integrations = dict.fromkeys(nodes, "integration")
    output = tmp_path / "generated.json"
    assert classify(unit) == (units, {})
    for suite in ("integration", "e2e"):
        for outcome in ("passed", "skipped"):
            service = tmp_path / f"service-{suite}-{outcome}"
            _ = _write_census_profile(service, "none", outcome)
            with (service / "events-1.jsonl").open("a") as stream:
                for node in nodes:
                    stream.write(
                        json.dumps(
                            {"kind": "consumes", "pid": 1, "nodeid": node, "fixtures": ["service"]}
                        )
                        + "\n"
                    )
                stream.write(
                    json.dumps(
                        {"kind": "service", "pid": 1, "fixtures": ["service"], "suite": suite}
                    )
                    + "\n"
                )
            owners = dict.fromkeys(nodes, suite)
            assert classify(service) == (owners, {})
            generate(unit, output, services=service)
            assert json.loads(output.read_text()) == {"cases": owners, "services": owners}
            check(service, output)
            generate(unit, output)
            with pytest.raises(ValueError, match="ownership drift"):
                check(service, output)
    extension = tmp_path / "additional-tests"
    _ = _write_census_profile(extension, "direct", "passed")
    events = extension / "events-1.jsonl"
    _ = events.write_text(events.read_text().replace("test_probe.py", "test_added.py"))
    added = {node.replace("test_probe.py", "test_added.py"): "integration" for node in nodes}
    generate(unit, output, extension=extension)
    assert json.loads(output.read_text()) == {"cases": units | added}
    with pytest.raises(ValueError, match="Extension overlaps"):
        generate(unit, output, extension=unit)
    for source in ("direct", "fixture", "child"):
        for outcome in ("passed", "setup", "call", "teardown", "skipped", "child"):
            positive = tmp_path / f"positive-{source}-{outcome}"
            _ = _write_census_profile(positive, source, outcome)
            assert classify(positive) == (integrations, {})
            unresolved = tmp_path / f"unresolved-{source}-{outcome}"
            _ = _write_census_profile(
                unresolved, "none", outcome if outcome != "passed" else "child"
            )
            assert classify(unresolved)[0] == integrations
            assert set(classify(unresolved)[1]) == set(nodes)
            for full, supplement in ((positive, unit), (unit, positive)):
                with pytest.raises(ValueError, match="Conflicting database evidence"):
                    generate(full, output, supplement)
            for full, supplement in ((positive, unresolved), (unresolved, positive)):
                generate(full, output, supplement)
                assert json.loads(output.read_text()) == {"cases": integrations}
                assert json.loads((full / "inconclusive.json").read_text()) == {}
            for full, supplement in ((unit, unresolved), (unresolved, unit)):
                generate(full, output, supplement)
                assert json.loads(output.read_text()) == {"cases": units}
            generate(positive, output)
            check(positive, output)
            generate(unit, output)
            with pytest.raises(ValueError, match="ownership drift"):
                check(positive, output)
