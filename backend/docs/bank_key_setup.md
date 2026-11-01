# Bank key setup

Follow [the rollout contract](bank_encryption.md#standing-contract) before
connecting a key. The operator console collects its exact key ARN,
bank AWS account ID and region, reviews those values, and sends `encryption_key`
in `POST /operator/v1/tenants`:

```json
{
  "encryption_key": {
    "provider": "aws_kms",
    "key_id": "arn:aws:kms:us-east-1:123456789012:key/example-key-id",
    "owner_account": "123456789012",
    "region": "us-east-1"
  }
}
```

Follow [bank-side AWS setup](bank_encryption.md#bank-side-aws-setup) for the
key policy and workload permissions, and
[the onboarding contract](bank_encryption.md#onboarding-and-operator-api) for
migration and deployment settings, exact-ARN validation and the custody probe.
Transport requirements are governed by [the transport contract](transport_security.md).

## Downloads and object recovery

Download credentials and encrypted transfer rules are governed by
[the download contract](bank_encryption.md#downloads). Keep capability links private.

From `backend/`, download every retained object revision into a distinct directory for each
backup generation:

```bash
uv run python -m scripts.backup_storage --out-dir <backup-directory> --download
```

Using recovery endpoint credentials, restore those objects into the recovery object store:

```bash
uv run python -m scripts.backup_storage --out-dir <backup-directory> --restore-manifest <manifest-path>
```

Use the format-3 `storage-inventory-<timestamp>.json` manifest reported by the
download. Versioned buckets retain superseded revisions; inventory-only or older
manifests cannot recover pinned documents. Missing buckets are recreated using the
provisioning rules for dialect, versioning, temp expiry and configured default SSE.
Keep the matching database envelopes and all recorded bank keys available.

### Reconcile pinned versions after replay

Restore replay emits `storage-restore-<timestamp>.json`, mapping each original
`(bucket, key, version ID)` to the new version ID minted by the recovery store.
Have the administrator rerun `scripts/database_roles_202610120096.sql` to prepare
the two unprivileged NOLOGIN recovery roles and the deployment owner's internal
owner membership before applying migration `202611010104` as `aqs_migrator` using
`SET ROLE aqs_owner`. Keep application writers
stopped, and retain the mapping with the incident evidence. This is an offline
maintenance operation for a dedicated restore target, not an application endpoint.

If the database backup already contains migration `202611010104`, applying it
again does nothing. The standard database backup/restore excludes owners and ACLs,
so after restoring the database, run this additional preparation once as the same
administrator identity that performed the restore. Connect to the dedicated
restore database using protected `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` and
`PGPASSWORD` environment variables, with the restored application schema first in
the connection's search path:

```bash
psql --no-psqlrc --set ON_ERROR_STOP=1 --file scripts/prepare_storage_recovery.sql
```

The administrator must already be able to assume the preprovisioned audit and
recovery owner roles. This atomic, repeatable step restores ownership and ACLs of
only the recovery permit, guard helper and reconciler, plus the schema access and
table grants installed by migration `202611010104`. It refuses missing objects,
unsafe role attributes and objects owned by anyone other than that administrator
or their intended owner. It does not transfer ownership of other tables or
functions, change policies or guards, create roles, or grant role memberships,
SUPERUSER or BYPASSRLS. Retain the preparation result with the incident evidence
before running reconciliation.

The DBA must issue an incident-only login with cross-tenant authority (SUPERUSER
or BYPASSRLS) and explicitly grant `aqs_storage_recovery` to that login for the
recovery window. Neither NOLOGIN recovery role has BYPASSRLS.
Never grant this capability or the internal `aqs_storage_recovery_owner` identity
to an application, worker, or operator runtime login. The internal owner is for
migration-owned functions; the incident login receives only the public recovery
capability. Revoke the incident membership and retire its credentials afterwards.
The operation requires direct membership recorded in `pg_auth_members`, even for
a superuser, and checks the actual connection identity (`session_user`) before
reading or changing any pinned reference. `SET ROLE` and caller-controlled settings
cannot establish a recovery session.

Set `RESTORE_DATABASE_URL` through a protected environment or `--env-file`, keeping
credentials off command lines and out of shell history. Keep the primary
`DATABASE_URL`, `WORKER_DATABASE_URL`, and `OPERATOR_DATABASE_URL` populated so the
CLI can refuse a target that addresses one of those databases.

```bash
uv run python -m scripts.reconcile_storage_versions \
  --mapping <backup-directory>/storage-restore-<timestamp>.json \
  --storage-env <restored-bucket-environment> \
  --reason <incident-or-change-reference>
```

Use the environment embedded in the restored bucket names; replay preserves
those names even when the recovery endpoint differs from the original.

Optional `--organization-id OR-…` and `--bank-id BK-…` narrow the recovery scope.
Every non-null pinned reference in that scope must have exactly one mapping by
bucket, key and old version. Missing or duplicate identities, collapsed restored
versions, malformed IDs/checksums and a zero-match scope refuse the entire
transaction. An already-applied mapping also refuses rather than guessing that a
missing old ID means success. For a retry, retain the original restored database
snapshot or explicitly inventory the current pins and investigate the previous
committed run first; do not edit the replay report to force a match.
Unrelated unversioned objects, including upload temp objects, may have empty
version IDs in the full replay report. The replay CLI writes that report but exits
with status 2 if any restored object has no version ID, including these temp
objects. Inspect the warning and report: every selected pin still requires a
usable old-to-new version mapping before reconciliation can succeed.

On refusal, the reconciler exits with status 1 and writes the fixed precondition
reason or PostgreSQL's primary diagnostic to stderr. For example, a missing or
ambiguous pinned mapping requires checking the replay report and selected scope;
an unproved session requires checking the incident login's authority. Driver
details, hints, context, SQL and parameters are omitted. Invalid input (`ValueError`),
file access failures (`OSError`) and database errors without a PostgreSQL primary
diagnostic receive only the generic refusal. Protected-target refusals omit the
host, port and database name. Keep writers stopped and investigate the refusal
before retrying; a failed transaction does not commit reference or audit changes.

Only `regulatory_artifact_versions.storage_version_id`,
`regulatory_package_attachments.storage_version_id`,
`icaap_attachments.storage_version_id`, and
`bank_supervisory_addons.letter_storage_version_id` can move. The existing guards
require the function identity and a private transaction-bound, exact-row permit;
ordinary UPDATE, DELETE, TRUNCATE and all other sealed fields retain their guards.
Reference updates and `audit_events(event_type='storage.version_reconciled')`
commit together in the existing hash chain. Each event records the connection
identity, incident reason, run ID, bank, table, row, bucket/key, before/after IDs,
replay ciphertext checksum, and SHA-256 of the report's PostgreSQL JSONB text.
No document, signature, content digest, timestamp, envelope or bank key is rewritten.

After committing, exercise version-pinned downloads through the application and
verify the archived bytes and signature bindings. Reconciliation does not unwrap
keys and cannot override revoked or unavailable bank keys. The synthetic migrated
PostgreSQL regression in `tests/db/test_storage_version_recovery.py` covers three
banks in two organizations, historical revisions, encrypted-store loss and replay,
old-key reads, key refusals, bank/tenant isolation, audit-chain integrity and
unauthorized writes. Whole-service orchestration and backup cadence remain separate.

Backup catalogue and retained-key requirements are owned by
[rotation, backups and recovery](bank_encryption.md#rotation-backups-and-recovery).

## Connect, rotate and retire

To replace a key, use the operator-admin operation
`POST /operator/v1/tenants/{org_id}/banks/{bank_id}/encryption-key/rotate` during an
active tenant inspection. Supply the replacement exact ARN, the same bank account,
region and a reason. Existing provisioned banks can connect their key using `PUT`
on the same encryption-key resource.

Before rotating or retiring a key, follow the
[backup retention and recovery contract](bank_encryption.md#rotation-backups-and-recovery).

Once the retention contract permits retirement, an operator admin in an active
tenant inspection can call
`POST /operator/v1/tenants/{org_id}/banks/{bank_id}/encryption-key/retire` with the source
ARN, account, region and reason. The application authorizes retirement and records
an audit event; the bank then administers its key in AWS under the retention
contract above.

## Optional-key rollout

Local evaluation and enforcement defaults are governed by
[the standing contract](bank_encryption.md#standing-contract). Configure local
S3/MinIO as before; evaluating without a bank key needs no AWS KMS account.

The onboarding form reads the requirement from `/operator/health`. Key fields
stay optional while configuration is pending or unavailable. Failed requests
retry with bounded backoff; refocusing the window or coming back online restarts
retries if configuration is still unresolved. A confirmed requirement blocks
Review and submission until the key fields are complete. Entering any key field
also requires completing the whole key configuration, even during optional
rollout. The provisioning API remains authoritative: when enforcement is enabled,
a keyless submission fails the `kms` step, and the console displays that refusal
in the provisioning result.

For connected-key failures and optional-key enforcement settings, follow
[the bank encryption contract](bank_encryption.md#standing-contract) and its
[scope and follow-ups](bank_encryption.md#scope-and-executable-evidence).

## Bank administrator self-service

Bank **Settings → Bank encryption key** is visible only with a complete,
organization-wide Account/restricted administration binding. The API enforces
that binding for status, tests, connect, replacement rotation and schedule
confirmation; ordinary bank users and machine credentials cannot use these
controls. Every bank lookup is organization- and bank-scoped.

Configure `ENCRYPTION_WORKLOAD_ROLE_ARN` with the deployed workload's exact AWS
role ARN before onboarding. The guided flow renders a copyable statement for
that principal, to add to the bank key's existing policy without removing its
administrator statements. The workload's IAM policy must separately authorize
the permissions in [bank-side AWS setup](bank_encryption.md#bank-side-aws-setup)
on the exact bank key ARN. Cross-account use requires both policies.
No bank key material or AWS credentials are entered into aequorOS.

**Test connection** describes the proposed key, wraps and unwraps random synthetic
bytes with an authenticated bank/organization context, then compares the result.
A successful probe does not connect or replace a key. Connect and rotation repeat
their own custody probe. Disabled keys, incorrect ARNs/accounts/regions and
operation-specific permission failures produce actionable, sanitized messages.
Connected-key tests update the last checked state only if the tested key's full
identity still matches the connected key when the observation is written; a test
racing replacement rotation cannot overwrite the replacement's health. Failures
preserve the last successful-use timestamp. Ordinary field and object use records
version telemetry best-effort in a separate short background transaction when the
caller transaction ends, throttled to five minutes and skipping locked versions.
It never updates the
bank key row used by rotation. Status includes the latest recorded version use.
Existing deployments have no historical successful-use timestamp until a new
successful operation occurs.

The status panel shows connection, the immutable aequorOS data-key version,
recorded successful use, and replacement-key rotation time. That version is
separate from AWS's internal backing-key material. Manual rotation changes the
wrapping key and current data-key version, retains the previous references, and
records actor, source/destination and reason in the audit trail. Existing data
and backups continue to require their original retained key versions.

### Optional AWS automatic rotation

The optional schedule is executed by the bank's AWS administrator. Settings
provides an exact `aws kms enable-key-rotation` command with the connected key
ARN, region and a 90–2560 day interval. The bank principal needs
`kms:EnableKeyRotation` on the key in its own account; granting that action to the
cross-account workload cannot enable it. AWS forbids cross-account use of this
operation ([AWS API contract](https://docs.aws.amazon.com/kms/latest/APIReference/API_EnableKeyRotation.html)).

After running the command successfully, the administrator can record a
confirmation. This stores the reported interval and confirmation time with an
audit event explicitly marked `bank_administrator_report`. The panel labels it
bank-reported and not independently verified. This does not start an aequorOS
scheduler or claim to observe AWS key-material rotation. Replacement-key rotation
clears the previous key's schedule confirmation; confirming a stale key is refused.
