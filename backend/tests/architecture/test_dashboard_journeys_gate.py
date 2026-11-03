"""Exercise the executable required-check contract with Actions job results."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

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
    ("changed_paths", "expected"),
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
        ("backend/tests/bi/fixtures.py", "true"),
        ("backend/tests/fixtures/live_plane.py", "true"),
        ("backend/tests/fixtures/reference_data.py", "true"),
        ("backend/tests/fixtures/icaap/frameworks/gh/test_icaap_e2e.json", "true"),
        ("backend/tests/support/factories/canonical.py", "true"),
        ("backend/tests/support/factories/reconciliation.py", "true"),
        ("backend/tests/market_data/adapters/manual_upload/fixtures.py", "true"),
        ("packages/shared/index.ts", "true"),
        ("backend/.python-version", "true"),
        ("backend/package.json", "true"),
        ("backend/dashboard/package.json", "true"),
        ("frontend/package.json", "true"),
        ("console/package.json", "true"),
        ("package.json", "true"),
        ("pnpm-lock.yaml", "true"),
        ("backend/uv.lock", "true"),
        ("pnpm-workspace.yaml", "true"),
        ("new-runtime/config.toml", "true"),
        ("frontend/new-build-hook.js", "true"),
        (".github/workflows/web.yml", "true"),
        (".github/actions/new-action/action.yml", "true"),
        (".github/scripts/new-script.sh", "true"),
        ("backend/app/ai/service/consent/ai-consent-2026-09-v1.md", "true"),
        ("backend/dashboard/public/branding/aequoros-mark.png", "true"),
        (".github/workflows/dashboard-journeys.yml", "true"),
        (".github/actions/setup-minio/action.yml", "true"),
        ("README.md", "false"),
        ("LICENSE", "false"),
        ("docs/product.md", "false"),
        ("backend/docs/transport_security.md", "false"),
        ("backend/dashboard/README.md", "false"),
        ("frontend/app/page.tsx", "false"),
        ("frontend/components/Navigation.tsx", "false"),
        ("frontend/lib/product-screens.ts", "false"),
        ("frontend/public/images/founder.jpg", "false"),
        (("README.md", "docs/product.md", "frontend/app/page.tsx"), "false"),
        (("README.md", "backend/.python-version"), "true"),
        (("frontend/app/page.tsx", "frontend/package.json"), "true"),
        (("frontend/public/images/founder.jpg", "console/package.json"), "true"),
    ],
)
def test_workflow_change_detector_selects_its_inputs(
    tmp_path: Path, changed_paths: str | tuple[str, ...], expected: str
) -> None:
    """Run the workflow's configured detector against a real disposable diff."""
    paths = (changed_paths,) if isinstance(changed_paths, str) else changed_paths
    output = _run_change_detector(tmp_path, paths, _workflow_detector_command())
    assert output == f"journeys={expected}"


@pytest.mark.parametrize(
    ("event", "base_state"),
    [
        ("pull_request", "valid"),
        ("pull_request", "missing"),
        ("pull_request", "invalid"),
        ("push", "valid"),
        ("workflow_dispatch", "valid"),
        ("", "valid"),
    ],
)
def test_journey_detector_runs_for_empty_or_unknown_decisions(
    tmp_path: Path, event: str, base_state: str
) -> None:
    paths = () if base_state == "valid" and event == "pull_request" else ("README.md",)
    command = _workflow_detector_command()
    if base_state == "missing":
        command = f"PR_BASE_SHA='' {command}"
    elif base_state == "invalid":
        command = f"PR_BASE_SHA=not-a-revision {command}"
    output = _run_change_detector(tmp_path, paths, command, event=event)
    assert output == "journeys=true"


@pytest.mark.parametrize(
    ("old_path", "new_path", "expected"),
    [
        ("backend/runtime.py", "docs/runtime.md", "true"),
        ("docs/runtime.md", "backend/runtime.py", "true"),
        ("docs/old.md", "docs/new.md", "false"),
    ],
)
def test_journey_detector_considers_both_rename_paths(
    tmp_path: Path, old_path: str, new_path: str, expected: str
) -> None:
    output = _run_change_detector(
        tmp_path, (new_path,), _workflow_detector_command(), original_path=old_path
    )
    assert output == f"journeys={expected}"


@pytest.mark.parametrize(
    ("changed_path", "expected"),
    [("frontend/package.json", "true"), ("frontend/app/page.tsx", "false")],
)
def test_journey_detector_classifies_deletions(
    tmp_path: Path, changed_path: str, expected: str
) -> None:
    output = _run_change_detector(
        tmp_path, (), _workflow_detector_command(), original_path=changed_path
    )
    assert output == f"journeys={expected}"


@pytest.mark.parametrize(
    ("workflow_step", "changed_path"),
    [
        (("dashboard.yml", "changes", "areas", "dashboard"), "backend/dashboard/app/page.tsx"),
        (("risk-service.yml", "preflight", "backend", "backend"), "backend/app/main.py"),
        (("risk-service.yml", "preflight", "guards", "guards"), ".github/workflows/web.yml"),
        (
            ("risk-service.yml", "preflight", "api_client", "api_client"),
            "packages/risk-service-api/index.ts",
        ),
        (("web.yml", "changes", "areas", "web"), "frontend/app/page.tsx"),
    ],
)
@pytest.mark.parametrize("selected", [True, False])
def test_other_workflows_keep_inclusion_selection(
    tmp_path: Path, workflow_step: tuple[str, str, str, str], changed_path: str, selected: bool
) -> None:
    workflow, job, step_id, output_name = workflow_step
    path = changed_path if selected else "README.md"
    output = _run_change_detector(
        tmp_path, (path,), _workflow_detector_command(workflow, step_id, job)
    )
    assert output == f"{output_name}={str(selected).lower()}"


@pytest.mark.parametrize(
    ("workflow", "job", "step_id", "output_name"),
    [
        ("dashboard.yml", "changes", "areas", "dashboard"),
        ("risk-service.yml", "preflight", "backend", "backend"),
        ("risk-service.yml", "preflight", "guards", "guards"),
        ("risk-service.yml", "preflight", "api_client", "api_client"),
        ("web.yml", "changes", "areas", "web"),
        ("dashboard-journeys.yml", "changes", "areas", "journeys"),
    ],
)
def test_merge_group_runs_every_area_even_for_a_docs_only_diff(
    tmp_path: Path, workflow: str, job: str, step_id: str, output_name: str
) -> None:
    output = _run_change_detector(
        tmp_path,
        ("README.md",),
        _workflow_detector_command(workflow, step_id, job),
        event="merge_group",
    )
    assert output == f"{output_name}=true"


def _workflow_detector_command(
    workflow: str = "dashboard-journeys.yml", step_id: str = "areas", job: str = "changes"
) -> str:
    repo = GATE.parents[2]
    document = cast(
        dict[str, dict[str, dict[str, object]]],
        yaml.load((repo / ".github/workflows" / workflow).read_text(), Loader=yaml.BaseLoader),
    )
    steps = cast(list[dict[str, object]], document["jobs"][job]["steps"])
    step = next(step for step in steps if step.get("id") == step_id)
    return cast(str, step["run"])


def _run_change_detector(
    tmp_path: Path,
    changed_paths: tuple[str, ...],
    command: str,
    *,
    event: str = "pull_request",
    original_path: str | None = None,
) -> str:
    repo = GATE.parents[2]
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
    if original_path:
        initial = tmp_path / original_path
        initial.parent.mkdir(parents=True, exist_ok=True)
        initial.write_text("changed\n")
    git("add", "--force", ".")
    git("commit", "-qm", "baseline")
    base = git("rev-parse", "HEAD")
    for changed_path in changed_paths:
        changed = tmp_path / changed_path
        changed.parent.mkdir(parents=True, exist_ok=True)
        if original_path:
            (tmp_path / original_path).rename(changed)
        else:
            changed.write_text("changed\n")
    if original_path and not changed_paths:
        (tmp_path / original_path).unlink()
    git("add", "--force", ".")
    git("commit", "--allow-empty", "-qm", "change")
    output = tmp_path / "outputs"
    subprocess.run(
        ["bash", "-c", command],
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": event,
            "PR_BASE_SHA": base,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return output.read_text().strip()
