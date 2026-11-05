"""Behavioral controls for runtime resource evidence, independent of membership."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.database_observation_check import check, observations


@pytest.mark.parametrize(
    "mutation", ["missing-session", "missing-report", "duplicate", "orphan", "child"]
)
def test_observer_rejects_incomplete_or_ambiguous_evidence(tmp_path: Path, mutation: str) -> None:
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
    _ = (tmp_path / "events-1.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    if mutation == "child":
        evidence = observations(tmp_path)[nodeid]
        assert not evidence.database
        assert not evidence.confirmed_children
        with pytest.raises(ValueError, match="Unit resource boundary"):
            check(tmp_path)
    else:
        with pytest.raises(ValueError):
            observations(tmp_path)


@pytest.mark.parametrize("source", ["direct", "fixture", "service"])
@pytest.mark.parametrize("suite", ["unit", "integration", "e2e"])
@pytest.mark.parametrize("passed", [False, True])
def test_runtime_resources_do_not_reassign_suite(
    tmp_path: Path, source: str, suite: str, passed: bool
) -> None:
    suffix = "" if suite == "unit" else "_" + suite
    nodeid = f"tests/test_probe{suffix}.py::test_probe"
    events: list[dict[str, object]] = [
        {"kind": "session_start", "pid": 1},
        {"kind": "selected", "pid": 1, "nodeids": [nodeid]},
        {"kind": "consumes", "pid": 1, "nodeid": nodeid, "fixtures": ["shared"]},
        *[
            {
                "kind": "report",
                "pid": 1,
                "nodeid": nodeid,
                "when": when,
                "outcome": "passed" if passed or when != "call" else "skipped",
            }
            for when in ("setup", "call", "teardown")
        ],
        {"kind": "session_finish", "pid": 1},
    ]
    events.append(
        {"kind": "database", "pid": 1, "nodeid": nodeid}
        if source == "direct"
        else {"kind": "database", "pid": 1, "fixtures": ["shared"]}
        if source == "fixture"
        else {"kind": "service", "pid": 1, "fixtures": ["shared"], "service": "minio"}
    )
    _ = (tmp_path / "events-1.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    evidence = observations(tmp_path)[nodeid]
    assert evidence.database == (source != "service")
    assert evidence.services == (frozenset({"minio"}) if source == "service" else frozenset())
    assert evidence.passed == passed
    if suite == "unit" or (source == "service" and not passed):
        with pytest.raises(ValueError):
            check(tmp_path)
    else:
        check(tmp_path)
