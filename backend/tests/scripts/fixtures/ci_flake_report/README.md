# ci_flake_report fixtures

Recorded from the repository's own GitHub Actions API (`gh api`) in November 2026
and trimmed to the fields the measurement script reads. Provenance per file:

- `workflow_runs.json` — `repos/CedynGroup/aequorOS/actions/workflows/{file}/runs`
  response, trimmed to a representative slice (push / pull_request / merge_group,
  reruns included). Run ids and event/attempt/conclusion shapes are real; head
  SHAs are placeholder repeats and run numbers substituted.
- `attempt_jobs_failed.json` — shape of `actions/runs/42340795777/attempts/1/jobs`
  (journeys shard 4/4 failing; attempt 1 of 4). Job ids are synthetic
  sequential values, not the real ones.
- `attempt_jobs_passed.json` — shape of `actions/runs/42340795777/attempts/4/jobs`
  (same run, attempt 4 passed; the rerun-only pass). Synthetic job ids.
- `schema_shard_jobs.json` — shape of `actions/runs/42371307347/attempts/1/jobs`
  (Risk Service CI Postgres schema shard 2/2 failing). Synthetic job ids.
- `pytest_doc_deletion.log` — excerpt of the real schema shard 2/2 job log
  (job 128400866871, run 42371307347 attempt 1, 2026-11-07) with the three
  `test_document_deletion_timeout_integration.py` failures.
- `playwright_rule_status.log` — excerpt of the real shard 4/4 job log
  (job 128987843134, attempt 1) for run 42564446882 (#846 badge-out-of-view
  failure).
- `typecheck_baseline.log` — excerpt of a real `static` job log (job
  125147575624, 2026-11-02; baseline typecheck reporting "New type errors").
- `setup_minio_504.log` — excerpt of real job 128682365829's log (setup-minio
  curl exit 22 download failure, run 42471215671).
- `passing_download_retry.log` — reconstructed in the recorded format: the same
  setup-minio download lines as `setup_minio_504.log` but in a job whose
  `curl --retry` recovered and the step finished successfully, so nothing may be
  counted.
- `apt_mirror_outage.log` — excerpt of the real Dashboard journeys shard 1/4 log
  (job 130580527090, run 43037570348, 2026-11-11 apt-mirror outage): the
  `playwright install-deps chromium` step's apt retries (`Ign:`/`Err:`/`W:`
  lines), the fatal `E: Failed to fetch` block, `Error: Installation process
  exited with code: 100` and the step's exit code 1. Middle sections of the
  `Ign:` retry list are omitted; every kept line is verbatim.
- `passing_apt_retry.log` — reconstructed in the recorded format: the same
  azure-mirror retry shape as `apt_mirror_outage.log` but the fetches recover,
  `install-deps` completes and the step finishes successfully, so nothing may
  be counted.
- `test_failure_with_apt_warnings.log` — reconstructed in the recorded format:
  a genuine Playwright test failure in a shard whose apt phase printed only
  transient mirror warnings (`Ign:`/`Err:`/`W:` lines, packages fetched on
  retry) and whose step aborted with the test runner's exit code 1, not apt's
  100; the test failure stays a flake and no infrastructure finding is
  produced.

Logs keep their raw timestamps so the timestamp-stripping logic is exercised on
real input. No credentials appear: runner logs mask secrets, and the trimmed
excerpts avoid env blocks.

The `.log` fixtures are exempted from the repository-wide `*.log` ignore in the
root `.gitignore` — without that exemption a fresh checkout loses them and the
log-extraction tests fail with `FileNotFoundError` (this actually happened to
PR #857's first CI run).
