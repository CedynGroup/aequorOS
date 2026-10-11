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

The compliance-storage layer must use the regulatory retention period confirmed
by counsel (#246).

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
boundary and a recovery epoch; a row lock with SKIP LOCKED and a committed advance coordinates worker
replicas and restarts. The worker requires SELECT/UPDATE on that schedule row.
AI and BI workers do not run the task. The task checks both full streams using
an all-tenant BYPASSRLS role and records successful counts in operational logs.
Tenant-readable job progress carries only its existing tenant-local scheduling
fields. Verification recomputes every hash from the source row, verifies links
and sequence continuity, and compares row count and final hash to the protected head. Content edits,
missing events, deleted middle entries and deleted tails fail verification.
Tenant ticks never invoke the platform sweep. Each completed hourly task checks
both streams; adding organizations or worker replicas does not multiply completed
sweeps. Monitor scan duration as volume grows.

Set `AUDIT_INTEGRITY_ALERT_TOPIC_ARN` to a standard AWS SNS topic subscribed to
the on-call pager and grant the worker's AWS identity `sns:Publish` on that topic.
The complete platform-task boundary publishes safe incident metadata for
mismatches and unavailable verification, independently of the database transaction.
Each running worker retains its outage episode outside the database transaction:
it pages on the first failure and on escalation to a newly broken stream.
Acquisition, advancement, commit and session failures cannot reset suppression.
A healthy sweep increments `recovery_epoch` in the singleton row in the same
commit as the schedule advance. Every worker reads that epoch on every poll,
including non-due polls, and clears its local episode when it observes a newer
committed epoch. Recovery by one replica therefore allows every replica to page
a later outage. Mismatch pages require a successful schedule advance and commit.

Only successful SNS publication latches a reported condition. Failed delivery
remains eligible within the same episode, with attempts separated by at least
five minutes; a successful page is suppressed until escalation or shared
recovery. Mismatch retries occur when later scheduled sweeps reconfirm the
mismatch. Paging state remains local to a worker lifetime: each replica or
restarted worker can send its initial page during an outage. A worker must
observe the committed recovery epoch before it can identify a later episode.
The task also emits the structured ERROR condition `audit.chain_broken`.
An absent topic or failed publication emits `paging_unconfigured` or
`paging_failed`, without vendor error details. A broken chain continues to be
checked on later hourly tasks.
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
An unavailable verifier emits and publishes the same paging condition; after a
mismatch or verifier refusal, the platform task advances to the next hour only
if the schedule transaction commits. A failed acquisition, advance or commit
leaves the task eligible for another poll, with paging bounded as described above.
The worker reports once per outage episode at the complete task boundary,
including combined verifier and schedule acquisition, advance, commit or session
failures, while ordinary job dispatch continues. Behavioral regressions cover
repeated failures, committed mismatch paging, escalation, bounded failed-delivery
retries, and two-replica recovery followed by a new outage. Native PostgreSQL
regressions exercise the committed epoch and its visibility to non-due peers. Migration regressions exercise
both forward and round-trip upgrades with superuser test credentials, using and
cleaning up an ordinary restricted migration role. Administrator regressions use
either `POSTGRES_ADMIN_URL` or a superuser `TEST_DATABASE_URL`; without superuser
credentials they skip locally and fail when `POSTGRES_PRIVILEGE_TESTS_REQUIRED`
is set. Ordinary migration tests still need only `TEST_DATABASE_URL`.
`tests/core/test_migration_credentials.py` exercises the migration credential
boundary for each runtime connection role.
`tests/core/test_audit_integrity_alerts.py` verifies SNS routing, the safe incident
payload, and visible missing/failed delivery with a fake publisher.
PostgreSQL tests use disposable schemas, never the primary database.

## Required deployment follow-up

Complete [deployment follow-up #509](https://github.com/CedynGroup/aequorOS/issues/509)
before deploying this migration in production or staging:

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
- The legacy `scripts/bootstrap_db.sh` is incompatible with this migration:
  it creates a NOCREATEROLE migrator without preparing audit-owner membership,
  reassigns existing table ownership and applies blanket runtime grants.
  Do not use it to initialize or refresh an audit-protected database; prepare
  roles and grants as described above before running Alembic.
- Confirm using the deployed API role that a normal audit insert commits its
  chain entry, while source/chain guard disabling, entry/head rewriting,
  truncation and function replacement fail. Confirm one sweep with zero banks
  and one initial mismatch page per worker and broken stream during a sustained
  incident with multiple core worker replicas.
  Attach role/grant evidence and a pager receipt to #356/#401.

## Filing and input seals

`202610100091` forbids financial-content updates on every generated package:
identity, version, source-run references, input snapshot and its hash, and the
master-data `register_state_digest` used by LRT attestation and evidence exports. Application
roles lose table UPDATE and receive only the lifecycle-column grants (including
`id` for PostgreSQL foreign-key key-share locking; changing it is trigger-blocked).
Application roles also lose DELETE/TRUNCATE on packages and regulatory runs,
including unsigned packages and unfinished runs.
Signed packages cannot be deleted; their content digest is sealed. Existing
signature evidence keeps the deletion seal active after a void. Lifecycle status,
review bookkeeping and regulator outcomes continue to advance. A correction
uses existing generation/resubmission authorization and `supersedes_id`, retaining
the original version and figures. Voiding withdraws certification; it never opens
an in-place financial-content edit. Completed regulatory runs, including their
inputs and metrics, cannot be updated or deleted. Queued/running work can finish.

`tests/db/test_signed_filing_locks.py` covers runtime column permissions and
owner-trigger refusals, completed runs and versioned corrections. Its LRT
regression retains the attestation/export binding through certification and
voiding while refusing changes to the register-state digest.

Apply the [deployment role and grant requirements](#required-deployment-follow-up)
to filing seals too. The worker's BYPASSRLS privilege does not grant permission
to alter evidence.
