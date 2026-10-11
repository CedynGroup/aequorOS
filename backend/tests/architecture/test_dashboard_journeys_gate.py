"""Exercise the executable required-check contract with Actions job results."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

GATE = Path(__file__).parents[3] / ".github/scripts/dashboard_journeys_gate.sh"


@pytest.mark.parametrize(
    ("detection", "changed", "journeys", "accepted"),
    [
        ("success", "true", "success", True),
        ("success", "false", "skipped", True),
        ("success", "true", "failure", False),
        ("success", "true", "cancelled", False),
        ("success", "true", "skipped", False),
        ("success", "false", "success", False),
        ("success", "false", "failure", False),
        ("success", "", "skipped", False),
        ("success", "unknown", "success", False),
        ("failure", "false", "skipped", False),
        ("cancelled", "true", "success", False),
        ("skipped", "false", "skipped", False),
    ],
)
def test_required_check_accepts_only_verified_results(
    detection: str, changed: str, journeys: str, accepted: bool
) -> None:
    results = {
        "changes": {"result": detection, "outputs": {"journeys": changed}},
        "journeys": {"result": journeys},
    }
    result = subprocess.run(
        ["bash", str(GATE)],
        env={**os.environ, "RESULTS": json.dumps(results)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr


@pytest.mark.parametrize("results", ["{}", "null", "not json"])
def test_required_check_refuses_missing_or_malformed_results(results: str) -> None:
    result = subprocess.run(
        ["bash", str(GATE)],
        env={**os.environ, "RESULTS": results},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    ("changed_path", "expected"),
    [
        ("backend/dashboard/app/page.tsx", "true"),
        ("backend/dashboard/playwright.config.ts", "true"),
        ("backend/dashboard/e2e/global-setup.ts", "true"),
        ("backend/dashboard/e2e/support/mint.ts", "true"),
        ("backend/dashboard/e2e/support/runtime-lock.ts", "true"),
        ("backend/dashboard/e2e/support/runtime-ports.ts", "true"),
        ("backend/dashboard/e2e/support/quarantine-reporter.ts", "true"),
        ("backend/app/main.py", "true"),
        ("backend/alembic/versions/migration.py", "true"),
        ("backend/scripts/e2e_bootstrap.py", "true"),
        ("backend/scripts/e2e_idp.py", "true"),
        ("backend/scripts/local_services.py", "true"),
        ("backend/tests/__init__.py", "true"),
        ("backend/tests/fixtures/canonical_bank_fixture.py", "true"),
        ("backend/tests/fixtures/bi_plane.py", "true"),
        ("backend/tests/fixtures/live_plane.py", "true"),
        ("backend/tests/fixtures/reference_data.py", "true"),
        ("backend/tests/fixtures/icaap/frameworks/gh/test_icaap_e2e.json", "true"),
        ("backend/tests/support/factories/canonical.py", "true"),
        ("backend/tests/support/factories/reconciliation.py", "true"),
        ("backend/tests/adapters/market_data/manual_upload/fixtures.py", "true"),
        ("packages/shared/index.ts", "true"),
        ("pnpm-lock.yaml", "true"),
        (".github/workflows/dashboard-journeys.yml", "true"),
        (".github/actions/setup-minio/action.yml", "true"),
        ("README.md", "false"),
        ("frontend/app/page.tsx", "false"),
    ],
)
def test_workflow_change_detector_selects_its_inputs(
    tmp_path: Path, changed_path: str, expected: str
) -> None:
    """Run the workflow's configured detector against a real disposable diff."""
    repo = GATE.parents[2]
    document = yaml.load(
        (repo / ".github/workflows/dashboard-journeys.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    step = next(step for step in document["jobs"]["changes"]["steps"] if step.get("id") == "areas")
    detector = tmp_path / ".github/scripts/changed_area.sh"
    detector.parent.mkdir(parents=True)
    shutil.copy2(repo / ".github/scripts/changed_area.sh", detector)

    def git(*args: str) -> str:
        return subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                *args,
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "baseline")
    base = git("rev-parse", "HEAD")
    changed = tmp_path / changed_path
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("changed\n")
    git("add", ".")
    git("commit", "-qm", "change")
    output = tmp_path / "outputs"
    subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": "pull_request",
            "PR_BASE_SHA": base,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert output.read_text().strip() == f"journeys={expected}"
