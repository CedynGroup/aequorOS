# Tamper-evident records

Evidence for issues #413, #356 and #401. PostgreSQL remains the record store.
No reported financial figure or calculation rule changes.

## Authorized delivery boundary

Firstmate approved the four-layer delivery contract during the review of this
run: this branch and [PR #504](https://github.com/CedynGroup/aequorOS/pull/504)
validate audit chaining; [PR #505](https://github.com/CedynGroup/aequorOS/pull/505)
delivers filing locks; [PR #506](https://github.com/CedynGroup/aequorOS/pull/506)
delivers compliance storage; and [PR #507](https://github.com/CedynGroup/aequorOS/pull/507)
delivers the append-only sweep. Each layer is validated separately. This audit
layer alone does not certify all of issue #413. No user-visible change;
screenshots are not applicable.

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
The dedicated `aequoros_audit_owner` NOLOGIN role owns both source tables, both
chain tables, the definer functions and the shared append-only guard. The chain
tables force RLS; an owner-only policy allows the definer to append entries and
advance heads. Runtime roles retain their existing source SELECT/INSERT grants
and insert through the trigger; they have no direct chain write or helper EXECUTE
grant. The all-tenant worker has SELECT on chain entries and heads. Source-table
ownership also prevents runtime credentials from disabling the source guards;
ownership of the shared guard prevents replacing its implementation.

Historical rows are backfilled under an exclusive lock with RLS temporarily
suspended by the existing owner-only migration helper. This seals their state
at migration time; it cannot certify what happened before the migration.

Set `AUDIT_INTEGRITY_ENABLED=true` on the core worker. Starting with its first
poll, it runs a single platform task independent of tenant ticks or bank
provisioning. A singleton `audit_integrity_schedule` row records the next hour
boundary; a row lock with SKIP LOCKED and a committed advance coordinates worker
replicas and restarts. The worker requires SELECT/UPDATE on that schedule row.
AI and BI workers do not run the task. The task checks both full streams using
an all-tenant BYPASSRLS role and records successful counts in operational logs.
Tenant-readable job progress carries only its existing tenant-local scheduling fields. Verification
recomputes every hash from the source row, verifies links and sequence continuity,
and compares row count and final hash to the protected head. Content edits,
missing events, deleted middle entries and deleted tails fail verification.
Tenant ticks never invoke the platform sweep. Each completed hourly task pages
once for each broken stream; adding organizations or worker replicas does not
multiply sweeps or pages. Monitor scan duration as volume grows.

Set `AUDIT_INTEGRITY_ALERT_TOPIC_ARN` to a standard AWS SNS topic subscribed to
the on-call pager and grant the worker's AWS identity `sns:Publish` on that topic.
Verification publishes safe incident metadata for mismatches and unavailable
verification, independently of the database transaction. It also emits the
structured ERROR condition `audit.chain_broken`. An absent topic or failed
publication emits `paging_unconfigured` or `paging_failed`, without vendor error
details. A broken chain continues to be checked and paged on later hourly tasks.
Before production acceptance,
exercise the route in a disposable environment and attach the pager receipt to
the BoG evidence pack. No pager credential or destination is stored in bank data.

Migration administrators and superusers can disable triggers and rewrite database
history and its head together. This is tamper evidence against application
credentials only when the production credential separation below is installed,
not an independent ledger against a compromised database administrator. Retained backups
provide the independently retained comparison boundary. Preserve the first
backup after migration and later manifests for examiner comparison.

## Verification evidence

`tests/db/test_audit_integrity.py` executes real migrated PostgreSQL guards,
transaction rollback, source tampering and chain deletion. It captures the broken
chain alert, concurrent writers and compatibility with added source columns.
`tests/services/test_audit_integrity_schedule.py` proves the audit
worker reaches the platform task with the audit flag alone, without tenant
ticks, while AI and BI workers do not repeat it. The PostgreSQL tests exercise
runtime-role inserts, denied ALTER/UPDATE/DELETE/TRUNCATE and helper replacement,
forced chain RLS, a bank-free hourly cadence and concurrent worker exclusion.
An unavailable verifier emits and publishes the same paging condition; the
platform task advances to the next hour even after a mismatch or refusal.
`tests/core/test_migration_credentials.py` exercises the migration credential
boundary for each runtime connection role. `tests/core/test_audit_integrity_alerts.py` verifies SNS routing, the safe
incident payload, and visible missing/failed delivery with a fake publisher.
PostgreSQL tests use disposable schemas, never the primary database.


## Required deployment follow-up

The AXI driver will file the deployment follow-up issue after this fix is
concrete; no issue number has been assigned here. Complete it before deploying
this migration in production or staging:

- Give the migration container its own `MIGRATION_DATABASE_URL`, distinct from
  API `DATABASE_URL`, `WORKER_DATABASE_URL`, `OPERATOR_DATABASE_URL` and
  `BI_DATABASE_URL`. Alembic refuses a missing migration URL or a shared role in
  production/staging; local/test runs retain the existing URL fallback. Runtime
  containers must never receive migration credentials.
- The migration identity needs CREATEROLE to create the NOLOGIN owner, or the
  database administrator must precreate it and grant the migrator ADMIN,
  INHERIT and SET membership. The migration grants only that administrative
  identity owner membership. Runtime identities must have no direct or inherited
  membership in the owner or migrator roles, no SUPERUSER/CREATEROLE/CREATEDB,
  no ownership of the database or schema, and no schema CREATE privilege.
- Keep worker/operator all-tenant BYPASSRLS identities separate from the
  migrator. Existing BYPASSRLS roles receive chain SELECT and schedule
  SELECT/UPDATE during migration. Provision those roles first, or apply those
  narrow grants afterward when provisioning them. Preserve existing authorized
  source SELECT/INSERT grants; grant no chain INSERT/UPDATE/DELETE/TRUNCATE and
  no definer EXECUTE to runtime identities. Avoid blanket post-migration grants
  and bootstrap ownership reassignment.
- Confirm using the deployed API role that a normal audit insert commits its
  chain entry, while source/chain guard disabling, entry/head rewriting,
  truncation and function replacement fail. Confirm one sweep with zero banks
  and one mismatch page per broken stream with multiple core worker replicas.
  Attach role/grant evidence and a pager receipt to #356/#401.
