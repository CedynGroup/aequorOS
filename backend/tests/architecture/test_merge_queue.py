"""Required status checks must be emitted for the queued merge result."""

from __future__ import annotations

import ast
from itertools import product
from pathlib import Path
from typing import cast

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


@pytest.mark.parametrize(
    ("workflow", "checks"),
    [
        ("secret-scan.yml", {"gitleaks"}),
        ("risk-service.yml", {"Preflight", "Unit tests", "Risk service gate"}),
        ("dashboard.yml", {"Dashboard gate"}),
        ("web.yml", {"Web gate"}),
        ("dashboard-journeys.yml", {"Dashboard journeys gate"}),
    ],
)
def test_required_checks_run_for_merge_groups(workflow: str, checks: set[str]) -> None:
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.load((WORKFLOWS / workflow).read_text(), Loader=yaml.BaseLoader),
    )
    # BaseLoader preserves the Actions 'on' key rather than YAML 1.1's boolean.
    triggers = document["on"]
    assert "pull_request" in triggers
    assert triggers["merge_group"] == {"types": ["checks_requested"]}
    names = {job.get("name", job_id) for job_id, job in document["jobs"].items()}
    assert checks <= names


def _condition_runs(condition: str, values: dict[str, str]) -> bool:
    expression = condition.removeprefix("${{").removesuffix("}}").strip()
    for field, value in values.items():
        expression = expression.replace(field, repr(value))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    tree = ast.parse(expression.strip(), mode="eval")

    def evaluate(node: ast.AST) -> str | bool:
        match node:
            case ast.Constant(value=value) if isinstance(value, (str, bool)):
                return value
            case ast.Name(id=name) if name in {"true", "false"}:
                return name == "true"
            case ast.BoolOp(op=operator, values=operands):
                results = [evaluate(operand) for operand in operands]
                return all(results) if isinstance(operator, ast.And) else any(results)
            case ast.Compare(left=left, ops=[comparator], comparators=[right]):
                left_value = evaluate(left)
                right_value = evaluate(right)
                if isinstance(comparator, ast.Eq):
                    return left_value == right_value
                return left_value != right_value
            case _:
                raise AssertionError(f"Unsupported condition: {condition}")

    result = evaluate(tree.body)
    assert isinstance(result, bool)
    return result


@pytest.mark.parametrize(
    "event,preflight,enabled",
    product(["merge_group", "pull_request", "push"], ["true", "false"], ["true", "false"]),
)
def test_queue_head_execution_policy(event: str, preflight: str, enabled: str) -> None:
    """Issue 818 requires selected queue gates and excludes primary-data access."""
    document = cast(
        dict[str, dict[str, dict[str, str]]],
        yaml.load((WORKFLOWS / "risk-service.yml").read_text(), Loader=yaml.BaseLoader),
    )
    jobs = {job.get("name", key): job for key, job in document["jobs"].items()}
    values = {
        "github.event_name": event,
        "needs.preflight.outputs.backend": preflight,
        "needs.preflight.outputs.guards": preflight,
        "needs.preflight.outputs.api_client": preflight,
        "vars.REAL_DATA_CI_ENABLED": enabled,
    }
    assert _condition_runs(jobs["Lint and typecheck"]["if"], values) == (preflight == "true")
    assert _condition_runs(jobs["Real-data API surface"]["if"], values) == (
        event != "merge_group" and preflight == "true" and enabled == "true"
    )


@pytest.mark.parametrize(
    ("workflow", "area", "real_jobs"),
    [
        ("dashboard.yml", "dashboard", ["dashboard"]),
        ("web.yml", "web", ["frontend", "console"]),
    ],
)
def test_queue_entries_run_the_real_jobs_and_the_gate_refuses_a_skip(
    workflow: str, area: str, real_jobs: list[str]
) -> None:
    """A queued merge result lands on main, so its filtered checks are owed there.

    The dashboard and web jobs used to skip on ``merge_group`` while their gate
    accepted ``skipped`` as success, so those checks first ran on main — the
    coverage ratchet failed there after a green queue. The wiring now relies on
    the detector (which answers ``true`` for every non-pull_request event) and
    the gate script (which rejects a skipped dependency on the queue).
    """
    document = cast(
        dict[str, dict[str, object]],
        yaml.load((WORKFLOWS / workflow).read_text(), Loader=yaml.BaseLoader),
    )
    jobs = cast(dict[str, dict[str, object]], document["jobs"])
    assert "if" not in jobs["changes"], "queue entries rely on the detector, not an event filter"
    for job_id in real_jobs:
        condition = cast(str, jobs[job_id]["if"])
        assert "github.event_name" not in condition
        output = f"needs.changes.outputs.{area}"
        assert _condition_runs(condition, {output: "true"}) is True
        assert _condition_runs(condition, {output: "false"}) is False
    gate = cast(dict[str, object], jobs["gate"])
    assert gate["if"] == "always()"
    step = next(iter(cast(list[dict[str, object]], gate["steps"])))
    assert "gate_result.sh" in cast(str, step["run"])
    env = cast(dict[str, str], step["env"])
    assert env["GATE_EVENT"] == "${{ github.event_name }}"


def test_disabled_gate_condition_is_rejected() -> None:
    assert not _condition_runs(
        "false && needs.preflight.outputs.backend == 'true'",
        {"needs.preflight.outputs.backend": "true"},
    )
