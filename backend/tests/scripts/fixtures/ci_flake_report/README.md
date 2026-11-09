# ci_flake_report fixtures

Recorded from the repository's own GitHub Actions API (`gh api`) in November 2026
and trimmed to the fields the measurement script reads. Provenance per file:

- `workflow_runs.json` — `repos/CedynGroup/aequorOS/actions/workflows/{file}/runs`
  response, trimmed to a representative slice (push / pull_request / merge_group,
  reruns included).
- `attempt_jobs_failed.json` — `actions/runs/42340795777/attempts/1/jobs`
  (journeys shard 4/4 failing; attempt 1 of 4).
- `attempt_jobs_passed.json` — `actions/runs/42340795777/attempts/4/jobs`
  (same run, attempt 4 passed; the rerun-only pass).
- `schema_shard_jobs.json` — `actions/runs/42371307347/attempts/1/jobs`
  (Risk Service CI Postgres schema shard 2/2 failing).
- `pytest_doc_deletion.log` — excerpt of job 127610379492's log (schema shard 2/2
  with the three `test_document_deletion_timeout_integration.py` failures).
- `playwright_rule_status.log` — excerpt of the shard 4/4 job log for run
  42564446882 (#846 badge-out-of-view failure).
- `typecheck_baseline.log` — excerpt of a `static` job log (baseline typecheck
  reporting "New type errors").
- `setup_minio_504.log` — excerpt of job 128682365829's log (setup-minio curl
  exit 22 download failure, run 42471215671).
- `passing_download_retry.log` — reconstructed in the recorded format: the same
  setup-minio download lines as `setup_minio_504.log` but in a job whose
  `curl --retry` recovered and the step finished successfully, so nothing may be
  counted.

Logs keep their raw timestamps so the timestamp-stripping logic is exercised on
real input. No credentials appear: runner logs mask secrets, and the trimmed
excerpts avoid env blocks.
