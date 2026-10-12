# Tamper-evident records

Evidence for issues #413, #356 and #401. PostgreSQL remains the record store.
No reported financial figure or calculation rule changes.

## Authorized delivery boundary

The four-layer delivery contract assigns
[PR #504](https://github.com/CedynGroup/aequorOS/pull/504)
to audit chaining; [PR #530](https://github.com/CedynGroup/aequorOS/pull/530)
delivers filing locks; [PR #533](https://github.com/CedynGroup/aequorOS/pull/533)
delivers compliance storage; and [PR #507](https://github.com/CedynGroup/aequorOS/pull/507)
delivers the append-only sweep. Each layer is validated separately. The audit
and filing layers alone do not certify all of issue #413. The compliance-storage
layer adds submission refusals described under [write-once objects and backups](#write-once-objects-and-backups).

The compliance-storage layer must use the regulatory retention period confirmed
by counsel (#246).

## Sign-in and inspector session retention

Refresh-token state is credential bookkeeping, not the audit chain. Enable
`AUTH_SESSION_PURGE_ENABLED=true` on the core worker to keep the existing hourly
tenant scheduler alive and enqueue `session_purge`. Each job deletes at most
`AUTH_SESSION_PURGE_BATCH_SIZE` rows (default 1000, maximum 10000) belonging to
its organization. Eligibility is expiry **or** revocation strictly older than
`AUTH_REFRESH_TOKEN_RETENTION_DAYS` (default 30 days). The configured interval
is retained for incident investigation and is the sole cleanup window. Settings
refuse startup if this window is shorter than `AUTH_REFRESH_ROTATION_GRACE`
(seconds); increase retention or reduce grace to make the configuration valid.
Rows with neither expiry nor revocation older than the cutoff stay, and retries
are idempotent.
The scheduler enqueues at most one purge per organization per UTC hour, including
completed jobs; PostgreSQL transaction locks serialize enqueueing across worker
replicas. Hourly batches drain any backlog without a single long cleanup transaction.
Provisioning seeds the tenant's first tick in its onboarding transaction,
including when the worker is already running. The operator's scheduling flags
do not gate seeding; the worker decides whether a tick runs or stays inert.
The switch defaults off and is rechecked by the handler. Monitor per-job
`refresh_tokens_deleted`; repeated full batches indicate a growing backlog.

`operator_inspector_sessions` records who accessed which tenant and why. It is
audit evidence, including expired and ended sessions, and is excluded from the
credential purge. As with the audit records in issue #413, no approved deletion
period is currently specified: the regulatory period requires counsel's
confirmation (#246), as described under
[write-once objects and backups](#write-once-objects-and-backups). Until a policy
explicitly permits deletion after an approved retention interval, inspector
sessions are retained indefinitely. Migration `202610120098` enforces this with
DELETE (including organization-cascade deletion) and TRUNCATE guards while
allowing the existing end-session UPDATE. A future approved disposal policy
needs an explicit migration and must never remove sessions inside its retention
period; the storage Object Lock placeholder is not authority to delete them.

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

## Bank audit read API

`GET /api/v1/banks/{bank_id}/audit-log` merges bank-attributable tenant and
staff audit events, newest first. The inclusive `start` and exclusive `end`
require timezone-aware timestamps. Exact actor, event-type and outcome filters
combine before cursor pagination; cursors are bound to their organization and
bank. Each event contains its detail, recorded before/after values where present,
source, actor identity, outcome and sequence within its own stream. Staff actors
are explicitly classified as `aequoros_staff`; their detail is projected through
an allow-list rather than exposing internal operator context.

A complete active human binding for Audit/restricted over the requested bank
is required. Security officer and Compliance officer carry VIEW and EXPORT;
Auditor retains VIEW only. Viewer, Analyst, administrator and owner bundles do
not themselves authorize this feed. The officer bundles are grantable through
the existing owner API and confined to whole-institution Audit coverage by
service validation and a database CHECK. No officer binding is backfilled.
Every successful page read appends `audit_log.viewed` in the same transaction;
a failure to retain that event refuses the read. Streaming export and the
Members composer options are the following delivery stages of issue #494.

Attribution uses an explicit `details.bank_id`, bank entity target,
binding `details.scope.institution_id`, or the retained subject's bank ownership.
Organization-wide binding grants and removals cover every bank in that
organization, including after revocation. Feed selection, chain visibility,
verification, inspection evidence and operator activity use the same retained
binding scope.
The shared event writer snapshots bank ownership for ingestion batches, mapping
configs, position snapshots, regulatory runs, packages and their artifact records,
core-database connections, filing templates, capital/ILAAP and contingency funding
plans, scenario analyses, bank-local scenarios and management plans, stress sign-offs,
and ICAAP cycles and child records; their lifecycle events use this boundary. Bank
parameter-register targets resolve through their bank platform IDs. Access requests
retain their `institution_id` through the same writer. Push staging snapshots the
caller's resolved bank; historical committed pushes resolve through their retained
ingestion batch. Deleted ICAAP children resolve through the recorded cycle.
Historical events resolve through the same tenant-scoped ownership catalogue
without rewriting sealed records.
Contradictory bank markers or subject ownership refuse visibility. Staff events
use `detail.bank_id`, the retained batch/mapping ownership, or a mapping
change’s `detail.after.bank_id`. The
shared staff writer snapshots that ownership, and historical reads and integrity
verification resolve it through the same catalogue. Recorded `note` explanations
become the public `reason`; batch actions name the batch as their target, and bank
actions without a more specific target name their bank. New inspector sessions
require a support reference, captured by both console entry forms. The shared
staff writer snapshots the caller-owned session's explanation and reference,
while preserving an action's own note. Historical staff and impersonated events
resolve those fields through `bank_audit_inspection_context`, which checks the
event's bank attribution, session tenant and operator identity before returning
only reason/reference. The API has no inspector-session table read grant. This
historical context supplements the sealed event payload; it is not included in
the event's integrity claim.
The staff writer receives the actual returned/affected resources and writes one
separate event per bank, validating each bank's organization. It stores identities,
not the response data; one bank's event never contains another bank's payload.
An empty bank-specific result retains a platform event rather than inventing bank
access. Shared tenant identity/configuration accesses apply to every bank in that
tenant. A fleet operation without an Inspector session automatically records its
operation purpose and a nullable support reference. Only an explicitly supplied
session ID attaches inspection evidence; no new fleet request fields are mandatory.
Repeated response identities are deduplicated and bank ownership is validated in
one batch. Publication actions record each bank's delivery status and outcome:
complete is success, failed is failure, skipped remains skipped, and unknown or
missing statuses remain unspecified.

### Staff operation coverage catalogue

Every operator surface is classified below; new surfaces must choose an explicit
bank-resource or platform-only policy. Platform-only activity is owned by AequorOS.
Historical sealed reads with no provable resource association remain platform-only.
The migration's source RLS, chain-entry visibility, previous-link verifier and
session-evidence helper use the same historical batch/mapping ownership catalogue
as the read API. Shared configuration mutations snapshot each affected bank before
commit; ownership is retained even when a resource is later removed.

| Operator surface                                                                                                                                           | Recording/ownership policy                                                                                             |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `tenants` list/get                                                                                                                                         | Actual returned bank rows, separately by organization and bank.                                                        |
| Tenant users, storage, entitlements                                                                                                                        | Shared tenant configuration: every bank of that organization.                                                          |
| `activity`                                                                                                                                                 | Bank identities of the returned ingestion/job/run/package/audit items, after paging.                                   |
| `inspector_reads` metrics/findings/ingestion/config                                                                                                        | Banks named by actual response resources; batch detail resolves its retained batch.                                    |
| `inspector_fix` recompute/official-run/ingestion/dedup                                                                                                     | Actual resolved bank or batch, with session evidence and remediation note.                                             |
| `inspector_fix` mapping/threshold config                                                                                                                   | Mapping's retained bank; threshold applicability by tenant, jurisdiction and institution class. Before/after retained. |
| Inspector session start/list/end/act-token                                                                                                                 | Shared tenant access window; each affected tenant bank. Token material is never recorded.                              |
| `bi_backfill`                                                                                                                                              | Resolved target bank and session evidence.                                                                             |
| `data_engines`, `jobs` and stranded-dedup boards                                                                                                           | Banks in the actual returned resources, including page/limit selection.                                                |
| Staff `audit_log` read                                                                                                                                     | Banks named by returned staff evidence; reading itself is audited.                                                     |
| Encryption-key get/connect/rotate/retire                                                                                                                   | Explicit bank, with existing safe key metadata.                                                                        |
| Provisioning                                                                                                                                               | Created bank and existing success/failure step evidence; rollback failures remain platform-visible.                    |
| Desk entitlement read/grant-tier/grant-dataset/revoke                                                                                                      | Shared tenant entitlement/configuration access: each tenant bank.                                                      |
| Desk methodology approval                                                                                                                                  | All banks addressed by the refresh policy, including banks without current live data.                                  |
| Desk determination/curve/operating-environment publish                                                                                                     | Actual per-bank publication results, including failed deliveries.                                                      |
| Desk publication history read                                                                                                                              | Banks in the returned publication results.                                                                             |
| Regulatory parameter approval                                                                                                                              | Same jurisdiction/type/class bank selection as refresh scheduling, including banks without current live data.          |
| Regulatory parameter list/proposal; desk methodology list/create/default/propose/PDF                                                                       | Platform policy/register work, before bank applicability changes.                                                      |
| Desk observation/capture/snippet reads/writes; unpublished determination list/get/package/create/compute/adjust/submit/approve/reject/supersede            | Platform research and maker/checker workflow; no bank-owned resource access. Existing mutation audit retained.         |
| Curve definitions/create/propose/approve/construct/stage/submit/approve; FX-forward construct; operating-environment preview/stage/list/get/submit/approve | Platform research/calculation work before publication; existing mutation audit retained.                               |
| Staff authentication and operator list/create/password-reset/deactivate/reactivate                                                                         | Platform staff identity; existing authentication/management evidence retained.                                         |
| `overview`, `worker_health`                                                                                                                                | Aggregate fleet counts and runtime heartbeats, no identifiable bank resources.                                         |

Password sign-in, SSO and signing re-authentication retain success and refusal
events before returning. Known users are attributed only to banks reachable
through their complete active scoped bindings; unidentified attempts and users
without bank authority remain platform-visible. Verified IdP `acr` and `amr`
claims are retained; an explicit `mfa` method also records MFA success. Password
authentication never invents MFA evidence or stores credentials.

The shared event writer and historical presentation normalize known ingestion,
capital-run and dedup outcomes, filing status transitions and position override
values. Outcome filters use the same mapping; events without a known or explicitly
recorded outcome remain unspecified. Historical sealed payloads remain unchanged.

Banks own incidents observable in their audit events;
AequorOS owns incidents outside that visibility boundary.

Migration `202610120100` enables forced RLS on the staff source and adds scoped
chain-entry reads. The new API role gains SELECT on these two tables and
EXECUTE on `bank_audit_previous_link`; it gains no global-head access, chain
write permission or BYPASSRLS. The helper returns only a predecessor-link
boolean for an attributable event. Its NOLOGIN audit owner, pinned search path
and temporary schema-CREATE grant follow the existing chain custody pattern.
The audit owner has tenant-scoped SELECT on the ownership catalogue's subject
tables so the predecessor check applies the same historical attribution rule.
Existing all-tenant operator and verifier policies retain their own reads.
Legacy runtime credentials need their existing source/chain SELECT grants and
the new helper EXECUTE grant, which the migration adds for existing chain
readers. Before switching a legacy tenant API role without chain SELECT, apply
the same narrow SELECT grants and helper EXECUTE grant as `aqs_api`; forced RLS
continues to restrict these reads. Do not grant global-head SELECT to the API.

`integrity=verified` means the source payload matches its database-generated
hash and its previous-hash link matches the preceding stored entry. It does
not certify the complete platform stream, its tail/head, events hidden by
bank visibility, or a predecessor's source payload. The explicit
`integrity_scope=event_payload_and_previous_link` preserves this distinction.
Missing entries and mismatches report `failed`; SQLite reports `unavailable`.
The platform worker still owns full-chain verification and paging.

Executable evidence lives in `tests/identity/api/test_audit_log.py` and
`tests/db/test_bank_audit_reader.py`: scoped authority, filtering, cursor ties,
view auditing, staff projection, real NOBYPASSRLS reads and privileged tampering.
The API tests use their own migrated PostgreSQL schema and execute HTTP reads
as `aqs_api`; they deliberately skip without `TEST_DATABASE_URL`. Run both
modules under the native `local_services.py run --postgres --role-admin` wrapper.

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
is set. Migration-test role preparation is owned by the
[role handoff runbook](../../docs/ops/database-roles.md#migration-and-local-development-order).
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
- Prepare migration roles and owner membership through the
  [role handoff runbook](../../docs/ops/database-roles.md#migration-and-local-development-order)
  before running Alembic.
- Keep worker/operator all-tenant BYPASSRLS identities separate from the
  migrator. Existing BYPASSRLS roles receive chain SELECT and schedule
  SELECT/UPDATE during migration. Provision those roles first, or apply those
  narrow grants afterward when provisioning them. Preserve existing authorized
  source SELECT/INSERT grants; grant no chain INSERT/UPDATE/DELETE/TRUNCATE and
  no audit-chain definer EXECUTE to runtime identities. Avoid blanket
  post-migration grants and bootstrap ownership reassignment.
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
Application roles also lose DELETE/TRUNCATE on packages, regulatory runs and
attestation signatures, including unsigned packages and unfinished runs.
Signature deletion is trigger-blocked alongside the existing update guard;
new signatures can still be inserted.
Signed packages cannot be deleted; their content digest is sealed. Existing
signature evidence keeps both the deletion and content-digest seals active after
a void. The package guard pins its search path to `pg_catalog`, the migration
schema and then `pg_temp`, so temporary tables cannot shadow retained signatures.
Lifecycle status, review bookkeeping and regulator outcomes continue to advance. A correction
uses existing generation/resubmission authorization and `supersedes_id`, retaining
the original version and figures. Voiding withdraws certification; it never opens
an in-place financial-content edit. Completed regulatory runs, including their
inputs and metrics, cannot be updated or deleted. Queued/running work can finish.

`tests/db/test_signed_filing_locks.py` covers runtime column permissions and
owner-trigger refusals, completed runs and versioned corrections. Its LRT
regression retains the attestation/export binding through certification and
voiding while refusing changes to the register-state digest, signature deletion
and content-digest changes under temporary-table shadowing and altered search paths.

Apply the [deployment role and grant requirements](#required-deployment-follow-up)
to filing seals too. The worker's BYPASSRLS privilege does not grant permission
to alter evidence.

## Write-once objects and backups

Set `STORAGE_OBJECT_LOCK_ENABLED=true` for the AWS deployment. Local MinIO and
hermetic fixtures retain their existing behavior with this flag off. Provisioning
creates lock-capable output buckets and refuses an existing output bucket without Object
Lock or with `Rule.DefaultRetention` in any mode. Bucket defaults retain every new
version, including working drafts. Provisioning, application writes, presigned uploads
and filing retention refuse incompatible Object Lock configurations before writing
objects or changing retention. Default-retention refusals name the bucket and rule.
They never rewrite an existing Object Lock configuration.
The enabled application and provisioning roles need `s3:GetBucketObjectLockConfiguration`
on output buckets to verify this policy; inability to read it refuses the operation.
Enable Object Lock/versioning without a default retention rule on existing output buckets through the
infrastructure owner before switching on the flag. At the shared submission boundary,
all artifacts admitted by `filing_admits_artifact()` receive confirmed `COMPLIANCE`
retention on their exact archived object versions before channel delivery or a
manual submission commits. This includes previously signed versions created with
the flag off, canonical exports, and the official BoG formula workbook. Internal
SDI working sheets, ICAAP Word drafts and unrelated outputs remain unlocked in the
supported output-bucket configuration. Dedicated backup buckets may carry a default
retention rule; each dump and manifest still receives explicit confirmed `COMPLIANCE`
retention.
Exports and signing archive bytes without automatically locking every output;
read-only filing previews do not apply retention. Attachments are recorded as a
manifest, not transmitted artifacts, and are outside this object retention set.
Retention can be extended but is never shortened, including an existing longer
governance interval. Required deadlines round up to whole seconds so storage
timestamp precision cannot shorten the interval. A retention PUT is followed by
a version-specific GET; submission refuses if compliance mode or the required
date cannot be confirmed.
The shared submission preparation boundary returns HTTP 409 with
`error_code=filing_retention_unconfirmed` for storage failures during auto-export
or exact-version retention, including output buckets without Object Lock or with
`Rule.DefaultRetention`. Refusal precedes channel delivery and the submission
event; package status and financial content stay unchanged. Resolve
the storage capability, permissions or archived-version problem before retrying.
The submission event records each retained `storage_version_id`.

For historical filings, run the tenant- and bank-scoped inventory from `backend`:

```bash
uv run python -m scripts.retain_filed_objects --organization-id OR-XXXXXXXX --bank-id BK-XXXXXXXX
```

This is a dry-run by default: it reads submission manifests and archived version
rows and prints bucket/key/version/date locators, without provisioning buckets or
writing database or object-store state. Resolve missing manifests, missing version
rows or nonconcrete versions before rollout; the command refuses those records.
For older manifests without version IDs it resolves the latest matching archived
kind/path/checksum at or before the submission, never the current object-store key.
Review the inventory and counsel-approved period, enable Object Lock on the
existing buckets, then explicitly pass `--apply` with the retention flag enabled
to apply and verify retention. This is an irreversible deployment-time operator
decision; never run it against real buckets during development or validation.
Re-running only extends retention and never shortens existing compliance locks.
Presigned writes keep normal encryption rules; they cannot bypass the shared
filing boundary's exact-version retention requirement.

A delete marker hides the current key but does not remove a
locked version. Apply an IAM deny on DeleteObject for evidence buckets if hiding
current keys must also be prevented.

`STORAGE_OBJECT_LOCK_RETENTION_DAYS` sets the interval in days; its default in
[`StorageEngineSettings`](../app/storage/config.py) is **an operational placeholder,
confirm with counsel, #246**. This is not a statement of BoG law or legal sign-off.
Counsel and the infrastructure owner must approve the production
period before enabling compliance retention, which cannot be shortened on stored
versions. In AWS, even account administrators cannot delete or shorten a locked
version during that interval. See [AWS Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html).

`backup_database.py` publishes both its dump and manifest when the flag is on.
Configure `BACKUP_WORM_BUCKET` to an existing versioned Object Lock bucket and
`STORAGE_KMS_KEY_ID` to the platform backup encryption key. Uploads use unique
prefixes, SSE-KMS and compliance retention, verify concrete version IDs, and print
those locators for the backup runner's evidence record. Publication errors fail
rather than claiming a retained backup; local recovery artifacts remain available.
Off-flag backup runs continue to write local files. The retained manifest includes
the chain tables in its schema fingerprints. Object-store backups copy ciphertext
and envelope metadata; keep the paired database backup for the envelopes.
Keep the platform backup KMS key decrypt-capable for the full retained backup
interval.

Object Lock retains ciphertext; it does not grant permission to decrypt it or
prevent cryptographic loss of access through key destruction. Bank-key enforcement,
rotation and retained-key recovery are governed by
[the bank encryption contract](bank_encryption.md#rotation-backups-and-recovery).

## Append-only inventory and sweep

`202610110095` complements the existing attestation, governance, ICAAP, market-data
and filing-chain migrations. The inventory distinguishes history from live state:

| Record family                                                                                                            | Database protection                                                                                                                                          |
| ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Tenant and operator audit logs                                                                                           | Existing UPDATE/DELETE guards; database hash chain; TRUNCATE guard                                                                                           |
| Signature and artifact versions, filing approvals/submission events, pinned stages/decisions and attachments/withdrawals | Existing UPDATE guards and signature DELETE guard; sweep closes other DELETE gaps and adds TRUNCATE guards                                                   |
| Scenario assumption and financial manual-edit histories                                                                  | Added UPDATE/DELETE/TRUNCATE guards                                                                                                                          |
| Eleven `param_*` board registers (risk weights, limits, thresholds, haircuts, shocks, ECL assumptions)                   | Values/scope/approval evidence sealed from insert; only effective-to and timestamp bookkeeping may update; DELETE/TRUNCATE refused                           |
| Approved/rejected forecast assumption generations                                                                        | All updates and deletion refused; drafts retain their editing lifecycle                                                                                      |
| Authorization bindings                                                                                                   | Grant/scope evidence sealed from insert; revocation fields write-once; revoked status cannot be reversed; DELETE/TRUNCATE refused                            |
| Completed regulatory runs and package financial snapshots                                                                | Filing/input seals above; added TRUNCATE guard                                                                                                               |
| Live scenario assumptions, current financial workspace, working artifact pointers                                        | Mutable operational state; immutable histories/version records retain their evidence                                                                         |
| Regulatory parameter control plane and system-of-record declarations                                                     | Existing governed-row seals; approved rows cannot be deleted; all TRUNCATE refused; draft, effective-date and revocation lifecycles preserved                |
| Reconciliation exceptions and canonical withdrawals                                                                      | Existing governed-row seals; all exceptions and applied/reversed withdrawals cannot be deleted; all TRUNCATE refused; pending withdrawal lifecycle preserved |

No application-set reset flag bypasses the new deletion guards. Destructive test
setup must use fresh disposable schemas or privileged fixture restoration, never
a production mutation capability. Every authority mutation continues to use the
existing transactional authorization invalidation service.

`tests/db/test_signed_filing_locks.py` verifies locked edits/deletes, completion,
void-and-rework, and linked resubmission. `tests/db/test_append_only_sweep.py`
exercises each board register plus assumption approvals, grant scope/revocation
and legacy history. `tests/db/test_retained_filing_evidence.py` tests UPDATE, DELETE
and cascading TRUNCATE against all eight retained filing-child tables.
`tests/storage/test_object_lock.py` uses Moto plus real local
bank-key encryption to verify retention metadata, refusal of version deletion,
idempotence, no shortening by the retention helper, and revocation refusal.
Moto's missing GetObjectRetention action is adapted to its real HEAD retention
headers; uploads, retention writes and version deletion guards remain emulator
operations. Vendor errors are translated to a safe storage-layer failure.
These are executable contract tests; actual AWS compliance immutability and pager
delivery require deployment acceptance evidence, not an emulator assertion.

## Historical local validation record (2026-10-10)

This records the original stack at `94643538`; it does not validate the restacked
branch. Current-parent validation remains required before shipping.

The exact `risk-service:format`, `risk-service:lint` and
`risk-service:typecheck` tasks passed. The strict type-check baseline added no
errors and removed three legacy Any errors. No financial figures or rules changed.

The hermetic suite passed **16,154 tests**, with **671 skips** and **one expected
failure**, using four workers. It preceded the final cloud-endpoint,
output-bucket and tenant-progress refinements. The final focused run below
exercised those refinements and passed **all 74 tests**, including complete
upgrade/downgrade of the migration chain. All PostgreSQL writes used disposable
schemas in this worktree's isolated local PostgreSQL service.

```bash
env -u TEST_DATABASE_URL -u REAL_DATA_DATABASE_URL \
  uv run pytest -n 4 --dist loadfile --tb=short -q

uv run python scripts/local_services.py run --postgres --role-admin -- \
  uv run pytest -n 4 --dist loadfile --tb=short -q \
  tests/db/test_postgres_migrations.py \
  tests/db/test_audit_integrity.py tests/db/test_signed_filing_locks.py \
  tests/db/test_append_only_sweep.py tests/db/test_retained_filing_evidence.py \
  tests/db/test_governance_append_only.py tests/core/test_audit_integrity_alerts.py \
  tests/services/test_audit_integrity_schedule.py tests/storage/test_object_lock.py \
  tests/db/test_regulatory_event_append_only.py
```

Production acceptance still needs counsel's retention period, an AWS locked-version
deletion/shortening refusal, and an SNS-to-pager receipt. Attach those deployment
records alongside this design and test evidence in the BoG pack (#356 / #401).

## Sweep integration validation (2026-10-11)

The sweep follows merged audit, filing and storage protections, with revision
`202610110095` following canonical bank-binding revision `202610110094` and
bank-ownership revision `202610110093`, which follows ICAAP revision
`202610110092`. Alembic reports one head.
The initial replay reproduced a two-head setup failure before guard execution;
linearizing this new revision resolves it without changing the ICAAP migration.
The signature DELETE guard remains owned by the filing-lock migration, while the
sweep adds its owner-level TRUNCATE guard.

Native PostgreSQL validation with four workers passed **81 migration, audit,
filing and sweep checks** in 218.02 seconds, including restricted migration
upgrade/downgrade and linked correction. A separate **66-case matrix** passed in
31.62 seconds: each of the 33 retained tables refuses cascading TRUNCATE both for
its actual owner and for the runtime role. Owner cases temporarily restore
TRUNCATE privileges, including cascade dependencies, inside rolled-back fixtures
to reach the statement trigger; runtime cases retain revoked privileges. These
checks prove both enforcement layers without adding a production bypass.

The disposable native cluster used `max_locks_per_transaction=1024`. A prior run
passed 80 checks but exhausted its default lock table during three schema
teardowns; the retry cleaned those known schemas and completed. This is test
capacity evidence, not a production configuration requirement. All managed
services stopped after validation. Exact Ruff lint, strict BasedPyright baseline
and changed-file Python/Markdown formatting passed without baseline growth.
This pre-pipeline evidence supplements the historical record above; final CI
and live pipeline evidence belong to the published sweep head.
