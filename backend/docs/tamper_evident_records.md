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

## Filing and input seals

`202610100091` forbids financial-content updates on every generated package:
identity, version, source-run references, input snapshot and its hash. Application
roles lose table UPDATE and receive only the lifecycle-column grants (including
`id` for PostgreSQL foreign-key key-share locking; changing it is trigger-blocked).
Signed packages cannot be deleted; their content digest is sealed. Existing
signature evidence keeps the deletion seal active after a void. Lifecycle status,
review bookkeeping and regulator outcomes continue to advance. A correction
uses existing generation/resubmission authorization and `supersedes_id`, retaining
the original version and figures. Voiding withdraws certification; it never opens
an in-place financial-content edit. Completed regulatory runs, including their
inputs and metrics, cannot be updated or deleted. Queued/running work can finish.

Do not grant blanket UPDATE/DELETE after migration. `bootstrap_db.sh`'s legacy
blanket grants must not be used as a production reprovisioning procedure; triggers
still protect records but the intended permission layer requires migration grants.
Use a separate migration owner and unprivileged application role. The worker's
BYPASSRLS privilege does not grant permission to alter evidence.

## Write-once objects and backups

Set `STORAGE_OBJECT_LOCK_ENABLED=true` for the AWS deployment. Local MinIO and
hermetic fixtures retain their existing behavior with this flag off. Provisioning
creates lock-capable output buckets and refuses an existing output bucket without Object
Lock. Enable Object Lock/versioning on existing cloud buckets through the
infrastructure owner before switching on the flag. Every output object (including
filed PDF/XLSX, archived signed revisions and attachments) is written with
`COMPLIANCE`, a retain-until date, and a transport checksum. All output versions
are retained to avoid a path-based classification missing a filing format.
Idempotent writes verify/apply retention to the existing concrete version;
retention can be extended but is never shortened. Object version IDs remain the
reference of record. Retained presigned uploads are refused so they cannot bypass
application retention checks. A delete marker hides the current key but does not remove a
locked version. Apply an IAM deny on DeleteObject for evidence buckets if hiding
current keys must also be prevented.

`STORAGE_OBJECT_LOCK_RETENTION_DAYS` defaults to **2555 days as an operational
placeholder, confirm with counsel, #246**. This is not a statement of BoG law or
legal sign-off. Counsel and the infrastructure owner must approve the production
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

Bank-held envelope encryption is applied before retention, unchanged. Object Lock
retains ciphertext; it does not grant permission to decrypt it or bypass a revoked
bank key. Existing reads still check the bank key and refuse revoked/unavailable
keys. Keep retired KMS keys and envelope registries for at least the longest
retained ciphertext/backup interval, through the existing retained-key recovery
contract. Destruction of a key is a separate cryptographic loss of access that
Object Lock cannot prevent. Rotation creates new encrypted versions; old retained
versions stay recoverable only with their original bank keys. Never shorten key
recovery windows below the Object Lock interval.
