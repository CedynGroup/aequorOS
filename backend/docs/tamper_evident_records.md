# Tamper-evident records

Evidence for issues #413, #356 and #401. PostgreSQL remains the record store.
No reported financial figure or calculation rule changes.

## Audit chain

`202610100090` installs a separate SHA-256 chain for `audit_events` and
`operator_audit_log`. An AFTER INSERT trigger includes every row column in
PostgreSQL's JSONB serialization, normalized to UTC, with a version tag, stream,
sequence and previous hash. A locked stream-head row serializes concurrent
writers. Event, chain entry and head advance commit or roll back together.
Each entry records its captured column names so adding a source column does not
invalidate historical hashes. Removing or renaming captured fields requires an
explicit chain-version migration; do not silently reinterpret old entries.
The helper functions are not callable by application roles. Their search path
is pinned to the migration schema with pg_catalog first and pg_temp last.
Tenant roles cannot read or write the chain tables; the worker has SELECT only.

Historical rows are backfilled under an exclusive lock with RLS temporarily
suspended by the existing owner-only migration helper. This seals their state
at migration time; it cannot certify what happened before the migration.

Set `AUDIT_INTEGRITY_ENABLED=true` on the core worker. It keeps the existing
hourly scheduler alive, checks both full streams using an all-tenant BYPASSRLS
role, and records successful counts in operational logs. Tenant-readable job
progress carries only its existing tenant-local scheduling fields. Verification
recomputes every hash from the source row, verifies links and sequence continuity,
and compares row count and final hash to the protected head. Content edits,
missing events, deleted middle entries and deleted tails fail verification.
Every organization's tick verifies the platform streams; this favors detection
over introducing a separate scheduler. Monitor scan duration as volume grows.

Set `AUDIT_INTEGRITY_ALERT_TOPIC_ARN` to a standard AWS SNS topic subscribed to
the on-call pager and grant the worker's AWS identity `sns:Publish` on that topic.
Verification publishes safe incident metadata for mismatches and unavailable
verification, independently of the database transaction. It also emits the
structured ERROR condition `audit.chain_broken`. An absent topic or failed
publication emits `paging_unconfigured` or `paging_failed`, without vendor error
details. A broken chain continues to be checked and paged on later ticks.
Before production acceptance,
exercise the route in a disposable environment and attach the pager receipt to
the BoG evidence pack. No pager credential or destination is stored in bank data.

Owners/superusers who can disable triggers can rewrite database history and its
head together. This is tamper evidence against application credentials, not an
independent ledger against a compromised database administrator. Retained backups
provide the independently retained comparison boundary. Preserve the first
backup after migration and later manifests for examiner comparison.

## Verification evidence

`tests/db/test_audit_integrity.py` executes real migrated PostgreSQL guards,
transaction rollback, source tampering and chain deletion. It captures the broken
chain alert, concurrent writers and compatibility with added source columns.
`tests/services/test_audit_integrity_schedule.py` proves the audit
flag alone reaches verification and schedules the next tick after a mismatch.
An unavailable verifier emits and publishes the same paging condition and preserves
the tick. `tests/core/test_audit_integrity_alerts.py` verifies SNS routing, the safe
incident payload, and visible missing/failed delivery with a fake publisher.
PostgreSQL tests use disposable schemas, never the primary database.
