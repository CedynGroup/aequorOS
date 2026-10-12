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
        ("risk-service.yml", {"Preflight", "Architecture and CI guards", "Risk service gate"}),
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
