"""Required status checks must be emitted for the queued merge result."""

from __future__ import annotations

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


# The merge queue evaluates required checks on the queue head — the tree that
# actually lands. The type gate and the API-client freshness check used to
# exempt merge_group, so a merged-tree-only violation (base drift between the
# last PR-head run and the queue rebuild) was first caught only by the
# post-merge push run and main went red (issue #818). These gates must execute
# for merge groups; preflight's change detectors answer "true" there, so the
# only permitted `if:` guards are the preflight outputs. real-data is the
# deliberate exception: it cannot execute without the primary database.
MERGE_GROUP_GATES = {"Lint and typecheck", "Generated API client freshness"}
MERGE_GROUP_EXEMPT = {"Real-data API surface"}


def test_merge_group_gate_jobs_do_not_exempt_merge_groups() -> None:
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.load((WORKFLOWS / "risk-service.yml").read_text(), Loader=yaml.BaseLoader),
    )
    jobs_by_name = {
        job.get("name", job_id): job for job_id, job in document["jobs"].items()
    }
    for name in MERGE_GROUP_GATES | MERGE_GROUP_EXEMPT:
        assert name in jobs_by_name
    for name in MERGE_GROUP_GATES:
        condition = jobs_by_name[name].get("if")
        assert condition is not None and "merge_group" not in str(condition), name
        assert "needs.preflight.outputs" in str(condition), name
    for name in MERGE_GROUP_EXEMPT:
        assert "merge_group" in str(jobs_by_name[name].get("if", "")), name
