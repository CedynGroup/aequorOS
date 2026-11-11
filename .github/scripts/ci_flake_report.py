#!/usr/bin/env python3
"""Measure CI flakiness from GitHub Actions history.

The single measurement consumer described in the mission architecture (§2.1):
it reads per-workflow run history, every attempt of rerun runs and the logs of
failed jobs, classifies failures (test flakes vs infrastructure), and ranks
flake signatures by frequency and cost.

Modes
-----
``report``
    Full ranking over the requested window (JSON + Markdown). This is the mode
    used for the milestone baseline report.
``rerun-summary``
    Compare the attempts of one run and list every job (and test, where the
    logs identify it) that failed earlier and passed only on a rerun. Wired
    into the four test workflows as the ``Rerun summary`` job (runs only when
    ``github.run_attempt > 1``, appends to ``$GITHUB_STEP_SUMMARY``, and exits
    0 with a visible note when the API cannot be read).
``weekly``
    Ranking over a rolling window plus threshold evaluation (>=2 rerun-only
    passes in 14 days => "needs flaky-test issue with owner"; >=3 => "fix or
    quarantine within 7 days"). Wired by the scheduled
    ``.github/workflows/flake-report.yml`` workflow, which appends the report
    to ``$GITHUB_STEP_SUMMARY``; it creates no issues or comments.

Pure functions (parsing, signature extraction, attempt comparison,
classification, ranking, thresholds) are separated from all I/O so they can be
unit-tested offline with recorded fixtures (see
``backend/tests/scripts/test_ci_flake_report.py``).

Example::

    python3 .github/scripts/ci_flake_report.py report \
        --repo CedynGroup/aequorOS \
        --min-runs-per-workflow 100 \
        --out-md reports/flake-ranking.md --out-json reports/flake-ranking.json

Requires ``gh`` authenticated with at least ``repo`` read access. Logs are
cached under ``--cache-dir`` (default ``/tmp/ci-flake-logs``); large logs are
deleted right after signature extraction unless ``--keep-logs`` is given.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = "CedynGroup/aequorOS"
WORKFLOW_FILES = (
    "risk-service.yml",
    "dashboard-journeys.yml",
    "dashboard.yml",
    "web.yml",
)
PER_PAGE = 100
LOG_CACHE_DEFAULT = "/tmp/ci-flake-logs"
# The window the approved flake policy defines its thresholds on. The weekly
# report renders any other window as exploratory rather than policy output.
POLICY_WINDOW_DAYS = 14
# The weekly workflow's runner budget is one hour; collection stops well
# before it so the report (or its unavailable note) is always written.
WEEKLY_COLLECTION_BUDGET_MINUTES = 45
# Signatures emitted when a failed job's log could not be read. They name no
# identified failure, so they stay out of the policy thresholds (they remain
# in the ranking and the caveat counts); a threshold crossed only by them
# would demand action on evidence nobody has seen.
MISSING_LOG_SIGNATURE_PREFIXES = ("log-budget-exhausted:", "log-unavailable:")
# Logs bigger than this are deleted right after extraction (journey shards
# reach ~15 MB); small ones stay cached for re-runs.
DELETE_LOG_THRESHOLD_BYTES = 1_000_000
API_TIMEOUT_SECONDS = 120
API_RETRIES = 4
API_RETRY_WAIT_SECONDS = 5
TRANSIENT_API_ERROR = re.compile(r"Server Error \(HTTP 5\d\d\)|HTTP 429|rate limit|secondary rate")

# Upper bound on failed-job logs one rerun summary may download. A rerun is a
# handful of failed jobs in practice; the cap only exists so a pathological
# history (many attempts, many failures) cannot turn a summary step into a log
# download campaign. Past the cap the row says its signature is uninspected.
RERUN_SUMMARY_LOG_BUDGET = 40
# Overall wall-clock budget for the optional log enrichment, well under the
# 10-minute workflow job timeout even with one worst-case single read.
RERUN_SUMMARY_ENRICHMENT_SECONDS = 240

# Jobs that aggregate other jobs' results or detect changes carry no test
# signal of their own.
AGGREGATE_JOB_NAMES = {
    "Postgres schema, RLS and locks",
    "Integration tests",
    "Claude review verdict",
    "changes",
}
AGGREGATE_JOB_PREFIXES = ("Detect ",)
AGGREGATE_JOB_SUFFIXES = ("gate",)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TIMESTAMP = re.compile(r"^\S+Z\s+")
BOM = "\ufeff"

PYTEST_FAILED = re.compile(r"^FAILED (?P<path>\S+?\.py)::(?P<name>[^ ]+)")
PYTEST_COLLECTION_ERROR = re.compile(r"^ERROR (?P<path>tests/\S+?\.py)(?:::(?P<name>[^ ]+))?")
PLAYWRIGHT_FAILED = re.compile(
    r"✘\s+\d+\s+(?P<path>\S+?\.(?:e2e|integration)\.spec\.ts):(?P<line>\d+):\d+ › (?P<name>.+?)"
    r"(?:\s+\([^)]*\))?$"
)
TYPECHECK_BASELINE = re.compile(r"^(?P<module>\S+) (?P<rule>report\w+): \d+ \(baseline \d+\)")
CURL_22 = re.compile(r"^curl: \(22\) The requested URL returned error: (?P<code>\d+)")
CURL_EXIT_MARKER = re.compile(r"Process completed with exit code 22\b")


@dataclass
class JobRecord:
    job_id: int
    name: str
    conclusion: str
    minutes: float


@dataclass
class RunRecord:
    run_id: int
    workflow: str
    event: str
    head_branch: str
    head_sha: str
    run_attempt: int
    conclusion: str
    created_at: str


@dataclass
class Finding:
    kind: str  # "test" | "typecheck" | "infra"
    signature: str
    test_names: list[str] = field(default_factory=list)


@dataclass
class RerunPass:
    failed_job: JobRecord
    failed_attempt: int
    passed_attempt: int
    passed_minutes: float


@dataclass
class RerunSummaryRow:
    """One job of one run: failed earlier, passed on the attempt being reported."""

    job_name: str
    # The actual attempt numbers, not a compressed "first failure": a job that
    # failed on attempts 1 and 2 and passed on 3 reports [1, 2].
    failed_attempts: list[int]
    passed_attempts: list[int]
    rerun_minutes: float
    findings: list[Finding] = field(default_factory=list)


@dataclass
class MixedOutcome:
    job_name: str
    failed_run_id: int
    passed_run_id: int


@dataclass
class FlakeEvent:
    signature: str
    kind: str  # "test" | "typecheck" | "infra" | "unclassified"
    workflow: str
    run_id: int
    event: str  # push | pull_request | merge_group | ...
    branch: str
    sha: str
    job: str
    attempt: int
    minutes: float
    rerun_minutes: float
    rerun: bool
    same_sha_pass: bool
    created_at: str
    test_names: list[str] = field(default_factory=list)


@dataclass
class ThresholdBreach:
    signature: str
    rerun_passes_in_window: int
    action: str


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def clean_lines(text: str) -> list[str]:
    """Strip runner timestamps, ANSI colour codes and a BOM from log lines."""
    cleaned = []
    for raw_line in text.splitlines():
        line = raw_line.lstrip(BOM)
        line = ANSI_ESCAPE.sub("", line)
        line = TIMESTAMP.sub("", line)
        cleaned.append(line.strip())
    return cleaned


def _add_test_name(by_path: dict[str, list[str]], path: str, name: str | None) -> None:
    # The path registers even without a name: a pytest collection error names
    # no test, but the file that failed to import is still a finding.
    names = by_path.setdefault(path, [])
    if name and name not in names:
        names.append(name)


def extract_findings(raw_lines: list[str]) -> list[Finding]:
    """Extract failure findings from raw job-log lines.

    Only fatal occurrences are reported: a curl exit 22 counts as an
    infrastructure download failure only when the step actually aborted with
    it (the "Process completed with exit code 22" marker); retry lines in
    logs where the download recovered produce nothing.
    """
    lines = clean_lines("\n".join(raw_lines))

    pytest_by_path: dict[str, list[str]] = defaultdict(list)
    playwright_by_path: dict[str, list[str]] = defaultdict(list)
    typecheck_keys: list[tuple[str, str]] = []
    curl_22_code: str | None = None
    has_curl_exit_marker = False

    for line in lines:
        match = PYTEST_FAILED.match(line) or PYTEST_COLLECTION_ERROR.match(line)
        if match:
            _add_test_name(pytest_by_path, match.group("path"), match.group("name"))
            continue
        match = PLAYWRIGHT_FAILED.match(line)
        if match:
            _add_test_name(playwright_by_path, match.group("path"), match.group("name"))
            continue
        match = TYPECHECK_BASELINE.match(line)
        if match:
            key = (match.group("module"), match.group("rule"))
            if key not in typecheck_keys:
                typecheck_keys.append(key)
            continue
        match = CURL_22.match(line)
        if match:
            curl_22_code = match.group("code")
            continue
        if CURL_EXIT_MARKER.search(line):
            has_curl_exit_marker = True

    findings: list[Finding] = []
    for path, names in pytest_by_path.items():
        findings.append(Finding("test", f"pytest:{path}", names))
    for path, names in playwright_by_path.items():
        findings.append(Finding("test", f"playwright:{path}", names))
    for module, rule in typecheck_keys:
        findings.append(Finding("typecheck", f"typecheck:{module}:{rule}"))
    if curl_22_code is not None and has_curl_exit_marker:
        findings.append(Finding("infra", f"infra:download-curl-22-{curl_22_code}"))

    return findings


def is_aggregate_job(name: str) -> bool:
    """True for gate/aggregate/change-detection jobs that carry no test signal."""
    return (
        name in AGGREGATE_JOB_NAMES
        or name.startswith(AGGREGATE_JOB_PREFIXES)
        or name.endswith(AGGREGATE_JOB_SUFFIXES)
    )


def is_test_job_conclusion(conclusion: str) -> bool:
    """Conclusions that mean the job's tests actually ran and produced a verdict."""
    return conclusion in ("success", "failure")


def is_failed_job_conclusion(conclusion: str) -> bool:
    """Conclusions that mean the job's work failed, including a job-level timeout."""
    return conclusion in ("failure", "timed_out")


def parse_jobs(payload: dict[str, Any]) -> list[JobRecord]:
    jobs = []
    for raw in payload.get("jobs", []):
        started = _parse_timestamp(raw.get("started_at"))
        completed = _parse_timestamp(raw.get("completed_at"))
        minutes = 0.0
        if started is not None and completed is not None:
            minutes = round((completed - started).total_seconds() / 60, 1)
        jobs.append(
            JobRecord(
                job_id=int(raw["id"]),
                name=raw["name"],
                conclusion=raw.get("conclusion") or "unknown",
                minutes=minutes,
            )
        )
    return jobs


def parse_runs(payload: dict[str, Any], workflow: str) -> list[RunRecord]:
    runs = []
    for raw in payload.get("workflow_runs", []):
        runs.append(
            RunRecord(
                run_id=int(raw["id"]),
                workflow=raw.get("name") or workflow,
                event=raw.get("event") or "unknown",
                head_branch=raw.get("head_branch") or "unknown",
                head_sha=raw.get("head_sha") or "",
                run_attempt=int(raw.get("run_attempt") or 1),
                conclusion=raw.get("conclusion") or "unknown",
                created_at=raw.get("created_at") or "",
            )
        )
    return runs


def compare_attempts(
    attempts: dict[int, list[JobRecord]],
) -> list[RerunPass]:
    """Jobs that failed and then passed on a later attempt of the same run.

    One RerunPass per failed attempt: its recovery is the first passing
    attempt after that failure. A job that succeeded before it failed and
    then recovered again (rerun the whole workflow and it passes on the
    third attempt) still needed its rerun, so the recovery that matters is
    the one that follows the failure. Later failures before that pass are
    additional lost minutes but not additional re-executed ones: a pass
    charges its minutes once, to the first failed attempt recovering at it
    (a run that failed, failed again and then passed needed one rerun, not
    two).

    Two same-named jobs can share one attempt; the name is not a reliable
    cross-attempt identity there. Two rules keep the accounting honest when
    that happens: a recovery must be an attempt where *every* job of the name
    succeeded (with one success and one failure in the same attempt, no
    identity ties an earlier failure to that one success, so recovering there
    would attribute one job's success to another), and the failures recovering
    at one attempt share that attempt's successes positionally, so each
    re-execution charges its own minutes. Tuple comparison never reaches the
    JobRecord objects (they are not orderable).
    """
    passes: list[RerunPass] = []
    for name in {job.name for jobs in attempts.values() for job in jobs}:
        timeline = sorted(
            (
                (attempt, job)
                for attempt, jobs in attempts.items()
                for job in jobs
                if job.name == name
            ),
            # Explicit key: two same-named jobs can share one attempt (run
            # 42465955513 attempt 1 had two "Claude review verdict" jobs), and
            # JobRecord is not orderable, so tuple comparison must never reach
            # it.
            key=lambda pair: (pair[0], pair[1].name, pair[1].job_id),
        )
        jobs_by_attempt: dict[int, list[JobRecord]] = {}
        for attempt, job in timeline:
            jobs_by_attempt.setdefault(attempt, []).append(job)
        failed = [
            (attempt, job)
            for attempt, jobs in sorted(jobs_by_attempt.items())
            for job in jobs
            if is_failed_job_conclusion(job.conclusion)
        ]
        if not failed:
            continue
        # A recovery is a fully-passing attempt for this name; see the
        # docstring on why a partially passing attempt does not qualify.
        recoveries = [
            attempt
            for attempt, jobs in sorted(jobs_by_attempt.items())
            if all(job.conclusion == "success" for job in jobs)
        ]
        # First failed attempt recovering at each passing attempt: that one
        # carries the re-executed minutes for the pass.
        first_failure_before: dict[int, int] = {}
        failures_by_recovery: dict[int, list[tuple[int, JobRecord]]] = {}
        for fail_attempt, fail_job in failed:
            recovery = next((a for a in recoveries if a > fail_attempt), None)
            if recovery is None:
                continue
            first_failure_before.setdefault(recovery, fail_attempt)
            failures_by_recovery.setdefault(recovery, []).append((fail_attempt, fail_job))
        for recovery, group in failures_by_recovery.items():
            # By construction every job of the name succeeded on a recovery
            # attempt; order the duplicates deterministically by job id.
            successes = sorted(jobs_by_attempt[recovery], key=lambda job: job.job_id)
            first_failure = first_failure_before[recovery]
            for position, (fail_attempt, fail_job) in enumerate(group):
                if position >= len(successes):
                    if fail_attempt == first_failure:
                        # A duplicate from the same failed attempt has no
                        # success of its own to recover at (the recovery
                        # attempt held fewer same-named jobs than the failing
                        # one): leave it unrecovered instead of attributing
                        # another job's success to it or charging its minutes
                        # twice.
                        continue
                    # A later failure of the same rerun cycle: no additional
                    # re-execution, so no minutes.
                    minutes = 0.0
                else:
                    minutes = successes[position].minutes
                    if fail_attempt != first_failure:
                        # Later failures of one rerun cycle are additional
                        # lost minutes but not additional re-executed ones:
                        # the pass charges its minutes once, to the first
                        # failed attempt recovering at it.
                        minutes = 0.0
                passes.append(
                    RerunPass(
                        failed_job=fail_job,
                        failed_attempt=fail_attempt,
                        passed_attempt=recovery,
                        passed_minutes=minutes,
                    )
                )
    return passes


def rerun_summary_rows(
    attempts: dict[int, list[JobRecord]],
    current_attempt: int,
) -> list[RerunSummaryRow]:
    """Jobs that failed in an earlier attempt and passed on ``current_attempt``.

    Unlike the ranking (which compresses a history to its first failure and
    first pass) this reports the actual attempt numbers: every earlier attempt
    that failed the job, and every attempt up to and including the current one
    that passed it. Aggregate/gate jobs are excluded — they carry no test
    signal of their own (see ``compare_attempts`` for the counting rule the
    ranking uses). Rows are ordered by the minutes the current attempt spent
    re-executing the job, most expensive first.
    """
    rows: list[RerunSummaryRow] = []
    names = {
        job.name for jobs in attempts.values() for job in jobs if not is_aggregate_job(job.name)
    }
    for name in names:
        failed_attempts = sorted(
            {
                attempt
                for attempt, jobs in attempts.items()
                if attempt < current_attempt
                for job in jobs
                if job.name == name and is_failed_job_conclusion(job.conclusion)
            }
        )
        passed_attempts = sorted(
            {
                attempt
                for attempt, jobs in attempts.items()
                if attempt <= current_attempt
                for job in jobs
                if job.name == name and job.conclusion == "success"
            }
        )
        if not failed_attempts or current_attempt not in passed_attempts:
            continue
        rerun_minutes = max(
            (job.minutes for job in attempts.get(current_attempt, []) if job.name == name),
            default=0.0,
        )
        rows.append(
            RerunSummaryRow(
                job_name=name,
                failed_attempts=failed_attempts,
                passed_attempts=passed_attempts,
                rerun_minutes=rerun_minutes,
            )
        )
    rows.sort(key=lambda row: (-row.rerun_minutes, row.job_name))
    return rows


def render_rerun_summary(
    run_id: int,
    attempt: int,
    rows: list[RerunSummaryRow],
    context: dict[str, str] | None = None,
) -> str:
    """Markdown for ``$GITHUB_STEP_SUMMARY``: what a rerun turned green."""
    lines = [f"## Rerun summary — run {run_id} (attempt {attempt})", ""]
    if context:
        parts = [context.get("workflow") or "", context.get("event") or ""]
        if context.get("branch"):
            parts.append(f"branch `{context['branch']}`")
        lines.append(" · ".join(part for part in parts if part))
        lines.append("")
    if attempt <= 1:
        lines.append(
            "First attempt — nothing to compare yet. This summary lists the jobs "
            "(and tests, where the logs identify them) that failed earlier in this "
            "run and passed only on a rerun; it appears when the run is re-executed."
        )
        return "\n".join(lines) + "\n"
    if not rows:
        lines.append(
            f"No job that failed in an earlier attempt passed on attempt {attempt}: "
            "the rerun did not turn any earlier failure green."
        )
        return "\n".join(lines) + "\n"
    lines.append(f"{len(rows)} job(s) failed earlier in this run and passed on attempt {attempt}:")
    lines += [
        "",
        "| Job | Failed on attempts | Passed on attempts | Re-executed (min) |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.job_name} "
            f"| {', '.join(str(a) for a in row.failed_attempts)} "
            f"| {', '.join(str(a) for a in row.passed_attempts)} "
            f"| {row.rerun_minutes:g} |"
        )
    lines += ["", "What the earlier failures were (from the failed attempts' logs):"]
    for row in rows:
        lines.append(
            f"- **{row.job_name}** — failed on attempts "
            f"{', '.join(str(a) for a in row.failed_attempts)}"
        )
        if not row.findings:
            lines.append("  - no recognised failure signature in the failed attempts' logs")
        for finding in row.findings:
            if finding.test_names:
                lines.append(f"  - `{finding.signature}` — {', '.join(finding.test_names)}")
            else:
                lines.append(f"  - `{finding.signature}`")
    lines += ["", "A rerun-only pass is reported, never retried silently."]
    return "\n".join(lines) + "\n"


def render_rerun_unavailable(run_id: int, attempt: int, error: str) -> str:
    """The never-failing fallback: say so in the summary and move on."""
    return (
        "\n".join(
            [
                f"## Rerun summary — run {run_id} (attempt {attempt})",
                "",
                "Rerun summary unavailable: the GitHub Actions API could not be read, "
                "so nothing was concluded about this rerun.",
                f"Reason: {error}",
                "",
                "The run itself is unaffected — this summary is visibility only.",
            ]
        )
        + "\n"
    )


def _attach_rerun_findings(
    repo: str,
    rows: list[RerunSummaryRow],
    attempts: dict[int, list[JobRecord]],
    cache_dir: Path,
    log: Any,
) -> None:
    """Give each row the failure signatures its earlier attempts' logs contain.

    Log reads are budgeted (RERUN_SUMMARY_LOG_BUDGET) and individually
    non-fatal: an unreadable log degrades that job's row to "signature
    unavailable" instead of failing the summary. They are also bounded by an
    overall deadline well under the workflow job timeout, so a slow API can
    never starve the summary itself: when the deadline passes, the remaining
    failures are noted as uninspected and the summary prints with what was
    gathered.
    """
    budget = RERUN_SUMMARY_LOG_BUDGET
    deadline = time.monotonic() + RERUN_SUMMARY_ENRICHMENT_SECONDS
    for row in rows:
        findings: list[Finding] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        budget_spent = False
        deadline_hit = False
        for attempt in row.failed_attempts:
            for job in attempts.get(attempt, []):
                if job.name != row.job_name or not is_failed_job_conclusion(job.conclusion):
                    continue
                if budget <= 0:
                    budget_spent = True
                    break
                if time.monotonic() >= deadline:
                    deadline_hit = True
                    break
                budget -= 1
                try:
                    # Optional visibility: read each log once, without the
                    # measurement path's retry budget, so a failing read
                    # cannot consume the job timeout.
                    text = fetch_job_log(repo, job.job_id, cache_dir, keep_logs=False, retries=1)
                    job_findings = extract_findings(clean_lines(text))
                    delete_large_log(cache_dir, job.job_id, keep_logs=False)
                except (RuntimeError, OSError) as error:
                    log(f"  ! log unavailable for job {job.job_id}: {error}")
                    job_findings = [Finding("unclassified", f"log-unavailable:{job.name}")]
                for finding in job_findings:
                    key = (finding.signature, tuple(finding.test_names))
                    if key not in seen:
                        seen.add(key)
                        findings.append(finding)
        if budget_spent:
            findings.append(
                Finding(
                    "unclassified",
                    "log budget reached "
                    f"({RERUN_SUMMARY_LOG_BUDGET}); remaining failures not inspected",
                )
            )
        if deadline_hit:
            findings.append(
                Finding(
                    "unclassified",
                    f"log enrichment deadline reached "
                    f"({RERUN_SUMMARY_ENRICHMENT_SECONDS}s); remaining failures not inspected",
                )
            )
        row.findings = findings


def same_sha_mixed_outcomes(
    runs: list[RunRecord],
    jobs_by_run: dict[int, list[JobRecord]],
) -> list[MixedOutcome]:
    """Same workflow + head SHA where a job failed in one run and passed in another.

    Outcomes are compared per job, not per run: two runs of one SHA can both
    end failed while different shards fail in each, and those swapped shards
    are exactly the mixed evidence a flake leaves behind. Jobs that were
    skipped in a run (e.g. Dashboard CI and Web CI skip their test jobs on
    merge_group) are not counted as passes: a skipped job never ran, so it
    says nothing about the health of that commit.
    """
    by_key: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for run in runs:
        by_key[(run.workflow, run.head_sha)].append(run)

    outcomes: list[MixedOutcome] = []
    seen: set[tuple[str, int, int]] = set()
    for group in by_key.values():
        if len(group) < 2:
            continue
        for failed_run in group:
            failed_names = {
                job.name
                for job in jobs_by_run.get(failed_run.run_id, [])
                if is_failed_job_conclusion(job.conclusion) and not is_aggregate_job(job.name)
            }
            for passed_run in group:
                if passed_run.run_id == failed_run.run_id:
                    continue
                # Only an explicit success is passing evidence: a job that
                # failed in both runs of one SHA is deterministic breakage,
                # not a mixed outcome.
                passed_names = {
                    job.name
                    for job in jobs_by_run.get(passed_run.run_id, [])
                    if job.conclusion == "success" and not is_aggregate_job(job.name)
                }
                for name in sorted(failed_names & passed_names):
                    key = (name, failed_run.run_id, passed_run.run_id)
                    if key not in seen:
                        seen.add(key)
                        outcomes.append(
                            MixedOutcome(
                                job_name=name,
                                failed_run_id=failed_run.run_id,
                                passed_run_id=passed_run.run_id,
                            )
                        )
    return outcomes


def _main_or_queue(event: FlakeEvent) -> bool:
    return event.event == "merge_group" or (event.event == "push" and event.branch == "main")


def _classify_signature(
    kind: str,
    events: list[FlakeEvent],
    branches: list[str],
) -> str:
    """Rerun/same-SHA passes and cross-branch recurrence mean flake.

    A signature seen on a single branch that never passes there is branch
    breakage, kept out of the flake rate.
    """
    if kind == "infra":
        return "infrastructure"
    if any(event.rerun or event.same_sha_pass for event in events):
        return "flake"
    if len(branches) >= 2 or any(_main_or_queue(event) for event in events):
        return "flake"
    if len(branches) == 1:
        return "branch-breakage"
    return "real-failure"


def build_ranking(
    events: list[FlakeEvent],
    denominators: dict[tuple[str, str], int],
) -> list[dict[str, Any]]:
    """Aggregate flake events into ranking rows (one per signature).

    Rows carry occurrence counts, rerun counts, lost minutes (failed-attempt
    minutes plus re-executed minutes of successful reruns), distinct branches,
    per-event counts/rates, main/merge-queue hits and first/last seen.
    """
    grouped: dict[str, list[FlakeEvent]] = defaultdict(list)
    for event in events:
        grouped[event.signature].append(event)

    rates_denominators_by_workflow: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for (workflow, event_type), count in denominators.items():
        rates_denominators_by_workflow[workflow][event_type] += count

    rows: list[dict[str, Any]] = []
    for signature, signature_events in grouped.items():
        kinds = {event.kind for event in signature_events}
        kind = (
            "infra"
            if "infra" in kinds
            else "typecheck"
            if "typecheck" in kinds
            else "test"
            if "test" in kinds
            else "unclassified"
        )
        row_workflows = {event.workflow for event in signature_events}
        # Rates are computed over the workflows the signature actually
        # occurs in, not the whole repository.
        rates_denominators: dict[str, int] = defaultdict(int)
        for workflow in row_workflows:
            for event_type, count in rates_denominators_by_workflow[workflow].items():
                rates_denominators[event_type] += count
        event_counts: Counter[str] = Counter()
        failed_runs_by_event: dict[str, set[int]] = defaultdict(set)
        for event in signature_events:
            event_counts[event.event] += 1
            failed_runs_by_event[event.event].add(event.run_id)
        rerun_events = [event for event in signature_events if event.rerun]
        branches = sorted({event.branch for event in signature_events})
        minutes = sum(event.minutes for event in signature_events)
        rerun_minutes = sum(event.rerun_minutes for event in signature_events)
        test_names: Counter[str] = Counter()
        for event in signature_events:
            for name in event.test_names:
                test_names[name] += 1

        classification = _classify_signature(kind, signature_events, branches)

        rows.append(
            {
                "signature": signature,
                "kind": kind,
                "classification": classification,
                "occurrences": len(signature_events),
                # One rerun cycle per run/job: a job failing the signature on
                # several attempts before its pass needed one rerun.
                "rerun_count": len({(event.run_id, event.job) for event in rerun_events}),
                "same_sha_count": sum(1 for event in signature_events if event.same_sha_pass),
                "lost_minutes": round(minutes, 1),
                "rerun_minutes": round(rerun_minutes, 1),
                "total_minutes": round(minutes + rerun_minutes, 1),
                "workflows": sorted(row_workflows),
                "event_counts": dict(sorted(event_counts.items())),
                "event_rates": {
                    # Rate numerator: distinct runs that saw the failure
                    # (a run failing the signature in two attempts or two
                    # jobs counts once against its runs).
                    event_type: {
                        "failed": len(failed_runs_by_event.get(event_type, ())),
                        "runs": rates_denominators.get(event_type, 0),
                        "rate": round(
                            len(failed_runs_by_event.get(event_type, ()))
                            / rates_denominators[event_type],
                            4,
                        )
                        if rates_denominators.get(event_type)
                        else 0.0,
                    }
                    for event_type in sorted(rates_denominators)
                },
                "branches": branches,
                "branch_count": len(branches),
                "main_or_queue_hits": sum(1 for event in signature_events if _main_or_queue(event)),
                "first_seen": min(event.created_at for event in signature_events),
                "last_seen": max(event.created_at for event in signature_events),
                "test_names": [name for name, _ in test_names.most_common(8)],
                "run_ids": sorted({event.run_id for event in signature_events}),
            }
        )

    rows.sort(key=lambda row: (-row["total_minutes"], -row["occurrences"], row["signature"]))
    return rows


def evaluate_thresholds(
    events: list[FlakeEvent],
    now: datetime,
    window_days: int = 14,
) -> list[ThresholdBreach]:
    """Policy thresholds over rerun-only passes in the trailing window.

    >=2 rerun-only passes in 14 days => a flaky-test issue with an owner is
    needed; >=3 => fix or quarantine within 7 days. Pure; used by the weekly
    mode wired in M3.
    """
    window_start = now - timedelta(days=window_days)
    # One rerun cycle per run/job: a job failing the signature on several
    # attempts before its pass needed one rerun, not two.
    counted: set[tuple[str, int, str]] = set()
    per_signature: dict[str, int] = Counter()
    for event in events:
        if not event.rerun:
            continue
        if event.signature.startswith(MISSING_LOG_SIGNATURE_PREFIXES):
            # An unread log is not an identified failure: demanding a
            # flaky-test issue for it would act on evidence nobody has seen.
            continue
        key = (event.signature, event.run_id, event.job)
        if key in counted:
            continue
        created = _parse_timestamp(event.created_at)
        if created is None or created < window_start:
            continue
        counted.add(key)
        per_signature[event.signature] += 1

    breaches: list[ThresholdBreach] = []
    for signature, count in sorted(per_signature.items()):
        if count >= 3:
            breaches.append(ThresholdBreach(signature, count, "fix or quarantine within 7 days"))
        elif count == 2:
            breaches.append(ThresholdBreach(signature, count, "needs flaky-test issue with owner"))
    return breaches


def baseline_rates(
    runs: list[RunRecord],
    events: list[FlakeEvent],
    excluded_run_ids: frozenset[int] | set[int] = frozenset(),
) -> dict[str, Any]:
    """Per-workflow baseline flake rate over the measured window.

    A run "needs a rerun" when ``run_attempt > 1``; a run "fails flakily on
    first attempt" when its first attempt failed with a test/typecheck
    signature (infrastructure failures are reported separately).
    ``excluded_run_ids`` names rerun runs whose failures are all
    branch-breakage signatures (reported separately, kept out of the flake
    rate); reruns with no failure events still count.
    """
    runs_by_workflow: dict[str, list[RunRecord]] = defaultdict(list)
    for run in runs:
        runs_by_workflow[run.workflow].append(run)

    flaky_runs: dict[str, set[int]] = defaultdict(set)
    infra_runs: dict[str, set[int]] = defaultdict(set)
    for event in events:
        workflow = event.workflow
        if event.kind in ("test", "typecheck"):
            if event.attempt == 1:
                flaky_runs[workflow].add(event.run_id)
        elif event.kind == "infra":
            infra_runs[workflow].add(event.run_id)

    report: dict[str, Any] = {}
    totals = {"total_runs": 0, "rerun_runs": 0, "flaky_failed_runs": 0}
    for workflow in sorted(runs_by_workflow):
        workflow_runs = runs_by_workflow[workflow]
        total = len(workflow_runs)
        rerun_runs = sum(1 for run in workflow_runs if run.run_attempt > 1)
        flaky = len(flaky_runs.get(workflow, set()))
        affected = 0
        flaky_run_ids = flaky_runs.get(workflow, set())
        rerun_run_ids = {
            run.run_id for run in workflow_runs if run.run_attempt > 1
        } - excluded_run_ids
        affected = len(flaky_run_ids | rerun_run_ids)
        entry = {
            "total_runs": total,
            "rerun_runs": rerun_runs,
            "rerun_rate": round(rerun_runs / total, 4) if total else 0.0,
            "flaky_failed_runs": flaky,
            "flaky_first_attempt_rate": round(flaky / total, 4) if total else 0.0,
            "infra_failed_runs": len(infra_runs.get(workflow, set())),
            "baseline_flake_rate": round(affected / total, 4) if total else 0.0,
        }
        report[workflow] = entry
        totals["total_runs"] += total
        totals["rerun_runs"] += rerun_runs
        totals["flaky_failed_runs"] += flaky

    if totals["total_runs"]:
        flaky_ids: set[int] = set()
        for workflow in runs_by_workflow:
            flaky_ids |= flaky_runs.get(workflow, set())
        rerun_ids = {run.run_id for run in runs if run.run_attempt > 1} - set(excluded_run_ids)
        report["overall"] = {
            "total_runs": totals["total_runs"],
            "rerun_runs": totals["rerun_runs"],
            "rerun_rate": round(totals["rerun_runs"] / totals["total_runs"], 4),
            "flaky_failed_runs": totals["flaky_failed_runs"],
            "flaky_first_attempt_rate": round(
                totals["flaky_failed_runs"] / totals["total_runs"], 4
            ),
            "infra_failed_runs": sum(
                entry["infra_failed_runs"]
                for entry in report.values()
                if isinstance(entry, dict) and "infra_failed_runs" in entry
            ),
            "baseline_flake_rate": round(len(flaky_ids | rerun_ids) / totals["total_runs"], 4),
        }
    return report


def build_report(
    runs: list[RunRecord],
    events: list[FlakeEvent],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the full report payload (pure; rendering is separate).

    ``meta`` carries ``repo``, ``workflow_files``, ``since``, ``commands`` and
    ``generated_at``.
    """
    denominators: Counter[tuple[str, str]] = Counter()
    run_counts: dict[str, dict[str, int]] = defaultdict(dict)
    for run in runs:
        denominators[(run.workflow, run.event)] += 1
        per_workflow = run_counts[run.workflow]
        per_workflow["total"] = per_workflow.get("total", 0) + 1
        per_workflow[run.event] = per_workflow.get(run.event, 0) + 1

    ranking = build_ranking(events, dict(denominators))
    infra_rows = [row for row in ranking if row["classification"] == "infrastructure"]
    flake_rows = [
        row for row in ranking if row["classification"] not in ("infrastructure", "branch-breakage")
    ]
    breakage_rows = [row for row in ranking if row["classification"] == "branch-breakage"]

    rerun_runs: dict[int, dict[str, Any]] = {}
    for event in events:
        if not event.rerun:
            continue
        detail = rerun_runs.setdefault(
            event.run_id,
            {
                "run_id": event.run_id,
                "workflow": event.workflow,
                "branch": event.branch,
                "jobs": [],
                "test_names": [],
            },
        )
        job_detail = next((entry for entry in detail["jobs"] if entry["job"] == event.job), None)
        if job_detail is None:
            job_detail = {
                "job": event.job,
                "failed_attempt": event.attempt,
                "test_names": [],
            }
            detail["jobs"].append(job_detail)
        for name in event.test_names:
            if name not in job_detail["test_names"]:
                job_detail["test_names"].append(name)
        for name in event.test_names:
            if name not in detail["test_names"]:
                detail["test_names"].append(name)

    # Branch breakage is excluded from the baseline flake rate (the report
    # notes say so); it is deterministic failure on its own branch.
    breakage_signatures = {row["signature"] for row in breakage_rows}
    # A rerun that failed again on nothing but branch-breakage signatures is
    # not evidence of flakiness either; reruns with no failure events (the
    # earlier attempt died for an unextracted reason) still count.
    failures_by_run: dict[int, list[FlakeEvent]] = defaultdict(list)
    for event in events:
        failures_by_run[event.run_id].append(event)
    breakage_only_rerun_ids = frozenset(
        run_id
        for run_id, run_events in failures_by_run.items()
        if all(event.signature in breakage_signatures for event in run_events)
    )
    baseline_events = [event for event in events if event.signature not in breakage_signatures]
    # The window claims what the data actually covers, which can be shorter
    # than the requested window when a run cap stopped paging early.
    oldest_seen = min((run.created_at[:10] for run in runs), default=meta["since"])
    return {
        "meta": {
            "repo": meta["repo"],
            "generated_at": meta["generated_at"],
            "window": {
                "since": oldest_seen,
                "until": meta["generated_at"][:10],
                "requested_since": meta["since"],
            },
            "commands": meta["commands"],
            "workflow_files": meta["workflow_files"],
            "run_counts": {
                workflow: dict(sorted(counts.items()))
                for workflow, counts in sorted(run_counts.items())
            },
        },
        "baseline": baseline_rates(runs, baseline_events, breakage_only_rerun_ids),
        "ranking": flake_rows,
        "infrastructure": infra_rows,
        "branch_breakage": breakage_rows,
        "rerun_runs": [rerun_runs[run_id] for run_id in sorted(rerun_runs, reverse=True)],
    }


def _render_ranking_table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    out = ["", f"## {title}", ""]
    if not rows:
        out += ["None.", ""]
        return out
    out += [
        "| Signature | Class | Count | Reruns | Lost min | Re-exec min | "
        "Branches | merge_group | pull_request | push | Main/queue hits | "
        "First seen | Last seen |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        rates_by_type = row["event_rates"]

        def cell(
            event_type: str,
            rates_by_type: dict[str, Any] = rates_by_type,
        ) -> str:
            rates = rates_by_type.get(event_type)
            if not rates or not rates["runs"]:
                return "-"
            return f"{rates['failed']}/{rates['runs']} ({rates['rate']:.1%})"

        out.append(
            f"| `{row['signature']}` | {row['classification']} "
            f"| {row['occurrences']} | {row['rerun_count']} "
            f"| {row['lost_minutes']} | {row['rerun_minutes']} "
            f"| {row['branch_count']} | {cell('merge_group')} "
            f"| {cell('pull_request')} | {cell('push')} "
            f"| {row['main_or_queue_hits']} "
            f"| {row['first_seen'][:10]} | {row['last_seen'][:10]} |"
        )
    out.append("")
    return out


def render_markdown(report: dict[str, Any]) -> str:
    meta = report["meta"]
    workflow_links = ", ".join(f"`.github/workflows/{name}`" for name in meta["workflow_files"])
    window = meta["window"]
    window_line = f"- Window: {window['since']} .. {window['until']}"
    requested = window.get("requested_since")
    if requested and requested != window["since"]:
        window_line += f" (requested from {requested}; run cap stopped earlier)"
    lines: list[str] = [
        "# CI flake ranking",
        "",
        f"- Repository: `{meta['repo']}`",
        window_line,
        f"- Generated: {meta['generated_at']}",
        f"- Command: `{meta['commands']}`",
        f"- Workflows: {workflow_links}",
        "",
        "## Run counts",
        "",
    ]
    for workflow, counts in report["meta"]["run_counts"].items():
        parts = [f"{key} {value}" for key, value in counts.items() if key != "total"]
        lines.append(f"- {workflow}: {counts.get('total', 0)} runs ({', '.join(parts)})")

    lines += ["", "## Baseline flake rate", ""]
    lines.append(
        "| Workflow | Runs | Needed rerun | Flaky first-attempt failure | "
        "Infra failures | Baseline flake rate |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for workflow, entry in report["baseline"].items():
        lines.append(
            f"| {workflow} | {entry['total_runs']} | {entry['rerun_runs']} "
            f"({entry['rerun_rate']:.1%}) | {entry['flaky_failed_runs']} "
            f"({entry['flaky_first_attempt_rate']:.1%}) | "
            f"{entry['infra_failed_runs']} | {entry['baseline_flake_rate']:.1%} |"
        )

    lines += _render_ranking_table("Ranking by cost", report["ranking"])
    by_frequency = sorted(
        report["ranking"],
        key=lambda row: (-row["occurrences"], -row["total_minutes"], row["signature"]),
    )
    lines += _render_ranking_table("Ranking by frequency", by_frequency)
    lines += _render_ranking_table(
        "Branch breakage (signature on one branch that never passes there; "
        "excluded from the flake rate)",
        report["branch_breakage"],
    )

    lines += ["", "## Infrastructure failures", ""]
    if report["infrastructure"]:
        for row in report["infrastructure"]:
            runs_hit = len(row["run_ids"])
            lines.append(
                f"- `{row['signature']}`: {row['occurrences']} failed job(s) "
                f"across {runs_hit} run(s), {row['lost_minutes']} lost minutes "
                f"({row['first_seen'][:10]} .. {row['last_seen'][:10]})."
            )
    else:
        lines.append("None.")

    lines += ["", "## Runs that passed only on rerun", ""]
    if report["rerun_runs"]:
        for rerun in report["rerun_runs"]:
            lines.append(
                f"- Run {rerun['run_id']} ({rerun['workflow']}, branch `{rerun['branch']}`):"
            )
            for job_detail in rerun["jobs"]:
                tests = ", ".join(f"`{name}`" for name in job_detail["test_names"])
                suffix = f" — failing test(s): {tests}" if tests else ""
                lines.append(
                    f"  - `{job_detail['job']}` failed on attempt "
                    f"{job_detail['failed_attempt']} and passed on rerun{suffix}"
                )
    else:
        lines.append("None.")

    lines += [
        "",
        "## Notes",
        "",
        '- A "flake" here is a signature that passed on a rerun or on the '
        "same SHA elsewhere, or that recurs across branches/main/queue; "
        "deterministic branch breakage is listed separately.",
        '- Jobs that only aggregate other jobs (gates, "Postgres schema, RLS '
        'and locks", change detection) carry no signal and are excluded.',
        "- A skipped job on merge_group is not a pass; merge-queue-skipped "
        "pairs cannot produce mixed-outcome flakes.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# weekly mode
# ---------------------------------------------------------------------------


def render_threshold_section(breaches: list[ThresholdBreach], window_days: int) -> list[str]:
    """The policy threshold crossings for the rolling window.

    Reporting only: this never creates issues or comments; a person files the
    flaky-test issue and picks its owner. The approved policy's thresholds are
    defined on the 14-day window; a report over any other window labels its
    crossings exploratory rather than policy determinations.
    """
    policy_window = window_days == POLICY_WINDOW_DAYS
    heading = (
        "## Threshold crossings (approved flake policy)"
        if policy_window
        else (
            f"## Threshold crossings (exploratory {window_days}-day window, "
            f"not the approved {POLICY_WINDOW_DAYS}-day policy)"
        )
    )
    lines = [
        heading,
        "",
        f">=2 rerun-only passes in {window_days} days need a flaky-test issue "
        "with an owner; >=3 need a fix or quarantine within 7 days. A "
        "rerun-only pass is a job that failed on an attempt and passed only "
        "because the run was retried.",
        "",
    ]
    if not policy_window:
        lines.append(
            "This window differs from the approved policy's window, so these "
            "crossings are exploratory reading, not policy determinations."
        )
        lines.append("")
    lines += [
        "This report never creates issues or comments; a person files the "
        "flaky-test issue and picks its owner. The window bounds runs by "
        "their creation date, so a rerun of a run created before the window "
        "is not counted.",
        "",
    ]
    if not breaches:
        lines.append("No signature reached the policy thresholds in this window.")
        return lines
    # A non-policy window's crossings are readings, not obligations: the
    # column says what the 14-day policy would require, not what does.
    action_column = (
        "Required action"
        if policy_window
        else "Action the 14-day policy would require (exploratory)"
    )
    lines += [
        f"| Signature | Rerun-only passes in window | {action_column} |",
        "| --- | --- | --- |",
    ]
    for breach in breaches:
        lines.append(
            f"| `{breach.signature}` | {breach.rerun_passes_in_window} | {breach.action} |"
        )
    return lines


def render_weekly_report(
    report: dict[str, Any],
    breaches: list[ThresholdBreach],
    window_days: int,
    truncated: list[tuple[str, str]] | None = None,
    collection_cutoff: str | None = None,
) -> str:
    """Weekly summary: caveats first, then the policy thresholds, then the ranking."""
    meta = report["meta"]
    window = meta["window"]
    caveats: list[str] = []
    if collection_cutoff is not None:
        caveats.append(
            f"- Collection caveat: {collection_cutoff}. The counts below may be "
            "undercounted; the next weekly run starts from an empty budget."
        )
    for workflow, oldest in truncated or []:
        caveats.append(
            f"- Threshold caveat: `{workflow}` hit the run cap before covering "
            f"the full window (its oldest collected run is {oldest}, which does "
            f"not precede the requested window start "
            f"{window.get('requested_since') or window['since']}), so its "
            "rerun-only passes may be undercounted."
        )
    incomplete = collection_cutoff is not None or bool(truncated)
    threshold_lines = render_threshold_section(breaches, window_days)
    if incomplete and not breaches:
        # An incomplete collection must not read as a clean bill of health:
        # the conclusive statement is replaced, not doubled with a footnote.
        threshold_lines = [
            (
                "No signature reached the policy thresholds in the collected "
                "data, but collection was incomplete (see the caveats above), "
                "so this result is inconclusive: runs or logs still missing "
                "could contain crossings."
                if line.startswith("No signature reached the policy thresholds")
                else line
            )
            for line in threshold_lines
        ]
    header = [
        "# Weekly CI flake report",
        "",
        f"- Repository: `{meta['repo']}`",
        f"- Generated: {meta['generated_at']}",
        f"- Command: `{meta['commands']}`",
        f"- Data window: {window['since']} .. {window['until']}",
        "",
        *(caveats + [""] if caveats else []),
        *threshold_lines,
        "",
    ]
    return "\n".join(header) + "\n---\n" + render_markdown(report)


def render_weekly_unavailable(window_days: int, error: str) -> str:
    """The never-failing fallback: say so in the summary and move on."""
    return (
        "\n".join(
            [
                "# Weekly CI flake report",
                "",
                "Weekly flake report unavailable: the ranking for the last "
                f"{window_days} days could not be collected this week; "
                "nothing is silently retried and no thresholds are claimed.",
                "",
                f"Reason: `{error}`",
                "",
                "Re-run the `Weekly flake report` workflow (workflow_dispatch) "
                "once the Actions API is readable again.",
            ]
        )
        + "\n"
    )


def cmd_weekly(args: argparse.Namespace, now: datetime | None = None) -> int:
    """Ranking over the rolling window plus the policy threshold crossings.

    Written for the scheduled workflow: it never raises, and every data
    failure becomes a visible note with exit code 0 so a measurement outage
    can never turn the report red. Output goes to stdout (the workflow step
    appends it to ``$GITHUB_STEP_SUMMARY``); ``--out-md``/``--out-json``
    record the same content for the mission's evidence trail.
    """
    current_time = now or datetime.now(UTC)
    window_days = args.window_days
    try:
        cache_dir = _private_cache_dir(args.cache_dir)
    except OSError as error:
        note = render_weekly_unavailable(window_days, str(error))
        print(note)
        print(f"Weekly flake report unavailable: {error}", file=sys.stderr)
        _write_unavailable_artifacts(args, note, window_days, error)
        return 0
    if cache_dir is None:
        return 2
    since = (current_time - timedelta(days=window_days)).strftime("%Y-%m-%d")
    workflow_files = args.workflows.split(",") if args.workflows else list(WORKFLOW_FILES)
    # Displayed commands must be copy-pasteable: the weekly subcommand derives
    # --since from --window-days and accepts no --since option itself.
    commands = (
        f"python3 .github/scripts/ci_flake_report.py weekly --repo {args.repo} "
        f"--window-days {window_days} --min-runs-per-workflow {args.min_runs_per_workflow}"
    )
    options = ReportOptions(
        repo=args.repo,
        workflow_files=workflow_files,
        min_runs_per_workflow=args.min_runs_per_workflow,
        since=since,
        cache_dir=cache_dir,
        keep_logs=args.keep_logs,
        deadline=time.monotonic() + WEEKLY_COLLECTION_BUDGET_MINUTES * 60,
        window_start=(current_time - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    try:
        # Diagnostics go to stderr: stdout is the Markdown step summary.
        collection_notes: list[str] = []
        runs, events = collect_report_data(
            options, lambda message: print(message, file=sys.stderr), collection_notes
        )
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        note = render_weekly_unavailable(window_days, str(error))
        print(note)
        print(f"Weekly flake report unavailable: {error}", file=sys.stderr)
        # The requested artifacts must not keep a stale earlier report: record
        # the unavailable state in them too, clearly marked as such.
        _write_unavailable_artifacts(args, note, window_days, error)
        return 0
    # Missing-log placeholders are accounting, not findings: a job whose logs
    # were never read (budget exhausted or log unavailable) must not reach the
    # policy thresholds or the ranking as if its failure signature were known.
    # The unfetched and unreadable counts are reported as caveats instead.
    budget_hits = sum(1 for event in events if event.signature.startswith("log-budget-exhausted:"))
    unavailable_hits = sum(1 for event in events if event.signature.startswith("log-unavailable:"))
    unclassified_hits = sum(1 for event in events if event.signature.startswith("unclassified:"))
    measured_events = [
        event
        for event in events
        if not event.signature.startswith("log-budget-exhausted:")
        and not event.signature.startswith("log-unavailable:")
    ]
    cutoff_parts = list(collection_notes)
    if budget_hits:
        cutoff_parts.append(f"{budget_hits} failed-job log(s) unfetched")
    if unavailable_hits:
        cutoff_parts.append(f"{unavailable_hits} failed-job log(s) unreadable")
    # An unclassified log was read but its failure is still unidentified: it
    # is incomplete evidence too, so the caveat names it.
    if unclassified_hits:
        cutoff_parts.append(
            f"{unclassified_hits} failed-job log(s) yielded no recognized failure signature"
        )
    collection_cutoff = "; ".join(cutoff_parts) if cutoff_parts else None

    report = build_report(
        runs=runs,
        events=measured_events,
        meta={
            "repo": args.repo,
            "workflow_files": workflow_files,
            "since": since,
            "commands": commands,
            "generated_at": current_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    # Per-workflow truncation check: the ranking's window line uses the global
    # oldest run, which can hide one workflow having been stopped by the run
    # cap. RunRecord.workflow carries the API workflow name (e.g. "Web CI"),
    # which is what the caveat shows. A workflow is flagged only when the cap
    # was actually reached: a low-traffic workflow with no runs early in the
    # window is not truncated. The comparison uses the full timestamps, so a
    # cap hit hours inside the window's first day is still caught.
    window_start = current_time - timedelta(days=window_days)
    window_start_iso = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    runs_per_workflow = Counter(run.workflow for run in runs)
    oldest_by_workflow = {
        workflow: min(run.created_at for run in runs if run.workflow == workflow)
        for workflow in runs_per_workflow
    }
    truncated = sorted(
        (workflow, oldest)
        for workflow, oldest in oldest_by_workflow.items()
        # An unlimited listing (cap 0) is never "capped": every workflow
        # satisfies count >= 0.
        if args.min_runs_per_workflow > 0
        and runs_per_workflow[workflow] >= args.min_runs_per_workflow
        and oldest > window_start_iso
    )
    breaches = evaluate_thresholds(measured_events, current_time, window_days)
    markdown = render_weekly_report(report, breaches, window_days, truncated, collection_cutoff)
    # stdout always carries the Markdown: the workflow step redirects it into
    # the step summary whether or not artifact paths were also given.
    print(markdown)
    if args.out_md:
        _write_weekly_artifact(args.out_md, markdown, "markdown artifact")
    if args.out_json:
        payload = {
            "thresholds": [
                {
                    "signature": breach.signature,
                    "rerun_passes_in_window": breach.rerun_passes_in_window,
                    "action": breach.action,
                }
                for breach in breaches
            ],
            # Collection status: a JSON reader must be able to tell an empty
            # threshold result from a complete measurement.
            "collection": {
                "caveat": collection_cutoff,
                "truncated_workflows": [workflow for workflow, _ in truncated],
            },
            "report": report,
        }
        _write_weekly_artifact(args.out_json, json.dumps(payload, indent=1), "JSON artifact")
    return 0


def _write_unavailable_artifacts(
    args: argparse.Namespace, note: str, window_days: int, error: Exception
) -> None:
    """Record the unavailable state in the requested artifacts.

    The artifacts must not keep a stale earlier report after a measurement
    failure, and the state must be clearly marked, never shaped like a report.
    """
    if args.out_md:
        _write_weekly_artifact(args.out_md, note, "markdown artifact")
    if args.out_json:
        payload = {"status": "unavailable", "window_days": window_days, "error": str(error)}
        _write_weekly_artifact(args.out_json, json.dumps(payload, indent=1), "JSON artifact")


def _write_weekly_artifact(path: str, text: str, label: str) -> None:
    """Write one requested artifact; a failure is a visible note, never a red run.

    Each artifact is attempted independently: an unwritable ``--out-md`` must
    not stop ``--out-json`` (the summary itself is already on stdout).
    """
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Atomic (tmp file + rename): an interrupt must not leave a truncated
        # report file behind.
        _atomic_write_text(Path(path), text)
        print(f"Wrote {path}", file=sys.stderr)
    except OSError as error:
        print(f"Weekly flake report {label} write failed: {error}", file=sys.stderr)


# ---------------------------------------------------------------------------
# I/O helpers (gh CLI)
# ---------------------------------------------------------------------------


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def gh_api(
    *endpoint_args: str, allow_escape_sequences: bool = False, retries: int = API_RETRIES
) -> str:
    """Read-only gh api call; transient server errors are retried a few times.

    (This is API-call resilience for the measurement tool, not a job retry:
    nothing about CI jobs or tests is ever re-run. Optional reads pass a
    smaller ``retries`` count so a failing enrichment cannot eat a job
    timeout.)
    """
    cmd = ["gh", "api"]
    if allow_escape_sequences:
        cmd.append("--allow-escape-sequences")
    cmd.extend(endpoint_args)
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=API_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            last_error = f"gh api timed out after {API_TIMEOUT_SECONDS}s: {error.cmd[-1]}"
            if attempt == retries:
                raise RuntimeError(last_error) from error
            time.sleep(API_RETRY_WAIT_SECONDS * attempt)
            continue
        if result.returncode == 0:
            return result.stdout
        last_error = f"gh api failed ({result.returncode}): {result.stderr[:400]}"
        if not TRANSIENT_API_ERROR.search(result.stderr) or attempt == retries:
            raise RuntimeError(last_error)
        time.sleep(API_RETRY_WAIT_SECONDS * attempt)
    raise RuntimeError(last_error)


def gh_api_json(*endpoint_args: str) -> dict[str, Any]:
    return json.loads(gh_api(*endpoint_args))


def list_workflow_runs(  # noqa: PLR0913 - the deadline bounds every page of one listing
    repo: str,
    workflow_file: str,
    min_runs: int,
    since: str,
    log: Any,
    deadline: float | None = None,
) -> tuple[list[RunRecord], str | None]:
    """Page through one workflow's runs (newest first) until enough are collected.

    Returns the collected runs and why the listing stopped early: "deadline"
    when the time budget passed between pages, "run-cap" when the cap was
    reached before the window was covered, and None when the listing covered
    the window or ran out of pages. Only the deadline stop means the counts
    may be undercounted because collection was cut short.
    """
    records: list[RunRecord] = []
    page = 1
    reason: str | None = None
    while True:
        if deadline is not None and time.monotonic() > deadline:
            log("Time budget reached during run listing; the report carries what was collected")
            reason = "deadline"
            break
        payload = gh_api_json(
            f"repos/{repo}/actions/workflows/{workflow_file}/runs?per_page={PER_PAGE}&page={page}"
        )
        page_records = parse_runs(payload, workflow_file)
        if not page_records:
            break
        records.extend(page_records)
        oldest = page_records[-1].created_at
        log(f"    {workflow_file} page {page}: {len(page_records)} runs (oldest {oldest[:10]})")
        if oldest[:10] < since:
            # The requested window is fully covered; this is completion, not
            # a cutoff.
            break
        if min_runs and len(records) >= min_runs:
            reason = "run-cap"
            break
        page += 1
    records = [run for run in records if run.created_at[:10] >= since]
    return (records[:min_runs] if min_runs else records), reason


def _write_cache(path: Path, text: str) -> None:
    """Write a cache file atomically (an interrupt must not leave a truncated file)."""
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write a report file atomically: a failed or interrupted write leaves no
    partial file that a later check could mistake for the complete report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _jobs_are_complete(jobs: list[dict[str, Any]]) -> bool:
    """A job listing is cacheable only once every job completed.

    An empty listing means the attempt has not reported jobs yet (e.g. a
    queued rerun): caching it would hide the failure or recovery evidence
    the attempt produces later, so it must be fetched again.
    """
    return bool(jobs) and all(raw.get("status") == "completed" for raw in jobs)


def fetch_attempt_jobs(
    repo: str,
    run_id: int,
    attempt: int,
    cache_dir: Path | None = None,
    deadline: float | None = None,
) -> list[JobRecord]:
    """Jobs of one run attempt; cached on disk so interrupted runs resume."""
    cache_path = cache_dir / "jobs" / f"{run_id}-{attempt}.json" if cache_dir else None
    if cache_path is not None and cache_path.exists():
        return parse_jobs(json.loads(cache_path.read_text(encoding="utf-8")))
    if deadline is not None and time.monotonic() > deadline:
        raise RuntimeError(
            f"time budget reached before job listing of run {run_id} attempt {attempt}"
        )
    payload = gh_api_json(
        f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page={PER_PAGE}"
    )
    # Never cache an attempt that is still running: a partial or empty job
    # list would be reused forever and hide the rerun pass it has not
    # produced yet.
    if cache_path is not None and _jobs_are_complete(payload.get("jobs", [])):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _write_cache(cache_path, json.dumps(payload))
    return parse_jobs(payload)


def fetch_job_log(
    repo: str, job_id: int, cache_dir: Path, keep_logs: bool, retries: int = API_RETRIES
) -> str:
    cache_path = cache_dir / f"{job_id}.log"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace")
    text = gh_api(
        f"repos/{repo}/actions/jobs/{job_id}/logs",
        allow_escape_sequences=True,
        retries=retries,
    )
    _write_cache(cache_path, text)
    return text


def delete_large_log(cache_dir: Path, job_id: int, keep_logs: bool) -> None:
    if keep_logs:
        return
    cache_path = cache_dir / f"{job_id}.log"
    try:
        if cache_path.exists() and cache_path.stat().st_size > DELETE_LOG_THRESHOLD_BYTES:
            cache_path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# report mode
# ---------------------------------------------------------------------------


@dataclass
class ReportOptions:
    repo: str
    workflow_files: list[str]
    min_runs_per_workflow: int
    since: str
    cache_dir: Path
    keep_logs: bool = False
    # A time.monotonic() reading: collection stops log fetches past it so a
    # slow or retried API never eats the whole runner budget and dies before
    # the report (or its unavailable note) is written.
    deadline: float | None = None
    # The full rolling-window boundary as an ISO timestamp: runs collected
    # before it are dropped before any classification, so a report run after
    # midnight cannot include the pre-window hours of the boundary date and
    # every section measures the same window.
    window_start: str | None = None


def _list_all_runs(options: ReportOptions, log: Any, notes: list[str]) -> list[RunRecord]:
    all_runs: list[RunRecord] = []
    for workflow_file in options.workflow_files:
        if options.deadline is not None and time.monotonic() > options.deadline:
            notes.append(f"run listing for {workflow_file} skipped at the time budget")
            continue
        log(
            f"Listing runs for {workflow_file} "
            f"(target {options.min_runs_per_workflow}, since {options.since})"
        )
        records, reason = list_workflow_runs(
            options.repo,
            workflow_file,
            options.min_runs_per_workflow,
            options.since,
            log,
            deadline=options.deadline,
        )
        if reason == "deadline":
            notes.append(
                f"run listing for {workflow_file} stopped at the time budget; "
                "its counts may be undercounted"
            )
        if options.window_start is not None:
            records = [run for run in records if run.created_at >= options.window_start]
        all_runs.extend(records)
    log(f"Collected {len(all_runs)} runs total")
    return all_runs


def _attempts_needed(all_runs: list[RunRecord]) -> dict[int, set[int]]:
    """Run ids -> attempts to list: failing runs, every attempt of rerun runs,
    and successful runs sharing a SHA with a failing run (mixed outcomes)."""
    failing_shas = {
        run.head_sha
        for run in all_runs
        if is_failed_job_conclusion(run.conclusion) or run.conclusion == "cancelled"
    }
    needed: dict[int, set[int]] = defaultdict(set)
    for run in all_runs:
        if run.conclusion in ("failure", "cancelled", "timed_out"):
            needed[run.run_id].add(1)
        if run.run_attempt > 1:
            needed[run.run_id] |= set(range(1, run.run_attempt + 1))
        if run.conclusion == "success" and run.head_sha in failing_shas:
            needed[run.run_id].add(1)
    return needed


def _fetch_attempt_jobs(
    repo: str,
    attempts_needed: dict[int, set[int]],
    options: ReportOptions,
    log: Any,
    notes: list[str],
) -> tuple[dict[int, dict[int, list[JobRecord]]], dict[int, list[JobRecord]]]:
    jobs_by_run_attempt: dict[int, dict[int, list[JobRecord]]] = {}
    jobs_by_run: dict[int, list[JobRecord]] = {}
    total = len(attempts_needed)
    fetched = 0
    for index, (run_id, attempts) in enumerate(sorted(attempts_needed.items()), 1):
        if options.deadline is not None and time.monotonic() > options.deadline:
            remaining = len(attempts_needed) - fetched
            notes.append(f"job listings for {remaining} run(s) unfetched at the time budget")
            break
        for attempt in sorted(attempts):
            if options.deadline is not None and time.monotonic() > options.deadline:
                # A partially listed run is not complete rerun evidence: the
                # unfetched later attempts cannot be read as passes, and the
                # cutoff is reported so the counts stay honest.
                notes.append(
                    "job listings stopped mid-run at the time budget; the "
                    "run's later attempts were not read"
                )
                break
            jobs = fetch_attempt_jobs(repo, run_id, attempt, options.cache_dir)
            jobs_by_run_attempt.setdefault(run_id, {})[attempt] = jobs
            jobs_by_run.setdefault(run_id, []).extend(jobs)
        else:
            fetched += 1
            if index % 25 == 0 or index == total:
                log(f"  job listings: {index}/{total} runs")
            continue
        break
    return jobs_by_run_attempt, jobs_by_run


def _fetch_findings(
    repo: str,
    failed_jobs: list[tuple[RunRecord, int, JobRecord]],
    options: ReportOptions,
    log: Any,
) -> dict[int, list[Finding]]:
    """Fetch logs of failed jobs, extract findings, free large logs."""
    findings_by_job: dict[int, list[Finding]] = {}
    for index, (_run, _attempt, job) in enumerate(failed_jobs, 1):
        if options.deadline is not None and time.monotonic() > options.deadline:
            remaining = len(failed_jobs) - index + 1
            log(
                f"Time budget reached with {remaining} failed-job log(s) unfetched; "
                "the report carries what was collected"
            )
            for _run, _attempt, remaining_job in failed_jobs[index - 1 :]:
                findings_by_job[remaining_job.job_id] = [
                    Finding("unclassified", f"log-budget-exhausted:{remaining_job.name}")
                ]
            break
        try:
            text = fetch_job_log(options.repo, job.job_id, options.cache_dir, options.keep_logs)
            findings_by_job[job.job_id] = extract_findings(clean_lines(text))
            delete_large_log(options.cache_dir, job.job_id, options.keep_logs)
        except (RuntimeError, OSError) as error:
            log(f"  ! log unavailable for job {job.job_id}: {error}")
            findings_by_job[job.job_id] = [Finding("unclassified", f"log-unavailable:{job.name}")]
        if index % 50 == 0 or index == len(failed_jobs):
            log(f"  logs: {index}/{len(failed_jobs)}")
    return findings_by_job


def _failed_leaf_jobs(
    runs_by_id: dict[int, RunRecord],
    jobs_by_run_attempt: dict[int, dict[int, list[JobRecord]]],
) -> list[tuple[RunRecord, int, JobRecord]]:
    failed_jobs: list[tuple[RunRecord, int, JobRecord]] = []
    for run_id, attempts in sorted(jobs_by_run_attempt.items()):
        run = runs_by_id[run_id]
        for attempt, jobs in attempts.items():
            for job in jobs:
                if is_failed_job_conclusion(job.conclusion) and not is_aggregate_job(job.name):
                    failed_jobs.append((run, attempt, job))
    return failed_jobs


def _build_events(
    failed_jobs: list[tuple[RunRecord, int, JobRecord]],
    rerun_passes: dict[tuple[int, int, int], RerunPass],
    mixed_flags: set[tuple[int, str]],
    findings_by_job: dict[int, list[Finding]],
) -> list[FlakeEvent]:
    events: list[FlakeEvent] = []
    for run, attempt, job in failed_jobs:
        rerun = rerun_passes.get((run.run_id, attempt, job.job_id))
        findings = findings_by_job.get(job.job_id, [])
        if not findings:
            findings = [Finding("unclassified", f"unclassified:{job.name}")]
        for finding in findings:
            events.append(
                FlakeEvent(
                    signature=finding.signature,
                    kind=finding.kind,
                    workflow=run.workflow,
                    run_id=run.run_id,
                    event=run.event,
                    branch=run.head_branch,
                    sha=run.head_sha,
                    job=job.name,
                    attempt=attempt,
                    minutes=job.minutes,
                    rerun_minutes=rerun.passed_minutes if rerun else 0.0,
                    rerun=rerun is not None,
                    same_sha_pass=(run.run_id, job.name) in mixed_flags,
                    created_at=run.created_at,
                    test_names=finding.test_names,
                )
            )
    return events


def collect_report_data(
    options: ReportOptions, log: Any = print, notes: list[str] | None = None
) -> tuple[list[RunRecord], list[FlakeEvent]]:
    """Fetch everything the report needs from the GitHub Actions API.

    ``notes`` collects the collection-stage cutoffs (run listing or job
    listing stopped at the time budget) so the caller can state them in the
    report instead of letting the counts shrink silently.
    """
    stage_notes: list[str] = [] if notes is None else notes
    all_runs = _list_all_runs(options, log, stage_notes)
    runs_by_id = {run.run_id: run for run in all_runs}

    attempts_needed = _attempts_needed(all_runs)
    jobs_by_run_attempt, jobs_by_run = _fetch_attempt_jobs(
        options.repo, attempts_needed, options, log, stage_notes
    )

    rerun_passes: dict[tuple[int, int, int], RerunPass] = {}
    for run_id, attempts in jobs_by_run_attempt.items():
        for rerun in compare_attempts(attempts):
            # Keyed by job id, not the job name: two same-named jobs can fail
            # in one attempt, and a name key would keep only the last one's
            # recovery (and charge its minutes to both).
            rerun_passes[(run_id, rerun.failed_attempt, rerun.failed_job.job_id)] = rerun
    mixed = same_sha_mixed_outcomes(all_runs, jobs_by_run)
    mixed_flags = {(outcome.failed_run_id, outcome.job_name) for outcome in mixed}

    failed_jobs = _failed_leaf_jobs(runs_by_id, jobs_by_run_attempt)
    log(f"Fetching logs for {len(failed_jobs)} failed job(s)")
    findings_by_job = _fetch_findings(options.repo, failed_jobs, options, log)

    events = _build_events(failed_jobs, rerun_passes, mixed_flags, findings_by_job)
    log(f"Built {len(events)} event(s) from {len(failed_jobs)} failed job(s)")
    return all_runs, events


def _private_cache_dir(raw: str) -> Path | None:
    """Create/validate the log cache dir; private, or refused (None)."""
    cache_dir = Path(raw)
    # Private: the cache is read back trusting earlier runs of this script.
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_stat = cache_dir.stat()
    if cache_dir.is_symlink() or cache_stat.st_uid != os.getuid() or cache_stat.st_mode & 0o077:
        print(
            f"Refusing cache dir {cache_dir}: not private to the current user",
            file=sys.stderr,
        )
        return None
    return cache_dir


def cmd_report(args: argparse.Namespace) -> int:
    cache_dir = _private_cache_dir(args.cache_dir)
    if cache_dir is None:
        return 2
    workflow_files = args.workflows.split(",") if args.workflows else list(WORKFLOW_FILES)
    since = args.since or (datetime.now(UTC) - timedelta(days=90)).strftime("%Y-%m-%d")
    commands = (
        f"python3 .github/scripts/ci_flake_report.py report --repo {args.repo} "
        f"--min-runs-per-workflow {args.min_runs_per_workflow} --since {since}"
    )
    options = ReportOptions(
        repo=args.repo,
        workflow_files=workflow_files,
        min_runs_per_workflow=args.min_runs_per_workflow,
        since=since,
        cache_dir=cache_dir,
        keep_logs=args.keep_logs,
    )

    runs, events = collect_report_data(options)
    report = build_report(
        runs=runs,
        events=events,
        meta={
            "repo": args.repo,
            "workflow_files": workflow_files,
            "since": since,
            "commands": commands,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    markdown = render_markdown(report)
    if args.out_md:
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text(markdown, encoding="utf-8")
        print(f"Wrote {args.out_md}")
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"Wrote {args.out_json}")
    if not args.out_md and not args.out_json:
        print(markdown)
    return 0


# ---------------------------------------------------------------------------
# rerun-summary mode
# ---------------------------------------------------------------------------


def cmd_rerun_summary(args: argparse.Namespace) -> int:
    """Compare this run's earlier attempts and report what passed only on rerun.

    Written for the CI step: it never raises, and every API failure becomes a
    visible note with exit code 0 so a summary outage can never fail the run.
    Attempt 1 is a no-op note and touches no API at all. Output goes to stdout
    (the workflow step appends it to ``$GITHUB_STEP_SUMMARY``).
    """
    run_id, attempt = args.run_id, args.attempt
    if attempt <= 1:
        print(render_rerun_summary(run_id, attempt, []))
        return 0

    context: dict[str, str] | None = None
    try:
        payload = gh_api_json(f"repos/{args.repo}/actions/runs/{run_id}")
        context = {
            "workflow": payload.get("name") or "",
            "event": payload.get("event") or "",
            "branch": payload.get("head_branch") or "",
        }
    except (RuntimeError, OSError, ValueError, AttributeError, TypeError) as error:
        # Cosmetic only; the summary still renders without the context line.
        print(f"Run context unavailable: {error}", file=sys.stderr)

    try:
        attempts = {
            earlier: fetch_attempt_jobs(args.repo, run_id, earlier)
            for earlier in range(1, attempt + 1)
        }
        # Parsing and row selection stay inside the fallback: a malformed
        # payload shape (for example an unhashable job name) must reach the
        # visible unavailable note, not escape the command.
        rows = rerun_summary_rows(attempts, attempt)
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(render_rerun_unavailable(run_id, attempt, str(error)))
        print(f"Rerun summary unavailable: {error}", file=sys.stderr)
        return 0
    try:
        with tempfile.TemporaryDirectory(prefix="ci-flake-rerun-") as cache_name:
            # Diagnostics go to stderr: stdout is the Markdown step summary.
            _attach_rerun_findings(
                args.repo,
                rows,
                attempts,
                Path(cache_name),
                lambda message: print(message, file=sys.stderr),
            )
    except (RuntimeError, OSError) as error:
        # Cosmetic only; the summary still renders without the signatures.
        print(f"Failure signatures unavailable: {error}", file=sys.stderr)
    print(render_rerun_summary(run_id, attempt, rows, context))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="mode", required=True)

    report_parser = subparsers.add_parser(
        "report", help="full ranking over the requested window (JSON + Markdown)"
    )
    report_parser.add_argument("--repo", default=REPO)
    report_parser.add_argument("--min-runs-per-workflow", type=int, default=100)
    report_parser.add_argument("--since", default=None, help="YYYY-MM-DD; default: 90 days back")
    report_parser.add_argument(
        "--workflows",
        default=None,
        help="comma-separated workflow file names; default: the four test workflows",
    )
    report_parser.add_argument("--out-md", default=None)
    report_parser.add_argument("--out-json", default=None)
    report_parser.add_argument("--cache-dir", default=LOG_CACHE_DEFAULT)
    report_parser.add_argument(
        "--keep-logs", action="store_true", help="keep large logs after extraction"
    )

    rerun_parser = subparsers.add_parser(
        "rerun-summary",
        help="list what passed only on rerun for one run (wired by the M3 rerun-summary feature)",
    )
    rerun_parser.add_argument("--repo", default=REPO)
    rerun_parser.add_argument("--run-id", type=int, required=True)
    rerun_parser.add_argument("--attempt", type=int, required=True)

    weekly_parser = subparsers.add_parser(
        "weekly",
        help="rolling ranking plus thresholds (wired by the M3 weekly-flake-workflow feature)",
    )
    weekly_parser.add_argument("--repo", default=REPO)
    weekly_parser.add_argument("--window-days", type=int, default=14)
    weekly_parser.add_argument("--min-runs-per-workflow", type=int, default=1000)
    weekly_parser.add_argument(
        "--workflows",
        default=None,
        help="comma-separated workflow file names; default: the four test workflows",
    )
    weekly_parser.add_argument("--out-md", default=None)
    weekly_parser.add_argument("--out-json", default=None)
    weekly_parser.add_argument("--cache-dir", default=LOG_CACHE_DEFAULT)
    weekly_parser.add_argument(
        "--keep-logs", action="store_true", help="keep large logs after extraction"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "report":
        return cmd_report(args)
    if args.mode == "rerun-summary":
        return cmd_rerun_summary(args)
    return cmd_weekly(args)


if __name__ == "__main__":
    sys.exit(main())
