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
import time
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
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def fixture_json(name: str) -> dict:
    with (FIXTURES / name).open(encoding="utf-8") as handle:
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


def test_collection_error_without_a_test_name_registers_the_path() -> None:
    findings = flake_report.extract_findings(
        ["ERROR tests/db/test_broken_imports.py - ImportError: cannot import name 'x'"]
    )
    assert [(f.kind, f.signature, f.test_names) for f in findings] == [
        ("test", "pytest:tests/db/test_broken_imports.py", [])
    ]


def test_playwright_browser_contract_signature_extraction() -> None:
    findings = flake_report.extract_findings(
        [
            "2026-11-08T08:56:49.2Z ✘  2 tests/submissions/exports.integration.spec.ts:88:7 "
            "› the artifact act answers while the pack detail loads (15.3s)"
        ]
    )
    assert [(f.kind, f.signature, f.test_names) for f in findings] == [
        (
            "test",
            "playwright:tests/submissions/exports.integration.spec.ts",
            ["the artifact act answers while the pack detail loads"],
        )
    ]


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


def test_duplicate_same_named_jobs_in_one_attempt_do_not_crash() -> None:
    """Two same-named jobs in one attempt must not make Python compare JobRecords.

    Real trigger: run 42465955513 (Web CI, branch mission/rbac-185-signer,
    created 2026-11-08T09:09:16Z) had attempt 1 carrying two jobs named
    "Claude review verdict". The unkeyed sort of ``(attempt, job)`` tuples
    then fell through to comparing JobRecord objects and raised TypeError,
    which the weekly mode swallowed into an "unavailable" note.
    """
    attempts = {
        1: [
            job("Claude review verdict", "failure", 5.0, 101),
            job("Claude review verdict", "success", 6.0, 102),
        ],
        2: [job("Claude review verdict", "success", 5.0, 103)],
    }
    passes = flake_report.compare_attempts(attempts)
    assert len(passes) == 1
    assert passes[0].failed_attempt == 1
    assert passes[0].failed_job.job_id == 101
    assert passes[0].passed_attempt == 2
    assert passes[0].passed_minutes == 5.0


def test_a_partially_passing_attempt_does_not_count_as_recovery() -> None:
    """With duplicate names, one success must not recover the other's failure.

    Attempt 2 has one success and one failure of the same name: no identity
    ties attempt 1's failures to that one success, so the recovery is the
    attempt where every job of the name succeeded (attempt 3).
    """
    attempts = {
        1: [
            job("Claude review verdict", "failure", 5.0, 301),
            job("Claude review verdict", "failure", 4.0, 302),
        ],
        2: [
            job("Claude review verdict", "success", 6.0, 303),
            job("Claude review verdict", "failure", 4.5, 304),
        ],
        3: [
            job("Claude review verdict", "success", 6.5, 305),
            job("Claude review verdict", "success", 7.0, 306),
        ],
    }
    passes = flake_report.compare_attempts(attempts)
    # Attempt 3 is the first fully-passing attempt. The attempt-2 failure is
    # part of the same rerun cycle: it counts, but charges no minutes again.
    assert [(p.failed_job.job_id, p.passed_attempt, p.passed_minutes) for p in passes] == [
        (301, 3, 6.5),
        (302, 3, 7.0),
        (304, 3, 0.0),
    ]


def test_two_same_named_failures_in_one_attempt_are_both_reported() -> None:
    """Duplicate names keep deterministic ordering and one pass per failure."""
    attempts = {
        1: [
            job("Claude review verdict", "failure", 5.0, 201),
            job("Claude review verdict", "failure", 4.0, 202),
        ],
        2: [
            job("Claude review verdict", "success", 6.0, 203),
            job("Claude review verdict", "success", 7.0, 204),
        ],
    }
    passes = flake_report.compare_attempts(attempts)
    assert [(p.failed_attempt, p.failed_job.job_id, p.passed_attempt) for p in passes] == [
        (1, 201, 2),
        (1, 202, 2),
    ]
    # Each re-execution charges its own minutes: the duplicates share the
    # recovery attempt's successes positionally (201 -> 203, 202 -> 204),
    # instead of the first success's minutes being charged twice.
    assert [p.passed_minutes for p in passes] == [6.0, 7.0]


def test_unmatched_duplicates_stay_unrecovered_on_later_attempts() -> None:
    """Each failed attempt's duplicates are matched to the recovery separately.

    With two same-named failures on attempt 1, two on attempt 2 and a single
    success on attempt 3, the success closes one cycle per failed attempt
    (the recovery attempt's pass is the same instance passing again), but no
    attempt gets more recoveries than it has successes: the unmatched
    duplicates stay unrecovered on every attempt.
    """
    attempts = {
        1: [
            job("Claude review verdict", "failure", 5.0, 401),
            job("Claude review verdict", "failure", 4.0, 402),
        ],
        2: [
            job("Claude review verdict", "failure", 5.5, 403),
            job("Claude review verdict", "failure", 4.5, 404),
        ],
        3: [job("Claude review verdict", "success", 6.0, 405)],
    }
    passes = flake_report.compare_attempts(attempts)
    assert [(p.failed_job.job_id, p.passed_attempt, p.passed_minutes) for p in passes] == [
        (401, 3, 6.0),
        (403, 3, 0.0),
    ]


def test_recovery_matches_are_separate_per_failed_attempt() -> None:
    """Two same-named failures on attempt 1 and two on attempt 2, with two
    successes on attempt 3: each attempt's instances match positionally, so
    all four failures carry recovery evidence, but only the first failed
    attempt charges the re-executed minutes."""
    attempts = {
        1: [
            job("Claude review verdict", "failure", 5.0, 601),
            job("Claude review verdict", "failure", 4.0, 602),
        ],
        2: [
            job("Claude review verdict", "failure", 5.5, 603),
            job("Claude review verdict", "failure", 4.5, 604),
        ],
        3: [
            job("Claude review verdict", "success", 6.0, 605),
            job("Claude review verdict", "success", 7.0, 606),
        ],
    }
    passes = flake_report.compare_attempts(attempts)
    assert [(p.failed_job.job_id, p.passed_attempt, p.passed_minutes) for p in passes] == [
        (601, 3, 6.0),
        (602, 3, 7.0),
        (603, 3, 0.0),
        (604, 3, 0.0),
    ]


def test_a_duplicate_that_first_fails_later_charges_its_own_minutes() -> None:
    """The re-executed minutes are charged once per instance, not per name.

    Attempt 1 fails instance A; attempt 2 fails A again and B for the first
    time; attempt 3 passes both. B's first failure is attempt 2, so B carries
    its own re-executed minutes; A's re-execution was already charged on
    attempt 1.
    """
    attempts = {
        1: [job("Claude review verdict", "failure", 5.0, 701)],
        2: [
            job("Claude review verdict", "failure", 5.5, 703),
            job("Claude review verdict", "failure", 4.5, 704),
        ],
        3: [
            job("Claude review verdict", "success", 6.0, 705),
            job("Claude review verdict", "success", 7.0, 706),
        ],
    }
    passes = flake_report.compare_attempts(attempts)
    assert [(p.failed_job.job_id, p.passed_minutes) for p in passes] == [
        (701, 6.0),
        (703, 0.0),
        (704, 7.0),
    ]


def test_recovery_minutes_follow_the_complete_listing_rank() -> None:
    """Matching uses the rank from the complete attempt listing.

    Attempt 1 has two same-named jobs where rank 0 succeeded and rank 1
    failed; the recovery attempt's successes last 1 and 30 minutes. The
    failure is rank 1, so it must be recovered by (and charged) the 30-minute
    success, not the first success in the list.
    """
    attempts = {
        1: [
            job("Claude review verdict", "success", 3.0, 801),
            job("Claude review verdict", "failure", 5.0, 802),
        ],
        2: [
            job("Claude review verdict", "success", 1.0, 803),
            job("Claude review verdict", "success", 30.0, 804),
        ],
    }
    passes = flake_report.compare_attempts(attempts)
    assert [(p.failed_job.job_id, p.passed_minutes) for p in passes] == [(802, 30.0)]


def test_run_cap_reason_survives_a_final_page_with_extra_in_window_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cap that removes in-window runs from the returned slice is a cap hit.

    The final page carries three in-window runs plus a pre-window one: the
    loop reports complete coverage, but the returned slice keeps only
    ``min_runs`` of them, so the listing must still be reported as capped -
    otherwise the weekly summary presents an incomplete threshold result as
    complete.
    """

    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        return {
            "workflow_runs": [
                {
                    "id": run_id,
                    "name": "Risk Service CI",
                    "event": "push",
                    "head_branch": "main",
                    "head_sha": "a" * 40,
                    "run_attempt": 1,
                    "conclusion": "success",
                    "created_at": created_at,
                }
                for run_id, created_at in [
                    (1, "2026-11-08T10:00:00Z"),
                    (2, "2026-11-07T10:00:00Z"),
                    (3, "2026-11-06T10:00:00Z"),
                    (4, "2026-10-01T10:00:00Z"),
                ]
            ],
            "total_count": 4,
        }

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    records, reason = flake_report.list_workflow_runs(
        "CedynGroup/aequorOS",
        "risk-service.yml",
        min_runs=2,
        since="2026-10-26",
        log=lambda message: None,
    )
    assert reason == "run-cap"
    assert [run.run_id for run in records] == [1, 2]


def test_consistent_failure_is_not_flaky() -> None:
    attempts = {
        1: [job("Dashboard journeys shard 4/4", "failure", 20.0)],
        2: [job("Dashboard journeys shard 4/4", "failure", 20.0)],
    }
    assert flake_report.compare_attempts(attempts) == []


def test_recovery_after_an_earlier_success_is_a_rerun_pass() -> None:
    attempts = {
        1: [job("Dashboard journeys shard 4/4", "success", 20.0)],
        2: [job("Dashboard journeys shard 4/4", "failure", 20.0)],
        3: [job("Dashboard journeys shard 4/4", "success", 21.0)],
    }
    passes = flake_report.compare_attempts(attempts)
    assert len(passes) == 1
    assert passes[0].failed_attempt == 2
    assert passes[0].passed_attempt == 3


def test_a_timed_out_attempt_recovers_as_a_rerun_pass() -> None:
    attempts = {
        1: [job("Dashboard journeys shard 2/4", "timed_out", 40.0)],
        2: [job("Dashboard journeys shard 2/4", "success", 21.0)],
    }
    passes = flake_report.compare_attempts(attempts)
    assert len(passes) == 1
    assert passes[0].failed_attempt == 1


def test_timed_out_job_is_collected_as_failed() -> None:
    run = flake_report.RunRecord(
        run_id=1,
        workflow="Dashboard Playwright journeys",
        event="pull_request",
        head_branch="feature/a",
        head_sha="abc123",
        run_attempt=1,
        conclusion="failure",
        created_at="2026-11-08T10:00:00Z",
    )
    failed = flake_report._failed_leaf_jobs(
        {1: run}, {1: {1: [job("Dashboard journeys shard 2/4", "timed_out", 40.0)]}}
    )
    assert [(job_record.name, attempt) for _, attempt, job_record in failed] == [
        ("Dashboard journeys shard 2/4", 1)
    ]


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


def test_swapped_shard_failures_between_failing_runs_are_mixed_outcomes() -> None:
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
            event="pull_request",
            head_branch="feature/a",
            head_sha="abc123",
            run_attempt=2,
            conclusion="failure",
            created_at="2026-11-08T12:00:00Z",
        ),
    ]
    # Both runs end failed, but different shards fail in each: the swap is the
    # mixed evidence, even though no run of this SHA ever passed.
    jobs_by_run = {
        1: [
            job("Dashboard journeys shard 1/4", "failure", 20.0),
            job("Dashboard journeys shard 2/4", "success", 20.0),
        ],
        2: [
            job("Dashboard journeys shard 1/4", "success", 20.0),
            job("Dashboard journeys shard 2/4", "failure", 20.0),
        ],
    }
    mixed = flake_report.same_sha_mixed_outcomes(runs, jobs_by_run)
    assert sorted((m.failed_run_id, m.job_name) for m in mixed) == [
        (1, "Dashboard journeys shard 1/4"),
        (2, "Dashboard journeys shard 2/4"),
    ]


def test_a_job_failing_in_both_runs_is_not_passing_evidence() -> None:
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
            event="pull_request",
            head_branch="feature/a",
            head_sha="abc123",
            run_attempt=2,
            conclusion="failure",
            created_at="2026-11-08T12:00:00Z",
        ),
    ]
    # The same shard failed in both runs of the SHA: deterministic breakage,
    # not mixed evidence, however the runs themselves concluded.
    jobs_by_run = {
        1: [job("Dashboard journeys shard 4/4", "failure", 20.0)],
        2: [job("Dashboard journeys shard 4/4", "failure", 20.0)],
    }
    assert flake_report.same_sha_mixed_outcomes(runs, jobs_by_run) == []


def test_a_timed_out_run_pulls_its_sha_into_the_attempt_plan() -> None:
    runs = [
        flake_report.RunRecord(
            run_id=1,
            workflow="Dashboard Playwright journeys",
            event="pull_request",
            head_branch="feature/a",
            head_sha="abc123",
            run_attempt=1,
            conclusion="timed_out",
            created_at="2026-11-08T10:00:00Z",
        ),
        flake_report.RunRecord(
            run_id=2,
            workflow="Dashboard Playwright journeys",
            event="pull_request",
            head_branch="feature/a",
            head_sha="abc123",
            run_attempt=1,
            conclusion="success",
            created_at="2026-11-08T12:00:00Z",
        ),
    ]
    # The successful run's jobs must be listed too, or the timed-out run's
    # recovery can never be recognized as a mixed outcome.
    assert flake_report._attempts_needed(runs) == {1: {1}, 2: {1}}


def test_a_job_listing_is_not_cacheable_before_it_has_jobs() -> None:
    # An empty listing means the attempt has not reported jobs yet (e.g. a
    # queued rerun): caching it would hide the failure or recovery evidence
    # the attempt produces later.
    assert flake_report._jobs_are_complete([]) is False
    assert flake_report._jobs_are_complete([{"status": "completed"}]) is True
    assert flake_report._jobs_are_complete([{"status": "completed"}, {"status": "queued"}]) is False


def test_fail_fail_pass_is_one_rerun_pass() -> None:
    attempts = {
        1: [job("Dashboard journeys shard 4/4", "failure", 20.0, 11)],
        2: [job("Dashboard journeys shard 4/4", "failure", 20.0, 12)],
        3: [job("Dashboard journeys shard 4/4", "success", 20.0, 13)],
    }
    passes = flake_report.compare_attempts(attempts)
    # One rerun cycle for the run/job; both failed attempts carry recovery
    # evidence, only the first carries the re-executed minutes.
    assert [(p.failed_attempt, p.passed_attempt) for p in passes] == [(1, 3), (2, 3)]
    assert passes[0].passed_minutes == 20.0
    assert passes[1].passed_minutes == 0.0
    row = flake_report.build_ranking(
        [
            flake_report.FlakeEvent(
                signature="pytest:tests/a.py",
                kind="test",
                workflow="Risk Service CI",
                run_id=42,
                event="pull_request",
                branch="feature/a",
                sha="a" * 40,
                job="Dashboard journeys shard 4/4",
                attempt=1,
                minutes=10.0,
                rerun_minutes=20.0,
                rerun=True,
                same_sha_pass=False,
                created_at="2026-11-08T10:00:00Z",
                test_names=[],
            ),
            flake_report.FlakeEvent(
                signature="pytest:tests/a.py",
                kind="test",
                workflow="Risk Service CI",
                run_id=42,
                event="pull_request",
                branch="feature/a",
                sha="a" * 40,
                job="Dashboard journeys shard 4/4",
                attempt=2,
                minutes=10.0,
                rerun_minutes=0.0,
                rerun=True,
                same_sha_pass=False,
                created_at="2026-11-08T10:00:00Z",
                test_names=[],
            ),
        ],
        {},
    )[0]
    assert row["rerun_count"] == 1


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
        # Keyed by job id, not the job name: two same-named jobs can fail in
        # one attempt and a name key would keep only the last one's recovery.
        (7, 1, 21): flake_report.RerunPass(
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
    attempt_jobs = {
        7: {
            1: [job("Dashboard journeys shard 4/4", "failure", 20.0, 21)],
            2: [job("Dashboard journeys shard 4/4", "success", 20.0, 22)],
            3: [job("Dashboard journeys shard 4/4", "failure", 20.0, 23)],
        }
    }
    events = flake_report._build_events(
        failed_jobs, attempt_jobs, rerun_passes, set(), findings_by_job
    )
    failures = [e for e in events if e.attempt != 2]
    assert len(failures) == 2
    by_attempt = {e.attempt: e for e in failures}
    assert by_attempt[1].rerun is True
    assert by_attempt[3].rerun is False
    assert by_attempt[3].rerun_minutes == 0.0


def test_a_failure_between_the_first_and_the_pass_carries_recovery_evidence() -> None:
    attempts = {
        1: [job("Dashboard journeys shard 4/4", "failure", 20.0, 31)],
        2: [job("Dashboard journeys shard 4/4", "failure", 20.0, 32)],
        3: [job("Dashboard journeys shard 4/4", "success", 20.0, 33)],
    }
    rerun_passes = flake_report.compare_attempts(attempts)
    # Both failed attempts recovered on attempt 3, so both carry rerun
    # evidence; only the first failed attempt carries the re-executed
    # minutes, so the rerun stays one rerun.
    assert [(p.failed_attempt, p.passed_attempt) for p in rerun_passes] == [(1, 3), (2, 3)]
    assert rerun_passes[0].passed_minutes == 20.0
    assert rerun_passes[1].passed_minutes == 0.0


def test_a_second_fail_pass_cycle_recovers_with_its_own_evidence() -> None:
    attempts = {
        1: [job("Dashboard journeys shard 4/4", "failure", 20.0, 41)],
        2: [job("Dashboard journeys shard 4/4", "success", 21.0, 42)],
        3: [job("Dashboard journeys shard 4/4", "failure", 20.0, 43)],
        4: [job("Dashboard journeys shard 4/4", "success", 22.0, 44)],
    }
    passes = flake_report.compare_attempts(attempts)
    # Each failed attempt recovers at the first pass after it, and each
    # re-execution carries its own minutes.
    assert [(p.failed_attempt, p.passed_attempt) for p in passes] == [(1, 2), (3, 4)]
    assert passes[0].passed_minutes == 21.0
    assert passes[1].passed_minutes == 22.0
    # Two cycles of one job in one run are still one rerun for the counts.
    shared = {
        "signature": "pytest:tests/a.py",
        "kind": "test",
        "workflow": "Risk Service CI",
        "run_id": 51,
        "event": "pull_request",
        "branch": "feature/a",
        "sha": "a" * 40,
        "job": "Dashboard journeys shard 4/4",
        "created_at": "2026-11-08T10:00:00Z",
        "test_names": [],
    }
    events = [
        flake_report.FlakeEvent(
            **shared,
            attempt=1,
            minutes=20.0,
            rerun_minutes=21.0,
            rerun=True,
            same_sha_pass=False,
        ),
        flake_report.FlakeEvent(
            **shared,
            attempt=3,
            minutes=20.0,
            rerun_minutes=22.0,
            rerun=True,
            same_sha_pass=False,
        ),
    ]
    row = flake_report.build_ranking(events, {})[0]
    assert row["rerun_count"] == 1
    assert row["rerun_minutes"] == pytest.approx(43.0)
    assert flake_report.evaluate_thresholds(events, datetime(2026, 11, 9, tzinfo=UTC)) == []


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
    # Each distinct run/job pair is one rerun cycle; several attempts of one
    # run/job count once (covered by the one-rerun-cycle test above).
    two = [
        event("flake:a", rerun=True, created_at=recent, run_id=41),
        event("flake:a", rerun=True, created_at=recent, run_id=42),
    ]
    three = [
        event("flake:x", rerun=True, created_at=recent, run_id=41),
        event("flake:x", rerun=True, created_at=recent, run_id=42),
        event("flake:x", rerun=True, created_at=recent, run_id=43),
    ]
    old = event("flake:old", rerun=True, created_at="2026-10-01T10:00:00Z")

    breaches = flake_report.evaluate_thresholds([*two, old], now)
    assert len(breaches) == 1
    assert breaches[0].action == "needs flaky-test issue with owner"

    breaches = flake_report.evaluate_thresholds([*three, old], now)
    assert len(breaches) == 1
    assert breaches[0].action == "fix or quarantine within 7 days"

    assert flake_report.evaluate_thresholds([old], now) == []


def test_duplicate_same_named_jobs_count_as_separate_rerun_cycles() -> None:
    """The rerun-cycle dedup keys on the job instance rank, not the name.

    Two same-named jobs in one attempt that both failed with the same
    signature and both passed on rerun are two re-executions: the policy
    counts two cycles, so two such recurrences cross the flaky-test-issue
    threshold. The ranks are what ``_build_events`` assigns (position among
    the same-named jobs of that attempt, from the complete listing).
    """
    now = datetime(2026, 11, 9, tzinfo=UTC)
    events = [
        event(
            "pytest:tests/a.py",
            rerun=True,
            run_id=41,
            instance_rank=0,
        ),
        event(
            "pytest:tests/a.py",
            rerun=True,
            run_id=41,
            instance_rank=1,
        ),
    ]
    breaches = flake_report.evaluate_thresholds(events, now)
    assert len(breaches) == 1
    assert breaches[0].rerun_passes_in_window == 2
    assert breaches[0].action == "needs flaky-test issue with owner"


def test_one_rerun_cycle_across_attempts_counts_once_despite_new_job_ids() -> None:
    """GitHub assigns fresh job ids on every attempt.

    One job that fails the same signature on attempts 1 and 2 and passes on 3
    is one rerun cycle, not two: the dedup identifies the instance across
    attempts (its rank is stable) while still telling same-named distinct
    jobs apart.
    """
    now = datetime(2026, 11, 9, tzinfo=UTC)
    events = [
        event("pytest:tests/a.py", rerun=True, run_id=41, attempt=1),
        event("pytest:tests/a.py", rerun=True, run_id=41, attempt=2),
    ]
    assert flake_report.evaluate_thresholds(events, now) == []


def test_unidentified_failures_do_not_cross_policy_thresholds() -> None:
    """A readable log with no recognized failure signature is not an
    identified failure.

    Three unrelated unidentified failures sharing one job-name placeholder
    must not demand a fix or quarantine; they stay visible in the ranking
    and the collection caveats instead.
    """
    now = datetime(2026, 11, 9, tzinfo=UTC)
    events = [event("unclassified:dashboard", rerun=True, run_id=run_id) for run_id in (41, 42, 43)]
    assert flake_report.evaluate_thresholds(events, now) == []


def test_each_duplicate_instance_charges_its_own_recovery_minutes() -> None:
    """A same-named instance that first fails on a later attempt still
    charges the minutes its own recovery spent.

    With failure/success on attempt 1, two failures on attempt 2 and two
    successes (7 and 11 minutes) on attempt 3, both recovery positions are
    charged once each (18 minutes total), instead of every failure after
    the earliest one passing for free.
    """
    attempts = {
        1: [
            flake_report.JobRecord(job_id=1, name="ci", conclusion="failure", minutes=3.0),
            flake_report.JobRecord(job_id=2, name="ci", conclusion="success", minutes=4.0),
        ],
        2: [
            flake_report.JobRecord(job_id=3, name="ci", conclusion="failure", minutes=5.0),
            flake_report.JobRecord(job_id=4, name="ci", conclusion="failure", minutes=6.0),
        ],
        3: [
            flake_report.JobRecord(job_id=5, name="ci", conclusion="success", minutes=7.0),
            flake_report.JobRecord(job_id=6, name="ci", conclusion="success", minutes=11.0),
        ],
    }
    passes = flake_report.compare_attempts(attempts)
    assert len(passes) == 3
    assert sum(p.passed_minutes for p in passes) == 18.0


def test_a_subset_rerun_recovery_is_not_discarded() -> None:
    """A subset rerun lists only the re-run jobs, so the recovery attempt can
    hold fewer same-named jobs than the failing attempt did.

    With success/failure on attempt 1 and a rerun of only the failed job that
    passes on attempt 2, the failure's recovery is real evidence: discarding
    it would lose the rerun's minutes and hide the identified failure from
    the policy thresholds.
    """
    attempts = {
        1: [
            flake_report.JobRecord(job_id=1, name="ci", conclusion="success", minutes=4.0),
            flake_report.JobRecord(job_id=2, name="ci", conclusion="failure", minutes=3.0),
        ],
        2: [flake_report.JobRecord(job_id=3, name="ci", conclusion="success", minutes=7.0)],
    }
    passes = flake_report.compare_attempts(attempts)
    assert len(passes) == 1
    assert passes[0].failed_attempt == 1
    assert passes[0].passed_minutes == 7.0


def test_a_subset_rerun_chain_keeps_the_first_failures_evidence() -> None:
    """A failed job that needs more than one subset rerun recovers once, for
    every failed execution.

    With success/failure on attempt 1, a subset rerun failing again on
    attempt 2 and the second subset rerun passing on attempt 3, both failed
    executions carry recovery evidence (the minutes charged once), instead
    of the first failure being discarded for its shifted rank.
    """
    attempts = {
        1: [
            flake_report.JobRecord(job_id=1, name="ci", conclusion="success", minutes=4.0),
            flake_report.JobRecord(job_id=2, name="ci", conclusion="failure", minutes=3.0),
        ],
        2: [flake_report.JobRecord(job_id=3, name="ci", conclusion="failure", minutes=5.0)],
        3: [flake_report.JobRecord(job_id=4, name="ci", conclusion="success", minutes=7.0)],
    }
    passes = flake_report.compare_attempts(attempts)
    assert [(p.failed_job.job_id, p.failed_attempt, p.passed_minutes) for p in passes] == [
        (2, 1, 7.0),
        (3, 2, 0.0),
    ]


def test_instance_rank_survives_a_passing_sibling_in_one_attempt() -> None:
    """The instance rank comes from the complete attempt listing, not the
    failed events alone.

    Two same-named jobs fail on attempt 1 (ranks 0 and 1); on attempt 2 only
    the rank-1 job fails again (the rank-0 job passes). Its rank stays 1, so
    the two recurrences are one rerun cycle per instance — not two for the
    rank-1 job because the sibling is missing from the failed events.
    """
    now = datetime(2026, 11, 9, tzinfo=UTC)
    events = [
        event("pytest:tests/a.py", rerun=True, run_id=41, attempt=1, instance_rank=1),
        event("pytest:tests/a.py", rerun=True, run_id=41, attempt=2, instance_rank=1),
    ]
    assert flake_report.evaluate_thresholds(events, now) == []


def test_a_subset_rerun_collapses_ambiguous_instance_identity() -> None:
    """A partial rerun lists only the re-run jobs, so a same-named job's
    position among its attempt's same-named jobs shifts between attempts.

    GitHub's subset rerun keeps the jobs that were not re-run out of the
    later attempt listing entirely. With attempt 1 listing two same-named
    jobs (A passed, B failed), attempt 2 listing only B (failed again) and
    attempt 3 only B (passed), which instance B is cannot be told apart, so
    the lineage collapses by name: one logical job stays one rerun cycle,
    and the conservative direction for the policy thresholds is the
    undercount.
    """
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
    name = "Dashboard journeys shard 4/4"
    signature = "playwright:tests/x.e2e.spec.ts"
    b1 = job(name, "failure", 20.0, 102)
    b2 = job(name, "failure", 20.0, 103)
    failed_jobs = [(run, 1, b1), (run, 2, b2)]
    rerun_passes = {
        (7, 1, 102): flake_report.RerunPass(
            failed_job=b1, failed_attempt=1, passed_attempt=3, passed_minutes=20.0
        ),
        (7, 2, 103): flake_report.RerunPass(
            failed_job=b2, failed_attempt=2, passed_attempt=3, passed_minutes=20.0
        ),
    }
    findings_by_job = {
        102: [flake_report.Finding("test", signature, ["t"])],
        103: [flake_report.Finding("test", signature, ["t"])],
    }
    attempt_jobs = {
        7: {
            1: [job(name, "success", 20.0, 101), b1],
            2: [b2],
            3: [job(name, "success", 20.0, 104)],
        }
    }
    events = flake_report._build_events(
        failed_jobs, attempt_jobs, rerun_passes, set(), findings_by_job
    )
    assert flake_report.evaluate_thresholds(events, datetime(2026, 11, 9, tzinfo=UTC)) == []


def test_two_same_named_jobs_across_attempts_still_count_separately() -> None:
    """Instance identity survives new per-attempt job ids.

    Two same-named jobs that each fail on attempts 1 and 2 before passing are
    two rerun cycles, even though every failed execution is a distinct event.
    """
    now = datetime(2026, 11, 9, tzinfo=UTC)
    events = [
        event("pytest:tests/a.py", rerun=True, run_id=41, attempt=1, instance_rank=0),
        event("pytest:tests/a.py", rerun=True, run_id=41, attempt=1, instance_rank=1),
        event("pytest:tests/a.py", rerun=True, run_id=41, attempt=2, instance_rank=0),
        event("pytest:tests/a.py", rerun=True, run_id=41, attempt=2, instance_rank=1),
    ]
    breaches = flake_report.evaluate_thresholds(events, now)
    assert len(breaches) == 1
    assert breaches[0].rerun_passes_in_window == 2


def test_attempt_jobs_pager_fetches_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """An attempt with more than PER_PAGE jobs must not lose its later pages."""
    endpoints: list[str] = []

    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        endpoint = endpoint_args[0]
        endpoints.append(endpoint)
        page = int(endpoint.rsplit("page=", 1)[1])
        if page == 1:
            return {
                "total_count": 3,
                "jobs": [
                    {"id": 1, "name": "shard 1/3", "status": "completed", "conclusion": "failure"},
                    {"id": 2, "name": "shard 2/3", "status": "completed", "conclusion": "success"},
                ],
            }
        return {
            "total_count": 3,
            "jobs": [
                {"id": 3, "name": "shard 3/3", "status": "completed", "conclusion": "success"},
            ],
        }

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    jobs = flake_report.fetch_attempt_jobs("CedynGroup/aequorOS", 9, 1)
    assert [j.job_id for j in jobs] == [1, 2, 3]
    assert len(endpoints) == 2
    assert "page=2" in endpoints[1]


def test_an_incomplete_cached_job_listing_is_refetched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cached listing that predates its last page must be refetched.

    Reusing a page-truncated copy would silently drop the later jobs from
    the findings and the thresholds.
    """
    cache_dir = tmp_path / "cache"
    (cache_dir / "jobs").mkdir(parents=True)
    (cache_dir / "jobs" / "9-1.json").write_text(
        json.dumps(
            {
                "total_count": 3,
                "jobs": [
                    {"id": 1, "name": "shard 1/3", "status": "completed", "conclusion": "failure"},
                    {"id": 2, "name": "shard 2/3", "status": "completed", "conclusion": "success"},
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        return {
            "total_count": 3,
            "jobs": [
                {"id": 1, "name": "shard 1/3", "status": "completed", "conclusion": "failure"},
                {"id": 2, "name": "shard 2/3", "status": "completed", "conclusion": "success"},
                {"id": 3, "name": "shard 3/3", "status": "completed", "conclusion": "success"},
            ],
        }

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    jobs = flake_report.fetch_attempt_jobs("CedynGroup/aequorOS", 9, 1, cache_dir=cache_dir)
    assert [j.job_id for j in jobs] == [1, 2, 3]


def test_a_complete_cached_job_listing_is_reused_without_refetching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cached listing holding every job the attempt reported needs no API
    traffic: the cache serves it unchanged."""
    cache_dir = tmp_path / "cache"
    (cache_dir / "jobs").mkdir(parents=True)
    (cache_dir / "jobs" / "9-1.json").write_text(
        json.dumps(
            {
                "total_count": 1,
                "jobs": [
                    {"id": 1, "name": "ci", "status": "completed", "conclusion": "success"},
                ],
            }
        ),
        encoding="utf-8",
    )

    def fail(*args: object, **kwargs: object) -> dict[str, Any]:
        raise AssertionError("a complete cache entry must not trigger a refetch")

    monkeypatch.setattr(flake_report, "gh_api_json", fail)
    jobs = flake_report.fetch_attempt_jobs("CedynGroup/aequorOS", 9, 1, cache_dir=cache_dir)
    assert [j.job_id for j in jobs] == [1]


def test_missing_log_markers_never_cross_the_policy_thresholds() -> None:
    """A log that could not be read names no failure, so it cannot demand one.

    ``log-budget-exhausted:``/``log-unavailable:`` events can be marked as
    rerun passes (the job did fail and pass again); grouping them as a
    signature must not produce a policy breach. A real signature in the same
    events still crosses.
    """
    now = datetime(2026, 11, 9, tzinfo=UTC)
    recent = "2026-11-08T10:00:00Z"
    markers = [
        event("log-budget-exhausted:Dashboard journeys shard 4/4", rerun=True, run_id=41),
        event("log-budget-exhausted:Dashboard journeys shard 4/4", rerun=True, run_id=42),
        event("log-unavailable:Dashboard journeys shard 4/4", rerun=True, run_id=43),
        event("log-unavailable:Dashboard journeys shard 4/4", rerun=True, run_id=44),
    ]
    assert flake_report.evaluate_thresholds(markers, now) == []
    real = [
        event("pytest:tests/a.py", rerun=True, created_at=recent, run_id=41),
        event("pytest:tests/a.py", rerun=True, created_at=recent, run_id=42),
    ]
    breaches = flake_report.evaluate_thresholds([*markers, *real], now)
    assert [breach.signature for breach in breaches] == ["pytest:tests/a.py"]


def test_one_rerun_cycle_counts_once_per_run_and_job() -> None:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    shared = {
        "signature": "pytest:tests/a.py",
        "kind": "test",
        "workflow": "Risk Service CI",
        "run_id": 42,
        "event": "pull_request",
        "branch": "feature/a",
        "sha": "a" * 40,
        "job": "Dashboard journeys shard 4/4",
        "created_at": "2026-11-08T10:00:00Z",
        "test_names": [],
    }
    # The same job failed the same signature on two attempts of one run and
    # then passed: one rerun cycle, so one rerun count and one threshold hit.
    events = [
        flake_report.FlakeEvent(
            **shared,
            attempt=1,
            minutes=10.0,
            rerun_minutes=8.0,
            rerun=True,
            same_sha_pass=False,
        ),
        flake_report.FlakeEvent(
            **shared,
            attempt=2,
            minutes=10.0,
            rerun_minutes=0.0,
            rerun=True,
            same_sha_pass=False,
        ),
    ]
    row = flake_report.build_ranking(events, {})[0]
    assert row["occurrences"] == 2
    assert row["rerun_count"] == 1
    assert row["rerun_minutes"] == pytest.approx(8.0)
    assert flake_report.evaluate_thresholds(events, now) == []


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


def test_rerun_summary_command_survives_malformed_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*args: object) -> dict[str, Any]:
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(flake_report, "gh_api_json", fail)
    exit_code = flake_report.main(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "9", "--attempt", "2"]
    )
    assert exit_code == 0
    assert "Rerun summary unavailable" in capsys.readouterr().out


def test_rerun_summary_command_survives_missing_job_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        endpoint = endpoint_args[0]
        if endpoint.endswith("/actions/runs/9"):
            return {"name": "Risk Service CI", "event": "push", "head_branch": "feature/a"}
        raise KeyError("id")

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    exit_code = flake_report.main(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "9", "--attempt", "2"]
    )
    assert exit_code == 0
    assert "Rerun summary unavailable" in capsys.readouterr().out


def test_rerun_summary_command_survives_malformed_job_payloads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        endpoint = endpoint_args[0]
        if endpoint.endswith("/actions/runs/9"):
            return {"name": "Risk Service CI", "event": "push", "head_branch": "feature/a"}
        return {"total_count": 0, "jobs": None}

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    exit_code = flake_report.main(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "9", "--attempt", "2"]
    )
    assert exit_code == 0
    assert "Rerun summary unavailable" in capsys.readouterr().out


def test_rerun_summary_log_download_diagnostics_stay_off_the_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        endpoint = endpoint_args[0]
        if endpoint.endswith("/actions/runs/9"):
            return {"name": "Risk Service CI", "event": "push", "head_branch": "feature/a"}
        if "/attempts/1/jobs" in endpoint:
            return fixture_json("attempt_jobs_failed.json")
        if "/attempts/2/jobs" in endpoint:
            return fixture_json("attempt_jobs_passed.json")
        return {"total_count": 0, "jobs": []}

    def failing_gh_api(
        *endpoint_args: str, allow_escape_sequences: bool = False, retries: int = 4
    ) -> str:
        raise RuntimeError("gh api failed (1): Server Error (HTTP 502)")

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    monkeypatch.setattr(flake_report, "gh_api", failing_gh_api)
    exit_code = flake_report.main(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "9", "--attempt", "2"]
    )
    assert exit_code == 0
    output = capsys.readouterr()
    assert "log unavailable for job" in output.err
    assert "log unavailable for job" not in output.out


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

    def fake_gh_api(
        *endpoint_args: str, allow_escape_sequences: bool = False, retries: int = 4
    ) -> str:
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


def test_rerun_summary_rows_include_timed_out_earlier_attempts() -> None:
    """A job-level timeout on the earlier attempt and success on the rerun is
    a rerun-only pass (the journey shards time out at 40 minutes)."""
    attempts = {
        1: [job("Dashboard journeys shard 2/4", "timed_out", 40.0, 1)],
        2: [job("Dashboard journeys shard 2/4", "success", 21.0, 2)],
    }
    rows = flake_report.rerun_summary_rows(attempts, 2)
    assert [(row.job_name, row.failed_attempts) for row in rows] == [
        ("Dashboard journeys shard 2/4", [1])
    ]


def test_rerun_summary_command_survives_a_non_object_run_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(flake_report, "gh_api_json", lambda *endpoint: ["not", "an", "object"])
    exit_code = flake_report.main(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "9", "--attempt", "2"]
    )
    assert exit_code == 0
    assert "Rerun summary unavailable" in capsys.readouterr().out


def test_rerun_summary_command_survives_an_unhashable_job_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        endpoint = endpoint_args[0]
        if endpoint.endswith("/actions/runs/9"):
            return {"name": "Risk Service CI", "event": "push", "head_branch": "feature/a"}
        return {
            "total_count": 1,
            "jobs": [
                {
                    "id": 1,
                    "name": {"unhashable": True},
                    "conclusion": "success",
                    "started_at": "2026-11-08T10:00:00Z",
                    "completed_at": "2026-11-08T10:01:00Z",
                }
            ],
        }

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    exit_code = flake_report.main(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "9", "--attempt", "2"]
    )
    assert exit_code == 0
    assert "Rerun summary unavailable" in capsys.readouterr().out


def test_rerun_summary_enrichment_deadline_stops_log_reads_and_still_prints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = FakeClock()

    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        endpoint = endpoint_args[0]
        if endpoint.endswith("/actions/runs/9"):
            return {"name": "Risk Service CI", "event": "push", "head_branch": "feature/a"}
        if "/attempts/1/jobs" in endpoint:
            return {
                "total_count": 1,
                "jobs": [
                    {
                        "id": 11,
                        "name": "Journeys shard",
                        "conclusion": "failure",
                        "started_at": "2026-11-08T10:00:00Z",
                        "completed_at": "2026-11-08T10:01:00Z",
                    }
                ],
            }
        if "/attempts/2/jobs" in endpoint:
            return {
                "total_count": 1,
                "jobs": [
                    {
                        "id": 12,
                        "name": "Journeys shard",
                        "conclusion": "failure",
                        "started_at": "2026-11-08T11:00:00Z",
                        "completed_at": "2026-11-08T11:01:00Z",
                    }
                ],
            }
        return {
            "total_count": 1,
            "jobs": [
                {
                    "id": 13,
                    "name": "Journeys shard",
                    "conclusion": "success",
                    "started_at": "2026-11-08T12:00:00Z",
                    "completed_at": "2026-11-08T12:01:00Z",
                }
            ],
        }

    reads: list[int] = []

    def slow_gh_api(
        *endpoint_args: str, allow_escape_sequences: bool = False, retries: int = 4
    ) -> str:
        reads.append(len(reads))
        clock.now = 10_000.0  # one optional read eats the whole enrichment budget
        return "pytest failure in tests/a.py"

    monkeypatch.setattr(flake_report, "time", clock)
    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    monkeypatch.setattr(flake_report, "gh_api", slow_gh_api)
    exit_code = flake_report.main(
        ["rerun-summary", "--repo", "CedynGroup/aequorOS", "--run-id", "9", "--attempt", "3"]
    )
    assert exit_code == 0
    output = capsys.readouterr()
    # The summary still prints, with at most one log read spent before the
    # deadline stopped the optional enrichment.
    assert "Journeys shard" in output.out
    assert len(reads) == 1
    assert "deadline" in output.out


# --- weekly mode --------------------------------------------------------------


def success_run(run_id: int, created_at: str = "2026-11-08T10:00:00Z") -> Any:
    return flake_report.RunRecord(
        run_id=run_id,
        workflow="Risk Service CI",
        event="push",
        head_branch="main",
        head_sha="a" * 40,
        run_attempt=1,
        conclusion="success",
        created_at=created_at,
    )


def test_thresholds_count_only_rerun_only_passes() -> None:
    """The policy counts rerun-only passes; same-SHA passes elsewhere and plain
    failures on one attempt do not move a signature across a threshold."""
    now = datetime(2026, 11, 9, tzinfo=UTC)
    recent = "2026-11-08T10:00:00Z"
    events = [
        event("flake:a", rerun=True, created_at=recent, run_id=1),
        event("flake:a", rerun=True, created_at=recent, run_id=2),
        event("flake:a", rerun=False, same_sha_pass=True, created_at=recent, run_id=3),
        event("flake:a", rerun=False, created_at=recent, run_id=4),
    ]
    breaches = flake_report.evaluate_thresholds(events, now)
    assert len(breaches) == 1
    assert breaches[0].rerun_passes_in_window == 2
    assert breaches[0].action == "needs flaky-test issue with owner"


def test_thresholds_window_boundary_is_inclusive() -> None:
    """An event exactly 14 days back is inside the window; one second earlier is
    outside, so a long-lived signature does not stay escalated forever."""
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    at_start = "2026-10-26T12:00:00Z"
    before_start = "2026-10-26T11:59:59Z"
    events = [
        event("flake:a", rerun=True, created_at=at_start, run_id=1),
        event("flake:a", rerun=True, created_at=at_start, run_id=2),
        event("flake:a", rerun=True, created_at=before_start, run_id=3),
    ]
    breaches = flake_report.evaluate_thresholds(events, now)
    assert [breach.rerun_passes_in_window for breach in breaches] == [2]


def test_thresholds_above_three_stay_at_fix_or_quarantine() -> None:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    recent = "2026-11-08T10:00:00Z"
    five = [event("flake:x", rerun=True, created_at=recent, run_id=i) for i in range(5)]
    breaches = flake_report.evaluate_thresholds(five, now)
    assert len(breaches) == 1
    assert breaches[0].rerun_passes_in_window == 5
    assert breaches[0].action == "fix or quarantine within 7 days"


def test_threshold_breach_names_its_signature() -> None:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    two = [
        event(
            "pytest:tests/db/test_document_deletion_timeout_integration.py",
            rerun=True,
            created_at="2026-11-08T10:00:00Z",
            run_id=i,
        )
        for i in range(2)
    ]
    breaches = flake_report.evaluate_thresholds(two, now)
    assert [breach.signature for breach in breaches] == [
        "pytest:tests/db/test_document_deletion_timeout_integration.py"
    ]


def test_render_weekly_report_lists_breaches_and_the_ranking() -> None:
    runs = [success_run(1)]
    events = [
        event(
            "pytest:tests/db/test_document_deletion_timeout_integration.py", rerun=True, run_id=1
        ),
        event(
            "pytest:tests/db/test_document_deletion_timeout_integration.py", rerun=True, run_id=2
        ),
    ]
    report = flake_report.build_report(runs=runs, events=events, meta=REPORT_META)
    breaches = flake_report.evaluate_thresholds(events, datetime(2026, 11, 9, tzinfo=UTC))
    markdown = flake_report.render_weekly_report(report, breaches, window_days=14)
    assert "# Weekly CI flake report" in markdown
    assert "## Threshold crossings (approved flake policy)" in markdown
    assert "needs flaky-test issue with owner" in markdown
    assert "pytest:tests/db/test_document_deletion_timeout_integration.py" in markdown
    assert "This report never creates issues or comments" in markdown
    assert "# CI flake ranking" in markdown


def test_render_weekly_report_without_breaches_says_so() -> None:
    report = flake_report.build_report(runs=[success_run(1)], events=[], meta=REPORT_META)
    markdown = flake_report.render_weekly_report(report, [], window_days=14)
    assert "## Threshold crossings (approved flake policy)" in markdown
    assert "No signature reached the policy thresholds" in markdown


def test_a_duplicate_with_no_success_of_its_own_stays_unrecovered() -> None:
    """Two failures cannot share one success when the recovery attempt held
    fewer same-named jobs: the unmatched failure is left unrecovered instead
    of attributing another job's success (and minutes) to it."""
    attempts = {
        1: [
            job("Claude review verdict", "failure", 5.0, 401),
            job("Claude review verdict", "failure", 4.0, 402),
        ],
        # The recovery attempt holds only one job of the name: the other
        # duplicate vanished, so its recovery cannot be attributed.
        2: [job("Claude review verdict", "success", 6.0, 403)],
    }
    passes = flake_report.compare_attempts(attempts)
    assert [(p.failed_job.job_id, p.passed_attempt, p.passed_minutes) for p in passes] == [
        (401, 2, 6.0)
    ]


def test_duplicate_rerun_passes_stay_distinct_through_event_creation() -> None:
    """The rerun-pass lookup is keyed by job id, so two same-named failures in
    one attempt each keep their own recovery minutes in the events."""
    run = flake_report.RunRecord(
        run_id=7,
        workflow="Web CI",
        event="push",
        head_branch="main",
        head_sha="a" * 40,
        run_attempt=2,
        conclusion="success",
        created_at="2026-11-08T10:00:00Z",
    )
    attempts = {
        1: [
            job("Claude review verdict", "failure", 5.0, 501),
            job("Claude review verdict", "failure", 4.0, 502),
        ],
        2: [
            job("Claude review verdict", "success", 6.0, 503),
            job("Claude review verdict", "success", 7.0, 504),
        ],
    }
    rerun_passes = {
        (7, p.failed_attempt, p.failed_job.job_id): p
        for p in flake_report.compare_attempts(attempts)
    }
    findings_by_job = {
        501: [flake_report.Finding("unclassified", "unclassified:Claude review verdict")],
        502: [flake_report.Finding("unclassified", "unclassified:Claude review verdict")],
    }
    events = flake_report._build_events(
        [
            (run, 1, job("Claude review verdict", "failure", 5.0, 501)),
            (run, 1, job("Claude review verdict", "failure", 4.0, 502)),
        ],
        {
            7: {
                1: [
                    job("Claude review verdict", "failure", 5.0, 501),
                    job("Claude review verdict", "failure", 4.0, 502),
                ]
            }
        },
        rerun_passes,
        set(),
        findings_by_job,
    )
    minutes = sorted(e.rerun_minutes for e in events)
    assert minutes == [6.0, 7.0]


def test_an_off_policy_window_labels_its_thresholds_exploratory() -> None:
    """A non-policy window reads as a reading, not an obligation."""
    report = flake_report.build_report(runs=[success_run(1)], events=[], meta=REPORT_META)
    breaches = [flake_report.ThresholdBreach("flake:a", 3, "fix or quarantine within 7 days")]
    markdown = flake_report.render_weekly_report(report, breaches, window_days=30)
    assert (
        "## Threshold crossings (exploratory 30-day window, "
        "not the approved 14-day policy)" in markdown
    )
    assert "exploratory reading, not policy determinations" in markdown
    assert "Action the 14-day policy would require (exploratory)" in markdown
    assert "| Required action |" not in markdown
    # The policy window keeps the binding wording.
    policy = flake_report.render_weekly_report(report, breaches, window_days=14)
    assert "| Required action |" in policy


def test_a_collection_cutoff_adds_the_time_budget_caveat() -> None:
    report = flake_report.build_report(runs=[success_run(1)], events=[], meta=REPORT_META)
    markdown = flake_report.render_weekly_report(
        report,
        [],
        window_days=14,
        collection_cutoff=(
            "collection stopped at the time budget with 3 failed-job log(s) unfetched"
        ),
    )
    assert "Collection caveat" in markdown
    assert "3 failed-job log(s) unfetched" in markdown


def test_unreadable_logs_get_their_own_collection_caveat(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A log that could not be read is not a time-budget cutoff.

    The caveat must say what actually happened (unreadable logs), and the
    empty threshold result under it must not read as conclusive.
    """
    events = [
        event("log-unavailable:Dashboard journeys shard 4/4", kind="unclassified", run_id=1),
        event("log-unavailable:Dashboard journeys shard 4/4", kind="unclassified", run_id=2),
    ]
    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        lambda options, log=print, notes=None: ([success_run(1)], events),
    )
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    output = capsys.readouterr()
    assert "Collection caveat: 2 failed-job log(s) unreadable" in output.out
    # An unreadable log is not a time-budget cutoff: the caveat must say what
    # actually happened, and the empty threshold result under it must not read
    # as conclusive.
    assert "time budget" not in output.out
    assert "this result is inconclusive" in output.out
    assert "No signature reached the policy thresholds in this window" not in output.out


def test_caveats_precede_an_empty_threshold_result_and_label_it_inconclusive() -> None:
    """An incomplete collection must not read as a clean bill of health.

    The caveats render before the threshold section, and an empty crossing
    table under an incomplete collection is labelled inconclusive rather
    than left to look like "no signature reached the thresholds".
    """
    report = flake_report.build_report(runs=[success_run(1)], events=[], meta=REPORT_META)
    markdown = flake_report.render_weekly_report(
        report,
        [],
        window_days=14,
        truncated=[("Web CI", "2026-11-05")],
        collection_cutoff=(
            "collection stopped at the time budget with 1 failed-job log(s) unfetched"
        ),
    )
    caveat_position = markdown.index("Collection caveat")
    threshold_position = markdown.index("## Threshold crossings")
    assert caveat_position < threshold_position
    assert "this result is inconclusive" in markdown
    assert "No signature reached the policy thresholds in this window" not in markdown
    # A complete collection still renders the plain, conclusive wording.
    complete = flake_report.render_weekly_report(report, [], window_days=14)
    assert "inconclusive" not in complete
    assert "No signature reached the policy thresholds" in complete


def test_a_truncated_workflow_caveat_names_the_requested_window_start() -> None:
    """The caveat compares against the requested window, not the oldest run.

    ``build_report`` narrows ``window.since`` to the oldest collected run, so
    comparing the capped workflow against it would compare a workflow with
    itself; ``requested_since`` names the part of the window that is missing.
    """
    late_run = success_run(1, created_at="2026-11-05T10:00:00Z")
    report = flake_report.build_report(runs=[late_run], events=[], meta=REPORT_META)
    assert report["meta"]["window"]["since"] == "2026-11-05"
    assert report["meta"]["window"]["requested_since"] == "2026-10-11"
    markdown = flake_report.render_weekly_report(
        report,
        [],
        window_days=14,
        truncated=[("Web CI", "2026-11-05")],
    )
    assert "does not precede the requested window start 2026-10-11" in markdown


def test_weekly_artifact_writes_fail_independently(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """An unwritable ``--out-md`` must not stop a writable ``--out-json``."""
    events = [
        event(
            "pytest:tests/db/test_document_deletion_timeout_integration.py", rerun=True, run_id=1
        ),
        event(
            "pytest:tests/db/test_document_deletion_timeout_integration.py", rerun=True, run_id=2
        ),
    ]
    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        lambda options, log=print, notes=None: ([success_run(1)], events),
    )
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    parser = flake_report.build_parser()
    args = parser.parse_args(
        [
            "weekly",
            "--cache-dir",
            str(tmp_path),
            "--out-md",
            str(blocked),  # a directory: writing raises IsADirectoryError
            "--out-json",
            str(tmp_path / "weekly.json"),
        ]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    output = capsys.readouterr()
    assert "markdown artifact write failed" in output.err
    payload = json.loads((tmp_path / "weekly.json").read_text(encoding="utf-8"))
    assert payload["thresholds"][0]["signature"] == (
        "pytest:tests/db/test_document_deletion_timeout_integration.py"
    )
    assert payload["collection"] == {"caveat": None, "truncated_workflows": []}


def test_cache_setup_failure_also_replaces_requested_artifacts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A refused cache setup must not leave a stale report in the artifacts."""

    def fail(raw: str) -> None:
        raise OSError("cannot create cache dir")

    monkeypatch.setattr(flake_report, "_private_cache_dir", fail)
    stale_md = tmp_path / "weekly.md"
    stale_json = tmp_path / "weekly.json"
    stale_md.write_text("stale ranking", encoding="utf-8")
    stale_json.write_text('{"stale": true}', encoding="utf-8")
    parser = flake_report.build_parser()
    args = parser.parse_args(
        [
            "weekly",
            "--cache-dir",
            str(tmp_path),
            "--out-md",
            str(stale_md),
            "--out-json",
            str(stale_json),
        ]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    assert "Weekly flake report unavailable" in stale_md.read_text(encoding="utf-8")
    payload = json.loads(stale_json.read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"
    assert "cannot create cache dir" in payload["error"]


def test_collection_failure_records_the_unavailable_state_in_requested_artifacts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Requested files must not keep a stale report when collection fails."""

    def fail(
        options: Any, log: Any = print, notes: list[str] | None = None
    ) -> tuple[list[Any], list[Any]]:
        raise RuntimeError("gh api failed (1): Server Error (HTTP 502)")

    monkeypatch.setattr(flake_report, "collect_report_data", fail)
    stale_md = tmp_path / "weekly.md"
    stale_json = tmp_path / "weekly.json"
    stale_md.write_text("stale ranking", encoding="utf-8")
    stale_json.write_text('{"stale": true}', encoding="utf-8")
    parser = flake_report.build_parser()
    args = parser.parse_args(
        [
            "weekly",
            "--cache-dir",
            str(tmp_path),
            "--out-md",
            str(stale_md),
            "--out-json",
            str(stale_json),
        ]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    assert "Weekly flake report unavailable" in stale_md.read_text(encoding="utf-8")
    payload = json.loads(stale_json.read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"
    assert "Server Error (HTTP 502)" in payload["error"]


def test_fetch_findings_stops_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(flake_report, "fetch_job_log", lambda *args: calls.append(args) or "")
    run = flake_report.RunRecord(
        run_id=1,
        workflow="Risk Service CI",
        event="pull_request",
        head_branch="feature/a",
        head_sha="abc123",
        run_attempt=1,
        conclusion="failure",
        created_at="2026-11-08T10:00:00Z",
    )
    failed_jobs = [
        (run, 1, job("Risk Service CI shard 1/6", "failure", 20.0, 1)),
        (run, 1, job("Risk Service CI shard 2/6", "failure", 20.0, 2)),
    ]
    options = flake_report.ReportOptions(
        repo="CedynGroup/aequorOS",
        workflow_files=["risk-service.yml"],
        min_runs_per_workflow=1,
        since="2026-11-01",
        cache_dir=tmp_path,
        deadline=0.0,
    )
    findings = flake_report._fetch_findings(
        "CedynGroup/aequorOS", failed_jobs, options, lambda _m: None
    )
    assert calls == []
    assert [f.signature for f in findings[1]] == ["log-budget-exhausted:Risk Service CI shard 1/6"]
    assert [f.signature for f in findings[2]] == ["log-budget-exhausted:Risk Service CI shard 2/6"]


def test_weekly_command_bounds_collection_within_the_runner_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(
        options: Any,
        log: Any = print,
        notes: list[str] | None = None,
    ) -> tuple[list[Any], list[Any]]:
        captured["options"] = options
        return [], []

    monkeypatch.setattr(flake_report, "collect_report_data", fake_collect)
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    assert captured["options"].deadline is not None


def test_run_listing_skips_past_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The budget bounds every collection stage, not only the log fetches.

    A workflow whose listing cannot start past the budget is skipped with a
    visible note instead of a silently biased short window.
    """
    options = flake_report.ReportOptions(
        repo="CedynGroup/aequorOS",
        workflow_files=["risk-service.yml", "web.yml"],
        min_runs_per_workflow=1,
        since="2026-11-01",
        cache_dir=Path("/tmp/unused"),
        deadline=0.0,
    )

    def fail(*args: object, **kwargs: object) -> list[Any]:
        raise AssertionError("no listing may start past the deadline")

    monkeypatch.setattr(flake_report, "list_workflow_runs", fail)
    notes: list[str] = []
    assert flake_report._list_all_runs(options, lambda _m: None, notes) == []
    assert notes == [
        "run listing for risk-service.yml skipped at the time budget",
        "run listing for web.yml skipped at the time budget",
    ]


def test_job_listings_stop_past_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Job listing stops at the budget with a note naming the unfetched runs."""
    options = flake_report.ReportOptions(
        repo="CedynGroup/aequorOS",
        workflow_files=["risk-service.yml"],
        min_runs_per_workflow=1,
        since="2026-11-01",
        cache_dir=Path("/tmp/unused"),
        deadline=0.0,
    )

    def fail(*args: object, **kwargs: object) -> list[Any]:
        raise AssertionError("no job listing may start past the deadline")

    monkeypatch.setattr(flake_report, "fetch_attempt_jobs", fail)
    notes: list[str] = []
    jobs_by_run_attempt, jobs_by_run = flake_report._fetch_attempt_jobs(
        "CedynGroup/aequorOS", {1: {1}, 2: {1}}, options, lambda _m: None, notes
    )
    assert jobs_by_run_attempt == {} and jobs_by_run == {}
    assert notes == ["job listings for 2 run(s) unfetched at the time budget"]


def test_run_listing_reports_the_deadline_reason_between_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paging continues only while the budget lasts, not once per workflow."""
    options = flake_report.ReportOptions(
        repo="CedynGroup/aequorOS",
        workflow_files=["risk-service.yml"],
        min_runs_per_workflow=1000,
        since="2026-11-01",
        cache_dir=Path("/tmp/unused"),
        deadline=time.monotonic() + 0.05,
    )

    def fail(*args: object, **kwargs: object) -> dict[str, Any]:
        raise AssertionError("no page may be fetched past the deadline")

    monkeypatch.setattr(flake_report, "gh_api_json", fail)
    monkeypatch.setattr(flake_report.time, "monotonic", lambda: options.deadline + 1.0)
    records, reason = flake_report.list_workflow_runs(
        "CedynGroup/aequorOS",
        "risk-service.yml",
        1000,
        "2026-11-01",
        lambda _m: None,
        deadline=options.deadline,
    )
    assert records == []
    assert reason == "deadline"


def test_attempt_listing_refuses_the_api_past_the_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cached attempts are free; an uncached attempt never starts past the budget."""
    deadline = time.monotonic() - 1.0

    def fail(*args: object, **kwargs: object) -> dict[str, Any]:
        raise AssertionError("the API call must not start past the deadline")

    monkeypatch.setattr(flake_report, "gh_api_json", fail)
    with pytest.raises(RuntimeError, match="time budget reached before job listing"):
        flake_report.fetch_attempt_jobs(
            "CedynGroup/aequorOS", 9, 2, cache_dir=tmp_path, deadline=deadline
        )


def test_weekly_command_flags_the_time_budget_cutoff_in_the_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    events = [
        event("log-budget-exhausted:Dashboard journeys shard 4/4", kind="unclassified", run_id=1),
        event(
            "pytest:tests/db/test_document_deletion_timeout_integration.py", rerun=True, run_id=1
        ),
        event(
            "pytest:tests/db/test_document_deletion_timeout_integration.py", rerun=True, run_id=2
        ),
    ]
    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        lambda options, log=print, notes=None: ([success_run(1)], events),
    )
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    output = capsys.readouterr()
    assert "Collection caveat" in output.out
    assert "1 failed-job log(s) unfetched" in output.out


def test_weekly_command_renders_thresholds_and_the_ranking(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    runs = [success_run(1)]
    events = [
        event(
            "pytest:tests/db/test_document_deletion_timeout_integration.py", rerun=True, run_id=1
        ),
        event(
            "pytest:tests/db/test_document_deletion_timeout_integration.py", rerun=True, run_id=2
        ),
    ]
    captured: dict[str, Any] = {}

    def fake_collect(
        options: Any,
        log: Any = print,
        notes: list[str] | None = None,
    ) -> tuple[list[Any], list[Any]]:
        captured["options"] = options
        log("  job listings: 25/25 runs")
        return runs, events

    monkeypatch.setattr(flake_report, "collect_report_data", fake_collect)
    parser = flake_report.build_parser()
    args = parser.parse_args(
        ["weekly", "--repo", "CedynGroup/aequorOS", "--cache-dir", str(tmp_path)]
    )
    exit_code = flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    assert exit_code == 0
    output = capsys.readouterr()
    assert "# Weekly CI flake report" in output.out
    assert "needs flaky-test issue with owner" in output.out
    assert "pytest:tests/db/test_document_deletion_timeout_integration.py" in output.out
    # Collection progress is a diagnostic: stdout is the Markdown step summary.
    assert "job listings: 25/25 runs" in output.err
    assert "job listings" not in output.out
    # The data window matches the policy window (14 days back from `now`).
    assert captured["options"].since == "2026-10-26"


def test_weekly_command_window_days_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(
        options: Any,
        log: Any = print,
        notes: list[str] | None = None,
    ) -> tuple[list[Any], list[Any]]:
        captured["options"] = options
        return [], []

    monkeypatch.setattr(flake_report, "collect_report_data", fake_collect)
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--window-days", "7", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    assert captured["options"].since == "2026-11-02"


def test_weekly_command_survives_api_errors_with_a_visible_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def fail(
        options: Any,
        log: Any = print,
        notes: list[str] | None = None,
    ) -> tuple[list[Any], list[Any]]:
        raise RuntimeError("gh api failed (1): Server Error (HTTP 502)")

    monkeypatch.setattr(flake_report, "collect_report_data", fail)
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args) == 0
    output = capsys.readouterr()
    assert "Weekly flake report unavailable" in output.out
    assert "Server Error (HTTP 502)" in output.out


def test_weekly_command_survives_api_errors_through_the_real_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The boundary stub, not the collector, is replaced for the failure path.

    ``collect_report_data`` carries the recovery matching, classification and
    event construction; stubbing it would bypass those contracts. For the API
    failure case only the external boundary is replaced, so the unavailable
    note is produced by the real collector.
    """

    def fail(*endpoint_args: str, **_kwargs: Any) -> Any:
        raise RuntimeError("gh api failed (1): Server Error (HTTP 502)")

    monkeypatch.setattr(flake_report, "gh_api_json", fail)
    monkeypatch.setattr(flake_report, "gh_api", fail)
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args) == 0
    output = capsys.readouterr()
    assert "Weekly flake report unavailable" in output.out
    assert "Server Error (HTTP 502)" in output.out


def test_unclassified_logs_also_get_the_collection_caveat(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A readable log with no recognized finding is incomplete evidence too.

    One recognized failure on a rerun plus an unclassified recurrence must
    not read as a conclusive empty threshold result.
    """
    events = [
        # One unclassified rerun pass: below the threshold, so the crossing
        # table is empty — which must not read as conclusive, because this
        # failure was never identified.
        event("unclassified:dashboard", kind="unclassified", rerun=True, run_id=1),
    ]
    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        lambda options, log=print, notes=None: ([success_run(1)], events),
    )
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    output = capsys.readouterr()
    assert (
        "Collection caveat: 1 failed-job log(s) yielded no recognized failure signature"
        in output.out
    )
    assert "this result is inconclusive" in output.out


def test_the_displayed_weekly_command_is_copy_pasteable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The command line shown in the report must parse with the weekly parser."""
    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        lambda options, log=print, notes=None: ([success_run(1)], []),
    )
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    out = capsys.readouterr().out
    displayed = next(line for line in out.splitlines() if line.startswith("- Command:"))
    command = displayed.split("`")[1]
    # Drop "python3 <script>": the remaining tokens must parse as a weekly command.
    assert flake_report.build_parser().parse_args(command.split()[2:]) is not None
    assert "--since" not in command


def test_weekly_command_collects_through_the_gh_api_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The weekly command runs end to end over mocked ``gh`` endpoints.

    Unlike the tests that stub ``collect_report_data``, this one exercises the
    command through its real boundary (the ``gh api`` calls): run listing,
    per-attempt job listing, log reads and the summary rendering, so a break
    between those stages cannot pass silently.
    """
    runs_payload = fixture_json("workflow_runs.json")
    failed_jobs_payload = fixture_json("attempt_jobs_failed.json")
    passed_jobs_payload = fixture_json("attempt_jobs_passed.json")
    log_text = "\n".join(fixture_text("pytest_doc_deletion.log"))

    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        endpoint = endpoint_args[0]
        if "/actions/workflows/" in endpoint and "/runs?" in endpoint:
            page = int(endpoint.rsplit("page=", 1)[1])
            return runs_payload if page == 1 else {"total_count": 0, "workflow_runs": []}
        if "/attempts/" in endpoint and "/jobs?" in endpoint:
            attempt = int(endpoint.rsplit("/attempts/", 1)[1].split("/", 1)[0])
            return passed_jobs_payload if attempt >= 4 else failed_jobs_payload
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    def fake_gh_api(*endpoint_args: str, **kwargs: Any) -> str:
        assert "/logs" in endpoint_args[0]
        return log_text

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    monkeypatch.setattr(flake_report, "gh_api", fake_gh_api)
    parser = flake_report.build_parser()
    args = parser.parse_args(
        [
            "weekly",
            "--repo",
            "CedynGroup/aequorOS",
            "--workflows",
            "risk-service.yml",
            "--cache-dir",
            str(tmp_path),
        ]
    )
    exit_code = flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    assert exit_code == 0
    output = capsys.readouterr()
    assert "# Weekly CI flake report" in output.out
    assert "## Threshold crossings" in output.out
    assert "# CI flake ranking" in output.out
    assert "unavailable" not in output.out
    # The collected data came through the API boundary, not a stub.
    assert "Listing runs for risk-service.yml" in output.err
    assert "Built " in output.err
    assert "pytest:tests/db/test_document_deletion_timeout_integration.py" in output.out


def test_weekly_command_writes_markdown_and_json_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        lambda options, log=print, notes=None: (
            [success_run(1)],
            [event("flake:a", rerun=True, run_id=1), event("flake:a", rerun=True, run_id=2)],
        ),
    )
    out_md = tmp_path / "weekly.md"
    out_json = tmp_path / "weekly.json"
    parser = flake_report.build_parser()
    args = parser.parse_args(
        [
            "weekly",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out-md",
            str(out_md),
            "--out-json",
            str(out_json),
        ]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    assert "# Weekly CI flake report" in out_md.read_text()
    payload = json.loads(out_json.read_text())
    assert payload["thresholds"][0]["action"] == "needs flaky-test issue with owner"
    assert payload["report"]["meta"]["repo"]
    # The step summary keeps working even with artifact paths given: stdout
    # always carries the Markdown, write status goes to stderr.
    output = capsys.readouterr()
    assert "# Weekly CI flake report" in output.out
    assert f"Wrote {out_md}" in output.err


def test_weekly_command_warns_when_a_workflow_was_truncated_by_the_run_cap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A workflow file stopped by the run cap before the window start must not
    let the empty threshold list read as a complete measurement. The cap is
    enforced per workflow file, so the caveat is keyed by the file name (a
    file can present several API workflow names in one window, and per-name
    reconstruction could keep each name under the cap and hide the
    truncation); only a collection that actually hit the cap is flagged."""
    truncated_run = flake_report.RunRecord(
        run_id=1,
        workflow="Risk Service CI",
        event="push",
        head_branch="main",
        head_sha="a" * 40,
        run_attempt=1,
        conclusion="success",
        created_at="2026-11-05T00:00:00Z",
    )

    def collector_with(note: str | None) -> Any:
        def scenario(
            options: Any,
            log: Any = print,
            notes: list[str] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            if note is not None and notes is not None:
                notes.append(note)
            return [truncated_run], []

        return scenario

    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        collector_with("run-cap:risk-service.yml:2026-11-05T00:00:00Z"),
    )
    parser = flake_report.build_parser()
    args = parser.parse_args(
        ["weekly", "--min-runs-per-workflow", "2", "--cache-dir", str(tmp_path / "a")]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    output = capsys.readouterr().out
    assert "Threshold caveat" in output
    assert "risk-service.yml" in output
    assert "undercounted" in output

    # No run-cap note: the listing covered the window on its own, no caveat.
    monkeypatch.setattr(flake_report, "collect_report_data", collector_with(None))
    args = parser.parse_args(
        ["weekly", "--min-runs-per-workflow", "2", "--cache-dir", str(tmp_path / "b")]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    assert "Threshold caveat" not in capsys.readouterr().out

    # The cap stopped on the window-start date itself: earlier runs on that
    # date may still be missing, so coverage stays uncertain.
    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        collector_with("run-cap:risk-service.yml:2026-10-26T23:00:00Z"),
    )
    args = parser.parse_args(
        ["weekly", "--min-runs-per-workflow", "2", "--cache-dir", str(tmp_path / "c")]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    assert "Threshold caveat" in capsys.readouterr().out

    # The cap signal only exists when the cap is actually enforced: an
    # unlimited listing (cap 0) never produces a run-cap note.
    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        collector_with("run-cap:risk-service.yml:2026-11-05T00:00:00Z"),
    )
    args = parser.parse_args(
        ["weekly", "--min-runs-per-workflow", "0", "--cache-dir", str(tmp_path / "d")]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    assert "Threshold caveat" in capsys.readouterr().out


def test_run_cap_note_is_only_emitted_when_the_cap_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlimited listing (min-runs 0) is never "capped": the run-cap note
    must not fire, so the weekly summary shows no truncation caveat."""
    pages: list[int] = []

    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        pages.append(len(pages))
        if len(pages) > 1:
            return {"workflow_runs": [], "total_count": 1}
        return {
            "workflow_runs": [
                {
                    "id": len(pages),
                    "name": "Risk Service CI",
                    "event": "push",
                    "head_branch": "main",
                    "head_sha": "a" * 40,
                    "run_attempt": 1,
                    "conclusion": "success",
                    "created_at": "2026-11-08T10:00:00Z",
                }
            ],
            "total_count": 1,
        }

    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    records, reason = flake_report.list_workflow_runs(
        "CedynGroup/aequorOS",
        "risk-service.yml",
        min_runs=0,
        since="2026-10-01",
        log=lambda message: None,
    )
    assert reason is None
    assert len(records) == 1


def test_weekly_command_survives_an_unwritable_cache_dir(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The report is a visibility job: a cache error becomes a visible note,
    never a failed run."""

    def refuse(raw: str) -> Path:
        raise OSError("disk full while creating cache dir")

    monkeypatch.setattr(flake_report, "_private_cache_dir", refuse)
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path / "cache")])
    assert flake_report.cmd_weekly(args) == 0
    output = capsys.readouterr()
    assert "Weekly flake report unavailable" in output.out
    assert "disk full while creating cache dir" in output.out


def test_weekly_command_refuses_a_shared_cache_dir(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The cache dir must stay private to the current user, as in report mode."""
    shared = tmp_path / "shared-cache"
    shared.mkdir()
    shared.chmod(0o755)

    def fail(*args: object) -> tuple[list[Any], list[Any]]:
        raise AssertionError("no data may be collected into a shared cache dir")

    monkeypatch.setattr(flake_report, "collect_report_data", fail)
    stale_md = tmp_path / "weekly.md"
    stale_md.write_text("stale ranking", encoding="utf-8")
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(shared), "--out-md", str(stale_md)])
    assert flake_report.cmd_weekly(args) == 2
    assert "not private to the current user" in capsys.readouterr().err
    assert "Weekly flake report unavailable" in stale_md.read_text(encoding="utf-8")


def test_the_collection_deadline_stops_job_listings(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = FakeClock()

    def slow_fetch(
        repo: str, run_id: int, attempt: int, cache_dir: Any, deadline: Any = None
    ) -> list[Any]:
        clock.now = flake_report.WEEKLY_COLLECTION_BUDGET_MINUTES * 60 + 1
        return []

    monkeypatch.setattr(flake_report, "time", clock)
    monkeypatch.setattr(flake_report, "fetch_attempt_jobs", slow_fetch)
    options = flake_report.ReportOptions(
        repo="CedynGroup/aequorOS",
        workflow_files=["risk-service.yml"],
        min_runs_per_workflow=1,
        since="2026-10-26",
        cache_dir=Path("/tmp/unused-cache"),
        deadline=1.0,
    )
    notes: list[str] = []
    jobs_by_run_attempt, _jobs_by_run = flake_report._fetch_attempt_jobs(
        "CedynGroup/aequorOS", {1: {1}, 2: {1}}, options, lambda message: None, notes
    )
    # The second run's listing is not fetched once the budget passed, and the
    # cutoff is reported so the summary can say the counts are undercounted.
    assert list(jobs_by_run_attempt) == [1]
    assert notes and "time budget" in notes[0]


def test_the_collection_deadline_stops_run_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = FakeClock()
    pages: list[int] = []

    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        pages.append(len(pages))
        clock.now = 10_000.0  # one page read eats the whole remaining budget
        return {
            "workflow_runs": [
                {
                    "id": 1,
                    "name": "Risk Service CI",
                    "event": "push",
                    "head_branch": "main",
                    "head_sha": "a" * 40,
                    "run_attempt": 1,
                    "conclusion": "success",
                    "created_at": "2026-11-08T10:00:00Z",
                }
            ],
            "total_count": 100,
        }

    monkeypatch.setattr(flake_report, "time", clock)
    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    records, reason = flake_report.list_workflow_runs(
        "CedynGroup/aequorOS",
        "risk-service.yml",
        min_runs=100,
        since="2026-10-01",
        log=lambda message: None,
        deadline=clock.now + 5.0,
    )
    # The second page is not fetched once the budget passed, and the stop is
    # reported as a time-budget cutoff so the summary can say the counts are
    # undercounted.
    assert len(records) == 1
    assert reason == "deadline"


def test_a_window_covered_listing_reports_no_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

    def fake_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        return {
            "workflow_runs": [
                {
                    "id": 1,
                    "name": "Risk Service CI",
                    "event": "push",
                    "head_branch": "main",
                    "head_sha": "a" * 40,
                    "run_attempt": 1,
                    "conclusion": "success",
                    # Older than the window start: the listing has covered
                    # the requested window completely.
                    "created_at": "2026-09-01T10:00:00Z",
                }
            ],
            "total_count": 100,
        }

    monkeypatch.setattr(flake_report, "time", FakeClock())
    monkeypatch.setattr(flake_report, "gh_api_json", fake_gh_api_json)
    records, reason = flake_report.list_workflow_runs(
        "CedynGroup/aequorOS",
        "risk-service.yml",
        min_runs=100,
        since="2026-10-01",
        log=lambda message: None,
    )
    # A complete window coverage is not a cutoff of any kind; the pre-window
    # run itself is filtered out of the collected records.
    assert records == []
    assert reason is None


def test_the_collection_deadline_stops_between_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = FakeClock()

    def slow_fetch(
        repo: str, run_id: int, attempt: int, cache_dir: Any, deadline: Any = None
    ) -> list[Any]:
        if attempt == 1:
            clock.now = flake_report.WEEKLY_COLLECTION_BUDGET_MINUTES * 60 + 1
        return []

    monkeypatch.setattr(flake_report, "time", clock)
    monkeypatch.setattr(flake_report, "fetch_attempt_jobs", slow_fetch)
    options = flake_report.ReportOptions(
        repo="CedynGroup/aequorOS",
        workflow_files=["risk-service.yml"],
        min_runs_per_workflow=1,
        since="2026-10-26",
        cache_dir=Path("/tmp/unused-cache"),
        deadline=1.0,
    )
    notes: list[str] = []
    jobs_by_run_attempt, _jobs_by_run = flake_report._fetch_attempt_jobs(
        "CedynGroup/aequorOS", {1: {1, 2}}, options, lambda message: None, notes
    )
    # A run whose later attempts were not fetched before the budget passed is
    # not silently treated as complete rerun evidence.
    assert list(jobs_by_run_attempt[1]) == [1]
    assert notes and "time budget" in notes[0]


def test_log_budget_placeholders_do_not_cross_policy_thresholds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    events = [
        event("log-budget-exhausted:Dashboard journeys shard 4/4", kind="unclassified", run_id=1),
        event("log-budget-exhausted:Dashboard journeys shard 4/4", kind="unclassified", run_id=2),
        event("pytest:tests/a.py", rerun=True, run_id=3),
    ]

    def fake_collect(options: Any, log: Any = print, notes: list[str] | None = None) -> Any:
        return [success_run(1)], events

    monkeypatch.setattr(flake_report, "collect_report_data", fake_collect)
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    output = capsys.readouterr()
    # Two uninspected logs must not read as two rerun-only passes of a
    # signature, and the missing logs stay accounted for as a caveat.
    assert "needs flaky-test issue with owner" not in output.out
    assert "log-budget-exhausted" not in output.out
    assert "2 failed-job log(s) unfetched" in output.out


def test_run_cap_truncation_uses_the_full_window_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    runs = [
        success_run(1, created_at="2026-10-26T14:00:00Z"),
        success_run(2, created_at="2026-11-08T10:00:00Z"),
    ]

    def fake_collect(options: Any, log: Any = print, notes: list[str] | None = None) -> Any:
        if notes is not None:
            notes.append("run-cap:risk-service.yml:2026-10-26T14:00:00Z")
        return runs, []

    monkeypatch.setattr(flake_report, "collect_report_data", fake_collect)
    parser = flake_report.build_parser()
    args = parser.parse_args(
        ["weekly", "--min-runs-per-workflow", "2", "--cache-dir", str(tmp_path)]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    # The cap was reached and the oldest run is two hours inside the window
    # (same date as the window start): the caveat must say so.
    assert "2026-10-26T14:00:00Z" in capsys.readouterr().out


def test_log_unavailable_placeholders_do_not_cross_policy_thresholds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    events = [
        event("log-unavailable:Dashboard journeys shard 4/4", kind="unclassified", run_id=1),
        event("log-unavailable:Dashboard journeys shard 4/4", kind="unclassified", run_id=2),
        event("pytest:tests/a.py", rerun=True, run_id=3),
    ]

    def fake_collect(options: Any, log: Any = print, notes: list[str] | None = None) -> Any:
        return [success_run(1)], events

    monkeypatch.setattr(flake_report, "collect_report_data", fake_collect)
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    output = capsys.readouterr()
    # Two unreadable logs must not read as two rerun-only passes of a
    # signature; the unreadable count stays visible as a collection caveat.
    assert "needs flaky-test issue with owner" not in output.out
    assert "log-unavailable" not in output.out
    assert "2 failed-job log(s) unreadable" in output.out


def test_weekly_deadline_is_enforced_through_the_real_collector(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = FakeClock()

    def slow_gh_api_json(*endpoint_args: str) -> dict[str, Any]:
        endpoint = endpoint_args[0]
        if "/actions/workflows/" in endpoint:
            # One slow listing page eats the whole remaining budget.
            clock.now = flake_report.WEEKLY_COLLECTION_BUDGET_MINUTES * 60 + 1
            return {
                "workflow_runs": [
                    {
                        "id": 1,
                        "name": "Risk Service CI",
                        "event": "push",
                        "head_branch": "main",
                        "head_sha": "a" * 40,
                        "run_attempt": 1,
                        "conclusion": "success",
                        "created_at": "2026-11-08T10:00:00Z",
                    }
                ],
                "total_count": 100,
            }
        return {"total_count": 0, "jobs": []}

    monkeypatch.setattr(flake_report, "time", clock)
    monkeypatch.setattr(flake_report, "gh_api_json", slow_gh_api_json)
    parser = flake_report.build_parser()
    args = parser.parse_args(
        ["weekly", "--repo", "CedynGroup/aequorOS", "--cache-dir", str(tmp_path)]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    output = capsys.readouterr()
    # The real collector (not a stub) hits the budget during the run listing,
    # and the summary says the collection was cut short instead of presenting
    # the counts as complete.
    assert "Collection caveat" in output.out
    assert "time budget" in output.out
    assert "undercounted" in output.out


def test_the_weekly_window_starts_at_its_exact_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(options: Any, log: Any = print, notes: list[str] | None = None) -> Any:
        captured["options"] = options
        return [], []

    monkeypatch.setattr(flake_report, "collect_report_data", fake_collect)
    parser = flake_report.build_parser()
    args = parser.parse_args(["weekly", "--cache-dir", str(tmp_path)])
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    # The window boundary is a full timestamp, not a date: a run from before
    # 2026-10-26T12:00Z is outside the 14-day window even on that date, and
    # every section must measure the same window.
    assert captured["options"].window_start == "2026-10-26T12:00:00Z"


def test_pre_window_runs_are_dropped_before_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_listing(*args: Any, **kwargs: Any) -> tuple[list[Any], str | None]:
        runs = [
            flake_report.RunRecord(
                run_id=1,
                workflow="Risk Service CI",
                event="push",
                head_branch="main",
                head_sha="a" * 40,
                run_attempt=1,
                conclusion="success",
                created_at="2026-10-26T01:00:00Z",
            ),
            flake_report.RunRecord(
                run_id=2,
                workflow="Risk Service CI",
                event="push",
                head_branch="main",
                head_sha="b" * 40,
                run_attempt=1,
                conclusion="success",
                created_at="2026-10-26T14:00:00Z",
            ),
        ]
        return runs, None

    monkeypatch.setattr(flake_report, "list_workflow_runs", fake_listing)
    options = flake_report.ReportOptions(
        repo="CedynGroup/aequorOS",
        workflow_files=["risk-service.yml"],
        min_runs_per_workflow=10,
        since="2026-10-26",
        cache_dir=Path("/tmp/unused-cache"),
        window_start="2026-10-26T12:00:00Z",
    )
    runs = flake_report._list_all_runs(options, lambda message: None, [])
    # The run from before the exact window boundary is not collected, so it
    # cannot leak into the mixed-outcome classification or the ranking.
    assert [run.run_id for run in runs] == [2]


def test_a_failed_report_write_leaves_no_partial_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out_md = tmp_path / "weekly.md"
    out_json = tmp_path / "weekly.json"

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("disk full")

    # Fail the atomic publish: the temp file may be written, but the
    # destination must never receive a partial report.
    monkeypatch.setattr(flake_report.os, "replace", failing_replace)
    monkeypatch.setattr(
        flake_report,
        "collect_report_data",
        lambda options, log=print, notes=None: ([success_run(1)], []),
    )
    parser = flake_report.build_parser()
    args = parser.parse_args(
        [
            "weekly",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out-md",
            str(out_md),
            "--out-json",
            str(out_json),
        ]
    )
    assert flake_report.cmd_weekly(args, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) == 0
    # A failed write must not leave a partial file that a later check could
    # mistake for the complete report.
    assert not out_md.exists()
    assert not out_json.exists()
    assert "artifact write failed" in capsys.readouterr().err
