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
            case ast.Name(id="true"):
                return True
            case ast.Name(id="false"):
                return False
            case ast.BoolOp(op=ast.And(), values=operands):
                return all(evaluate(operand) for operand in operands)
            case ast.BoolOp(op=ast.Or(), values=operands):
                return any(evaluate(operand) for operand in operands)
            case ast.Compare(left=left, ops=[ast.Eq()], comparators=[right]):
                return evaluate(left) == evaluate(right)
            case ast.Compare(left=left, ops=[ast.NotEq()], comparators=[right]):
                return evaluate(left) != evaluate(right)
            case _:
                raise AssertionError(f"Unsupported condition: {condition}")

    result = evaluate(tree.body)
    assert isinstance(result, bool)
    return result


@pytest.mark.parametrize("event,preflight,enabled", product(
    ["merge_group", "pull_request", "push"], ["true", "false"], ["true", "false"]
))
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
        "needs.preflight.outputs.api_client": preflight,
        "vars.REAL_DATA_CI_ENABLED": enabled,
    }
    for name in ("Lint and typecheck", "Generated API client freshness"):
        assert _condition_runs(jobs[name]["if"], values) == (preflight == "true"), name
    assert _condition_runs(jobs["Real-data API surface"]["if"], values) == (
        event != "merge_group" and preflight == "true" and enabled == "true"
    )


def test_disabled_gate_condition_is_rejected() -> None:
    assert not _condition_runs(
        "false && needs.preflight.outputs.backend == 'true'",
        {"needs.preflight.outputs.backend": "true"},
    )
