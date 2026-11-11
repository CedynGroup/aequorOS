"""Unit tests for the CI flake measurement script (.github/scripts/ci_flake_report.py).

The script's pure logic (log parsing, signature extraction, attempt comparison,
classification, ranking, thresholds) is exercised here against recorded GitHub
Actions fixtures; no network access happens in this module. The I/O layer
(``gh api`` subprocesses) is deliberately not covered offline.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ci_flake_report"
SCRIPT_PATH = Path(__file__).resolve().parents[3] / ".github" / "scripts" / "ci_flake_report.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_flake_report", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve forward references through sys.modules on first use.
    sys.modules["ci_flake_report"] = module
    spec.loader.exec_module(module)
    return module


flake_report: ModuleType = _load_script()

REPORT_META: dict[str, Any] = {
    "repo": "CedynGroup/aequorOS",
    "workflow_files": ["risk-service.yml"],
    "since": "2026-10-11",
    "commands": "python3 .github/scripts/ci_flake_report.py report",
    "generated_at": "2026-11-09T12:00:00Z",
}


def fixture_text(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()


def fixture_json(name: str) -> dict:
    with (FIXTURES / name).open() as handle:
        return json.load(handle)


def job(name: str, conclusion: str, minutes: float, job_id: int = 1):
    """Build a JobRecord the way the API payload parser would."""
    return flake_report.JobRecord(
        job_id=job_id,
        name=name,
        conclusion=conclusion,
        minutes=minutes,
    )


def event(signature: str, **overrides: Any) -> Any:
    """Build a FlakeEvent with defaults for the fields most tests don't care about."""
    values: dict[str, Any] = {
        "signature": signature,
        "kind": "test",
        "workflow": "Risk Service CI",
        "run_id": 42,
        "event": "pull_request",
        "branch": "feature/a",
        "sha": "a" * 40,
        "job": "Dashboard journeys shard 4/4",
        "attempt": 1,
        "minutes": 10.0,
        "rerun_minutes": 0.0,
        "rerun": False,
        "same_sha_pass": False,
        "created_at": "2026-11-08T10:00:00Z",
        "test_names": [],
    }
    values.update(overrides)
    if values["rerun"] and not values["rerun_minutes"]:
        values["rerun_minutes"] = 8.0
    return flake_report.FlakeEvent(**values)


# --- log signature extraction -------------------------------------------------


def test_pytest_log_signature_extraction() -> None:
    findings = flake_report.extract_findings(fixture_text("pytest_doc_deletion.log"))
    signatures = [f.signature for f in findings]
    assert "pytest:tests/db/test_document_deletion_timeout_integration.py" in signatures
    doc_deletion = next(
        f
        for f in findings
        if f.signature == "pytest:tests/db/test_document_deletion_timeout_integration.py"
    )
    assert sorted(doc_deletion.test_names) == [
        "test_cleanup_deletes_real_minio_object_and_reruns_safely",
        "test_deletion_commits_before_storage_and_retries_under_role_timeouts",
        "test_upload_completion_cannot_resurrect_deletion_during_head[True-True]",
    ]
    assert all(f.kind == "test" for f in findings)


def test_playwright_log_signature_extraction() -> None:
    findings = flake_report.extract_findings(fixture_text("playwright_rule_status.log"))
    rule_status = [f for f in findings if f.signature.startswith("playwright:")]
    assert len(rule_status) == 1
    assert rule_status[0].signature == (
        "playwright:tests/submissions/regulatory-rule-status.e2e.spec.ts"
    )
    assert rule_status[0].test_names == [
        "status disclosure remains reachable when its badge scrolls out of view"
    ]
    assert rule_status[0].kind == "test"


def test_typecheck_baseline_signature_extraction() -> None:
    findings = flake_report.extract_findings(fixture_text("typecheck_baseline.log"))
    signatures = {f.signature for f in findings}
    assert (
        "typecheck:app.adapters.database_direct.drivers.snowflake:reportUnknownMemberType"
        in signatures
    )
    assert "typecheck:app.services.attestation.signers:reportUnknownVariableType" in signatures
    assert all(f.kind == "typecheck" for f in findings)


def test_infrastructure_download_failure_classified() -> None:
    findings = flake_report.extract_findings(fixture_text("setup_minio_504.log"))
    infra = [f for f in findings if f.kind == "infra"]
    assert len(infra) == 1
    assert infra[0].signature == "infra:download-curl-22-504"


def test_nonfatal_retry_lines_not_counted() -> None:
    findings = flake_report.extract_findings(fixture_text("passing_download_retry.log"))
    assert findings == []


def test_clean_lines_strips_timestamps_and_ansi() -> None:
    cleaned = flake_report.clean_lines(
        "\x1b[32m2026-11-08T08:56:49.2016680Z curl: (22) boom\x1b[0m"
    )
    assert cleaned == ["curl: (22) boom"]


# --- attempt comparison -------------------------------------------------------


def test_rerun_pass_is_classified_flaky() -> None:
    attempts = {
        1: [
            job("Dashboard journeys shard 4/4", "failure", 26.4, 7),
            job("Dashboard journeys shard 1/4", "success", 29.3, 8),
        ],
        4: [
            job("Dashboard journeys shard 4/4", "success", 21.6, 9),
            job("Dashboard journeys shard 1/4", "success", 29.3, 10),
        ],
    }
    rerun_passes = flake_report.compare_attempts(attempts)
    assert len(rerun_passes) == 1
    assert rerun_passes[0].failed_job.name == "Dashboard journeys shard 4/4"
    assert rerun_passes[0].failed_attempt == 1
    assert rerun_passes[0].passed_attempt == 4


def test_consistent_failure_is_not_flaky() -> None:
    attempts = {
        1: [job("Dashboard journeys shard 4/4", "failure", 20.0)],
        2: [job("Dashboard journeys shard 4/4", "failure", 20.0)],
    }
    assert flake_report.compare_attempts(attempts) == []


def test_aggregate_jobs_excluded() -> None:
    assert flake_report.is_aggregate_job("risk-service gate")
    assert flake_report.is_aggregate_job("Postgres schema, RLS and locks")
    assert flake_report.is_aggregate_job("Integration tests")
    assert flake_report.is_aggregate_job("Detect journey changes")
    assert not flake_report.is_aggregate_job("Postgres schema shard 2/2")
    assert not flake_report.is_aggregate_job("Dashboard journeys shard 4/4")


# --- same-SHA mixed outcomes --------------------------------------------------


def test_same_sha_mixed_outcomes_detected() -> None:
    runs = [
        flake_report.RunRecord(
            run_id=1,
            workflow="Dashboard Playwright journeys",
            event="pull_request",
            head_branch="feature/a",
            head_sha="abc123",
            run_attempt=1,
            conclusion="failure",
            created_at="2026-11-08T10:00:00Z",
        ),
        flake_report.RunRecord(
            run_id=2,
            workflow="Dashboard Playwright journeys",
            event="push",
            head_branch="main",
            head_sha="abc123",
            run_attempt=1,
            conclusion="success",
            created_at="2026-11-08T12:00:00Z",
        ),
    ]
    jobs_by_run = {
        1: [job("Dashboard journeys shard 4/4", "failure", 20.0)],
        2: [job("Dashboard journeys shard 4/4", "success", 20.0)],
    }
    mixed = flake_report.same_sha_mixed_outcomes(runs, jobs_by_run)
    assert [(m.failed_run_id, m.job_name) for m in mixed] == [(1, "Dashboard journeys shard 4/4")]


def test_merge_group_skipped_jobs_excluded() -> None:
    runs = [
        flake_report.RunRecord(
            run_id=1,
            workflow="Dashboard CI",
            event="pull_request",
            head_branch="feature/a",
            head_sha="abc123",
            run_attempt=1,
            conclusion="failure",
            created_at="2026-11-08T10:00:00Z",
        ),
        flake_report.RunRecord(
            run_id=2,
            workflow="Dashboard CI",
            event="merge_group",
            head_branch="gh-readonly-queue/main/pr-1-x",
            head_sha="abc123",
            run_attempt=1,
            conclusion="success",
            created_at="2026-11-08T12:00:00Z",
        ),
    ]
    jobs_by_run = {
        1: [job("dashboard", "failure", 20.0)],
        # On merge_group the dashboard job is skipped and the gate passes.
        2: [job("dashboard", "skipped", 0.0)],
    }
    assert flake_report.same_sha_mixed_outcomes(runs, jobs_by_run) == []


def test_fail_fail_pass_is_one_rerun_pass() -> None:
    attempts = {
        1: [job("Dashboard journeys shard 4/4", "failure", 20.0, 11)],
        2: [job("Dashboard journeys shard 4/4", "failure", 20.0, 12)],
        3: [job("Dashboard journeys shard 4/4", "success", 20.0, 13)],
    }
    passes = flake_report.compare_attempts(attempts)
    assert len(passes) == 1
    assert passes[0].failed_attempt == 1
    assert passes[0].passed_attempt == 3


def test_fail_pass_fail_later_failure_is_not_a_rerun() -> None:
    run = flake_report.RunRecord(
        run_id=7,
        workflow="Dashboard Playwright journeys",
        event="pull_request",
        head_branch="feature/a",
        head_sha="abc123",
        run_attempt=3,
        conclusion="failure",
        created_at="2026-11-08T10:00:00Z",
    )
    failed_jobs = [
        (run, 1, job("Dashboard journeys shard 4/4", "failure", 20.0, 21)),
        (run, 2, job("Dashboard journeys shard 4/4", "success", 20.0, 22)),
        (run, 3, job("Dashboard journeys shard 4/4", "failure", 20.0, 23)),
    ]
    rerun_passes = {
        (7, 1, "Dashboard journeys shard 4/4"): flake_report.RerunPass(
            failed_job=failed_jobs[0][2],
            failed_attempt=1,
            passed_attempt=2,
            passed_minutes=20.0,
        ),
    }
    signature = "playwright:tests/x.e2e.spec.ts"
    findings_by_job = {
        21: [flake_report.Finding("test", signature, ["t"])],
        23: [flake_report.Finding("test", signature, ["t"])],
    }
    events = flake_report._build_events(failed_jobs, rerun_passes, set(), findings_by_job)
    failures = [e for e in events if e.attempt != 2]
    assert len(failures) == 2
    by_attempt = {e.attempt: e for e in failures}
    assert by_attempt[1].rerun is True
    assert by_attempt[3].rerun is False
    assert by_attempt[3].rerun_minutes == 0.0


# --- ranking ------------------------------------------------------------------


def test_ranking_orders_by_wasted_minutes_then_count() -> None:
    events = [
        event("flake:A", minutes=30.0),
        event("flake:A", minutes=30.0),
        event("flake:B", minutes=90.0),
    ]
    rows = flake_report.build_ranking(events, {})
    assert [row["signature"] for row in rows] == ["flake:B", "flake:A"]
    assert rows[0]["lost_minutes"] == 90.0
    assert rows[1]["occurrences"] == 2


def test_per_event_rates() -> None:
    events = [
        event("flake:A", event="merge_group", run_id=41),
        event("flake:A", event="merge_group", run_id=42),
        # A run that failed the signature twice counts once in the rate.
        event("flake:A", event="merge_group", run_id=41),
        event("flake:A", event="pull_request", run_id=43),
    ]
    denominators = {
        ("Risk Service CI", "merge_group"): 64,
        ("Risk Service CI", "pull_request"): 100,
        ("Risk Service CI", "push"): 36,
    }
    row = flake_report.build_ranking(events, denominators)[0]
    assert row["event_counts"] == {"merge_group": 3, "pull_request": 1}
    assert row["event_rates"]["merge_group"] == {"failed": 2, "runs": 64, "rate": 0.0312}
    assert row["event_rates"]["pull_request"] == {
        "failed": 1,
        "runs": 100,
        "rate": 0.01,
    }
    assert row["event_rates"]["push"] == {"failed": 0, "runs": 36, "rate": 0.0}


def test_rerun_and_branch_information_in_ranking() -> None:
    events = [
        event("flake:A", rerun=True),
        event("flake:A", branch="feature/b", rerun=False, same_sha_pass=True),
    ]
    row = flake_report.build_ranking(events, {})[0]
    assert row["rerun_count"] == 1
    assert row["same_sha_count"] == 1
    assert row["branch_count"] == 2
    assert row["total_minutes"] == pytest.approx(10.0 + 8.0 + 10.0)


def test_deterministic_branch_breakage_marked() -> None:
    events = [
        event("flake:branch-only", branch="feature/break", rerun=False),
        event("flake:branch-only", branch="feature/break", rerun=False),
    ]
    row = flake_report.build_ranking(events, {})[0]
    assert row["classification"] == "branch-breakage"
    assert row["branch_count"] == 1


def test_main_or_queue_hits_counted() -> None:
    events = [
        event("flake:A", event="merge_group"),
        event("flake:A", event="push", branch="main"),
        event("flake:A", event="pull_request", branch="feature/c"),
    ]
    row = flake_report.build_ranking(events, {})[0]
    assert row["main_or_queue_hits"] == 2
    assert row["classification"] == "flake"


# --- weekly thresholds (design for the weekly mode) ---------------------------


def test_thresholds_need_issue_at_two_fix_or_quarantine_at_three() -> None:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    recent = "2026-11-08T10:00:00Z"
    two = [event("flake:a", rerun=True, created_at=recent) for _ in range(2)]
    three = [event("flake:x", rerun=True, created_at=recent) for _ in range(3)]
    old = event("flake:old", rerun=True, created_at="2026-10-01T10:00:00Z")

    breaches = flake_report.evaluate_thresholds([*two, old], now)
    assert len(breaches) == 1
    assert breaches[0].action == "needs flaky-test issue with owner"

    breaches = flake_report.evaluate_thresholds([*three, old], now)
    assert len(breaches) == 1
    assert breaches[0].action == "fix or quarantine within 7 days"

    assert flake_report.evaluate_thresholds([old], now) == []


# --- report assembly ----------------------------------------------------------


def test_baseline_rates() -> None:
    runs = [
        flake_report.RunRecord(
            run_id=1,
            workflow="Risk Service CI",
            event="push",
            head_branch="main",
            head_sha="a",
            run_attempt=2,
            conclusion="success",
            created_at="2026-11-08T10:00:00Z",
        ),
        flake_report.RunRecord(
            run_id=2,
            workflow="Risk Service CI",
            event="push",
            head_branch="main",
            head_sha="b",
            run_attempt=1,
            conclusion="success",
            created_at="2026-11-08T11:00:00Z",
        ),
        flake_report.RunRecord(
            run_id=3,
            workflow="Risk Service CI",
            event="pull_request",
            head_branch="feature/a",
            head_sha="c",
            run_attempt=1,
            conclusion="failure",
            created_at="2026-11-08T12:00:00Z",
        ),
        flake_report.RunRecord(
            run_id=4,
            workflow="Risk Service CI",
            event="merge_group",
            head_branch="gh-readonly-queue/main/pr-1-x",
            head_sha="d",
            run_attempt=1,
            conclusion="success",
            created_at="2026-11-08T13:00:00Z",
        ),
    ]
    flaky = event("flake:A", event="pull_request", run_id=3)
    rates = flake_report.baseline_rates(runs, [flaky])
    entry = rates["Risk Service CI"]
    assert entry["total_runs"] == 4
    assert entry["rerun_runs"] == 1
    assert entry["flaky_failed_runs"] == 1
    assert entry["rerun_rate"] == 0.25
    assert entry["flaky_first_attempt_rate"] == 0.25
    assert entry["baseline_flake_rate"] == 0.5


def test_markdown_renders_ranking_and_baseline() -> None:
    runs = [
        flake_report.RunRecord(
            run_id=1,
            workflow="Risk Service CI",
            event="push",
            head_branch="main",
            head_sha="a",
            run_attempt=1,
            conclusion="success",
            created_at="2026-11-08T10:00:00Z",
        )
    ]
    events = [event("pytest:tests/db/test_document_deletion_timeout_integration.py")]
    report = flake_report.build_report(runs=runs, events=events, meta=REPORT_META)
    markdown = flake_report.render_markdown(report)
    assert "# CI flake ranking" in markdown
    assert "pytest:tests/db/test_document_deletion_timeout_integration.py" in markdown
    assert "baseline" in markdown.lower()
    assert "python3 .github/scripts/ci_flake_report.py report" in markdown
    # The window reports what the data covers, not what was requested.
    assert report["meta"]["window"]["since"] == "2026-11-08"
    assert report["meta"]["window"]["requested_since"] == "2026-10-11"


def test_report_json_round_trips() -> None:
    runs = [
        flake_report.RunRecord(
            run_id=1,
            workflow="Risk Service CI",
            event="push",
            head_branch="main",
            head_sha="a",
            run_attempt=1,
            conclusion="success",
            created_at="2026-11-08T10:00:00Z",
        )
    ]
    report = flake_report.build_report(
        runs=runs,
        events=[event("flake:A", rerun=True, test_names=["t1"])],
        meta=REPORT_META,
    )
    encoded = json.dumps(report)
    assert "flake:A" in encoded
    assert report["ranking"][0]["rerun_count"] == 1


def test_rerun_run_merges_test_names_across_findings() -> None:
    run = flake_report.RunRecord(
        run_id=9,
        workflow="Risk Service CI",
        event="pull_request",
        head_branch="feature/a",
        head_sha="abc123",
        run_attempt=2,
        conclusion="success",
        created_at="2026-11-08T10:00:00Z",
    )
    events = [
        event("pytest:tests/a.py", run_id=9, rerun=True, test_names=["t_a"]),
        event("pytest:tests/b.py", run_id=9, rerun=True, test_names=["t_b1", "t_b2"]),
    ]
    report = flake_report.build_report(runs=[run], events=events, meta=REPORT_META)
    detail = report["rerun_runs"][0]
    assert len(detail["jobs"]) == 1
    assert sorted(detail["jobs"][0]["test_names"]) == ["t_a", "t_b1", "t_b2"]
    assert sorted(detail["test_names"]) == ["t_a", "t_b1", "t_b2"]


def test_branch_breakage_excluded_from_baseline_rate() -> None:
    run = flake_report.RunRecord(
        run_id=11,
        workflow="Risk Service CI",
        event="pull_request",
        head_branch="feature/break",
        head_sha="abc123",
        run_attempt=1,
        conclusion="failure",
        created_at="2026-11-08T10:00:00Z",
    )
    events = [event("flake:branch-only", branch="feature/break", run_id=11)]
    report = flake_report.build_report(runs=[run], events=events, meta=REPORT_META)
    entry = report["baseline"]["Risk Service CI"]
    assert entry["flaky_failed_runs"] == 0
    assert entry["baseline_flake_rate"] == 0.0
    assert len(report["branch_breakage"]) == 1


def test_rerun_failing_only_branch_breakage_excluded_from_baseline_rate() -> None:
    run = flake_report.RunRecord(
        run_id=21,
        workflow="Risk Service CI",
        event="pull_request",
        head_branch="feature/break",
        head_sha="abc123",
        run_attempt=2,
        conclusion="failure",
        created_at="2026-11-08T10:00:00Z",
    )
    events = [event("flake:branch-break-rerun", branch="feature/break", run_id=21)]
    report = flake_report.build_report(runs=[run], events=events, meta=REPORT_META)
    entry = report["baseline"]["Risk Service CI"]
    assert entry["rerun_runs"] == 1
    assert entry["flaky_failed_runs"] == 0
    assert entry["baseline_flake_rate"] == 0.0
    assert len(report["branch_breakage"]) == 1


def test_rerun_with_mixed_breakage_and_flake_still_counts() -> None:
    run = flake_report.RunRecord(
        run_id=22,
        workflow="Risk Service CI",
        event="pull_request",
        head_branch="feature/break",
        head_sha="abc123",
        run_attempt=2,
        conclusion="success",
        created_at="2026-11-08T10:00:00Z",
    )
    events = [
        event("flake:branch-mixed", branch="feature/break", run_id=22),
        event("flake:real", branch="feature/break", run_id=22, rerun=True),
    ]
    report = flake_report.build_report(runs=[run], events=events, meta=REPORT_META)
    entry = report["baseline"]["Risk Service CI"]
    assert entry["rerun_runs"] == 1
    assert entry["baseline_flake_rate"] == 1.0
    assert len(report["branch_breakage"]) == 1


# --- CLI ----------------------------------------------------------------------


def test_cli_accepts_the_three_modes() -> None:
    parser = flake_report.build_parser()
    report_args = parser.parse_args(["report", "--repo", "CedynGroup/aequorOS"])
    assert report_args.mode == "report"
    rerun_args = parser.parse_args(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "123", "--attempt", "2"]
    )
    assert rerun_args.mode == "rerun-summary"
    weekly_args = parser.parse_args(["weekly"])
    assert weekly_args.mode == "weekly"


# --- rerun-summary mode -------------------------------------------------------


def rerun_row(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "job_name": "Dashboard journeys shard 4/4",
        "failed_attempts": [1],
        "passed_attempts": [2],
        "rerun_minutes": 21.5,
    }
    values.update(overrides)
    return flake_report.RerunSummaryRow(**values)


def test_rerun_summary_rows_list_rerun_only_passes() -> None:
    """The recorded shape of run 42340795777 (attempts 1 and 4): shard 4/4 failed
    on attempt 1 and the whole run was green on attempt 4."""
    attempts = {
        1: flake_report.parse_jobs(fixture_json("attempt_jobs_failed.json")),
        4: flake_report.parse_jobs(fixture_json("attempt_jobs_passed.json")),
    }
    rows = flake_report.rerun_summary_rows(attempts, 4)
    assert [(row.job_name, row.failed_attempts, row.passed_attempts) for row in rows] == [
        ("Dashboard journeys shard 4/4", [1], [4])
    ]
    assert rows[0].rerun_minutes == 21.5


def test_rerun_summary_rows_report_actual_failed_attempts() -> None:
    """A fail-fail-pass history reports BOTH failed attempt numbers, not just
    the first (the ranking's first-failure compression must not leak into the
    rerun summary)."""
    attempts = {
        1: [job("Unit tests", "failure", 5.0, 1)],
        2: [job("Unit tests", "failure", 5.0, 2)],
        3: [job("Unit tests", "success", 6.0, 3)],
    }
    rows = flake_report.rerun_summary_rows(attempts, 3)
    assert len(rows) == 1
    assert rows[0].failed_attempts == [1, 2]
    assert rows[0].passed_attempts == [3]


def test_rerun_summary_rows_include_intermediate_passes() -> None:
    """Fail, pass, fail again, pass again: both failed attempts and both
    passing attempts are reported as they happened."""
    attempts = {
        1: [job("Journeys", "failure", 5.0, 1)],
        2: [job("Journeys", "success", 5.0, 2)],
        3: [job("Journeys", "failure", 5.0, 3)],
        4: [job("Journeys", "success", 5.0, 4)],
    }
    rows = flake_report.rerun_summary_rows(attempts, 4)
    assert rows[0].failed_attempts == [1, 3]
    assert rows[0].passed_attempts == [2, 4]


def test_rerun_summary_rows_exclude_jobs_still_failing_or_never_failing() -> None:
    attempts = {
        1: [job("Still failing", "failure", 5.0, 1), job("Always green", "success", 5.0, 2)],
        2: [job("Still failing", "failure", 5.0, 3), job("Always green", "success", 5.0, 4)],
    }
    assert flake_report.rerun_summary_rows(attempts, 2) == []


def test_rerun_summary_rows_exclude_aggregate_jobs() -> None:
    attempts = {
        1: [job("Dashboard journeys gate", "failure", 0.2, 1)],
        2: [job("Dashboard journeys gate", "success", 0.2, 2)],
    }
    assert flake_report.rerun_summary_rows(attempts, 2) == []


def test_rerun_summary_rows_sorted_most_expensive_rerun_first() -> None:
    attempts = {
        1: [job("Cheap job", "failure", 1.0, 1), job("Costly job", "failure", 1.0, 2)],
        2: [job("Cheap job", "success", 2.0, 3), job("Costly job", "success", 30.0, 4)],
    }
    rows = flake_report.rerun_summary_rows(attempts, 2)
    assert [row.job_name for row in rows] == ["Costly job", "Cheap job"]


def test_render_rerun_summary_lists_attempts_and_tests() -> None:
    row = rerun_row(
        failed_attempts=[1, 2],
        passed_attempts=[2, 3],
        findings=[
            flake_report.Finding(
                "test",
                "playwright:tests/submissions/regulatory-rule-status.e2e.spec.ts",
                ["status disclosure remains reachable when its badge scrolls out of view"],
            ),
            flake_report.Finding("infra", "infra:download-curl-22-504"),
        ],
    )
    markdown = flake_report.render_rerun_summary(
        42340795777,
        3,
        [row],
        {
            "workflow": "Dashboard Playwright journeys",
            "event": "pull_request",
            "branch": "feature/a",
        },
    )
    assert "## Rerun summary — run 42340795777 (attempt 3)" in markdown
    assert "Dashboard Playwright journeys" in markdown and "pull_request" in markdown
    assert "Dashboard journeys shard 4/4" in markdown
    assert "failed on attempts 1, 2" in markdown
    assert "attempt 3" in markdown
    assert "playwright:tests/submissions/regulatory-rule-status.e2e.spec.ts" in markdown
    assert "status disclosure remains reachable when its badge scrolls out of view" in markdown
    assert "infra:download-curl-22-504" in markdown


def test_render_rerun_summary_attempt_one_writes_first_attempt_note() -> None:
    markdown = flake_report.render_rerun_summary(42340795777, 1, [])
    assert "first attempt" in markdown.lower()
    assert "Failed on attempts" not in markdown
    assert "|" not in markdown


def test_render_rerun_summary_without_rows_notes_no_flip() -> None:
    markdown = flake_report.render_rerun_summary(42340795777, 2, [])
    assert "No job that failed in an earlier attempt passed on attempt 2" in markdown


def test_rerun_summary_command_attempt_one_never_calls_the_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*args: object) -> dict[str, Any]:
        raise AssertionError("attempt 1 must not touch the API")

    monkeypatch.setattr(flake_report, "gh_api_json", fail)
    exit_code = flake_report.main(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "9", "--attempt", "1"]
    )
    assert exit_code == 0
    assert "first attempt" in capsys.readouterr().out.lower()


def test_rerun_summary_command_survives_api_errors_with_a_visible_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*args: object) -> dict[str, Any]:
        raise RuntimeError("gh api failed (1): Server Error (HTTP 502)")

    monkeypatch.setattr(flake_report, "gh_api_json", fail)
    exit_code = flake_report.main(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "9", "--attempt", "2"]
    )
    assert exit_code == 0
    output = capsys.readouterr()
    assert "Rerun summary unavailable" in output.out
    assert "Server Error (HTTP 502)" in output.out


def test_rerun_summary_command_lists_fixture_run_rerun_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failed_payload = fixture_json("attempt_jobs_failed.json")
    passed_payload = fixture_json("attempt_jobs_passed.json")

    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        endpoint = endpoint_args[0]
        if endpoint.endswith("/actions/runs/42340795777"):
            return {
                "name": "Dashboard Playwright journeys",
                "event": "pull_request",
                "head_branch": "mission/ci-flake-measurement",
                "html_url": "https://github.com/CedynGroup/aequorOS/actions/runs/42340795777",
            }
        if "/attempts/1/jobs" in endpoint:
            return failed_payload
        if "/attempts/4/jobs" in endpoint:
            return passed_payload
        return {"total_count": 0, "jobs": []}

    def fake_gh_api(*endpoint_args: str, allow_escape_sequences: bool = False) -> str:
        assert "/actions/jobs/128700000005/logs" in endpoint_args[0]
        return "\n".join(fixture_text("playwright_rule_status.log"))

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    monkeypatch.setattr(flake_report, "gh_api", fake_gh_api)
    exit_code = flake_report.main(
        [
            "rerun-summary",
            "--repo",
            "CedynGroup/aequorOS",
            "--run-id",
            "42340795777",
            "--attempt",
            "4",
        ]
    )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "## Rerun summary — run 42340795777 (attempt 4)" in output
    assert "Dashboard journeys shard 4/4" in output
    assert "failed on attempts 1" in output
    assert "playwright:tests/submissions/regulatory-rule-status.e2e.spec.ts" in output
    assert "status disclosure remains reachable when its badge scrolls out of view" in output
