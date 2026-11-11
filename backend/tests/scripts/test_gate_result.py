"""The aggregate gate script turns a workflow's ``needs`` results into a verdict.

Both ``dashboard.yml`` and ``web.yml`` call the same script, so the verdict
rules live in one tested place: a failure or cancellation always fails the
gate, a skipped dependency is acceptable on a pull request (that is the whole
point of path filtering below the trigger), and a skipped dependency fails the
gate on a merge-queue entry — the queued merge result is what lands on main,
so its checks must actually run, and "skipped" must never read as success
there (that gap let the coverage ratchet fail on main after a green queue).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / ".github/scripts/gate_result.sh"

PASSED_BOTH = '{"changes":{"result":"success"},"dashboard":{"result":"success"}}'
SKIPPED_ON_PR = '{"changes":{"result":"success"},"dashboard":{"result":"skipped"}}'
SKIPPED_ALL = (
    '{"changes":{"result":"skipped"},'
    '"frontend":{"result":"skipped"},'
    '"console":{"result":"skipped"}}'
)
FAILED_ONE = '{"changes":{"result":"success"},"dashboard":{"result":"failure"}}'
CANCELLED_ONE = '{"changes":{"result":"success"},"console":{"result":"cancelled"}}'


def run_gate(needs: str, event: str, label: str = "Dashboard") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            "GATE_LABEL": label,
            "GATE_EVENT": event,
            "GATE_NEEDS": needs,
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_every_dependency_passing_passes_the_gate_on_the_queue() -> None:
    result = run_gate(PASSED_BOTH, "merge_group")

    assert result.returncode == 0, result.stderr
    assert "Every Dashboard job passed" in result.stdout


def test_a_failed_dependency_fails_the_gate_on_any_event() -> None:
    for event in ("pull_request", "merge_group", "push"):
        result = run_gate(FAILED_ONE, event)
        assert result.returncode == 1, (event, result.stdout, result.stderr)
        assert "did not pass" in result.stderr


def test_a_cancelled_dependency_fails_the_gate() -> None:
    result = run_gate(CANCELLED_ONE, "pull_request")

    assert result.returncode == 1, result.stderr


def test_a_skipped_dependency_is_acceptable_on_a_pull_request() -> None:
    result = run_gate(SKIPPED_ON_PR, "pull_request")

    assert result.returncode == 0, result.stderr
    assert "passed or was skipped" in result.stdout


def test_a_skipped_dependency_fails_the_gate_on_the_queue() -> None:
    for needs in (SKIPPED_ON_PR, SKIPPED_ALL):
        result = run_gate(needs, "merge_group")
        assert result.returncode == 1, (needs, result.stdout, result.stderr)
        assert "merge-queue" in result.stderr
        assert "skipped" in result.stderr


def test_the_queue_verdict_names_the_offending_jobs() -> None:
    result = run_gate(SKIPPED_ALL, "merge_group", label="Web")

    assert result.returncode == 1, result.stderr
    assert "frontend: skipped" in result.stderr
    assert "console: skipped" in result.stderr


def test_the_gate_refuses_to_run_without_its_inputs() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
