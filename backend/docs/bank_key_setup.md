# Bank key setup

Bank-held custody is optional during rollout (`BANK_KEY_REQUIRED=false` by
default). When connecting a key, the operator console collects its exact key ARN,
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

The key must be a customer-managed symmetric encryption key, enabled for
`ENCRYPT_DECRYPT`. Its ARN account and region must match the declared values.
Configure `ENCRYPTION_PLATFORM_AWS_ACCOUNT_ID` in deployed environments; the bank
account must differ from it. The workload uses its AWS role credentials; the bank must authorize
that role for key description, data-key generation, encryption and decryption.
Onboarding verifies key ownership, enabled state and a wrap/unwrap
probe; refusal rolls back tenant setup and removes buckets created by the attempt.
Transport requirements are governed by [the transport contract](transport_security.md).

Alias ARNs (`arn:…:alias/…`), alias names and bare key IDs are rejected with
“An exact AWS KMS key ARN is required; alias ARNs are not accepted.” An alias can
be repointed, so it cannot provide the fixed identity required for decryption
and audit evidence. Only `aws_kms` is accepted in runtime configuration; local
providers are injected test fixtures, never an AWS failure fallback.

For banks with a connected key, every object in raw, canonical, outputs and temp
S3 tiers is an AWS Encryption
SDK message. Each object has an independent wrapping key held only as a bank-KMS
wrapped envelope in the database. S3 metadata holds the envelope UUID and format;
plaintext keys are never persisted or cached across operations. Optional bucket
SSE remains additional platform protection. Reads authenticate the entire SDK
message and checksum before returning plaintext. Legacy plaintext files are refused
and require an explicit migration before use.

Revoking or disabling the bank key refuses reads and writes, including duplicate
writes and application download-link redemption. Downloads pass through
`/api/v1/storage/download`. Link issuance uses object metadata to pin the current
version; decryption, authentication and key-access checks happen at redemption.
A download link is a capability credential: redemption needs no bearer session,
and the link must be kept private.
Direct uploads through the bank storage interface are
refused because they would bypass application encryption. Legacy organization/case
document transfer retains its existing signed S3 URLs; document transfer under bank
keys is follow-up work. Configure `STORAGE_DOWNLOAD_BASE_URL` to the HTTPS tenant
API origin in deployments. Backup copies must preserve ciphertext, object metadata
and the corresponding database envelopes. From `backend/`, download current objects
into a distinct directory for each backup generation:

```bash
uv run python -m scripts.backup_storage --out-dir <backup-directory> --download
```

The version-2 manifest records all object metadata (including envelope UUID, format
and plaintext checksum), content type and ciphertext SHA-256. Using recovery endpoint
credentials, restore those objects into the recovery object store:

```bash
uv run python -m scripts.backup_storage --out-dir <backup-directory> --restore-manifest <manifest-path>
```

Use the `storage-inventory-<timestamp>.json` manifest reported by the download. Missing
buckets are recreated using the provisioning rules for dialect, versioning, temp
expiry and configured default SSE. Restore the matching database envelopes to read
current objects without a version pin. Inventory-only or older manifests are not
sufficient for encrypted-object recovery.

Known limitation: this backup inventories current objects only. Restore assigns new
S3 version IDs and does not recover archived versions or reconcile database fields
that pin the original version. Version-pinned ICAAP attachments and regulatory
artifacts therefore are not recovered by this procedure. Version-addressed backup
and restore is deferred to [issue #501](https://github.com/CedynGroup/aequorOS/issues/501).

To replace a key, use the operator-admin operation
`POST /operator/v1/tenants/{org_id}/banks/{bank_id}/encryption-key/rotate` during an
active tenant inspection. Supply the replacement exact ARN, the same bank account,
region and a reason. Rotation verifies the replacement key and atomically rewraps
all bank object envelopes while updating the connected key. Ciphertext and historic
object versions stay unchanged. Readers hold a shared registry-row lock through
wrapper selection and unwrap; rotation and writers take the exclusive lock.
After commit the rotated bank uses the replacement key for new encryption. Other
banks still connected to the source key continue reading and writing with it. Each
bank retains its own source-key backup hold; reusing a key and rotating away again
extends that bank's hold without shortening prior retention. The source key remains
KMS-enabled and decrypt-capable for envelopes in retained database backups.
Failure leaves the source reference and envelopes intact. Existing provisioned
banks can connect their key using `PUT` on the same encryption-key resource.

Set `ENCRYPTION_BACKUP_RETENTION_DAYS` to at least the longest retention window of
any database or object backup before rotation. Rotation persists a source-key hold
through that window. Without a configured window, the hold is indefinite. Retirement
checks both the originally recorded window and the current setting; lowering the
current setting cannot shorten the originally recorded hold. Every backup taken before the last rotation away from a shared source key must
age out before disabling it. Keep the source key enabled and its decrypt grant intact;
the bank must not disable, revoke or delete it while those backups are retained.

After the window expires, an operator admin in an active tenant inspection can call
`POST /operator/v1/tenants/{org_id}/banks/{bank_id}/encryption-key/retire` with the source
ARN, account, region and reason. This refuses current keys, keys outside the scoped
bank's history, and keys with any unexpired or indefinite hold, including other banks
that shared the source key. It authorizes retirement only after those checks and
records an audit event.
The bank then disables the key using its own AWS-account credentials. The workload
cannot perform cross-account `DisableKey` ([AWS API contract](https://docs.aws.amazon.com/kms/latest/APIReference/API_DisableKey.html));
no key-management operation here schedules deletion. The application cannot prevent
the owning bank from changing AWS policy directly, so the bank must apply the same retention rule to
its own AWS key administration and any longer-lived recovery copies.

Bank key health monitoring is follow-up work; there is no separate `/check` endpoint.

## Rollout: bank keys are optional until the management UI ships

`BANK_KEY_REQUIRED` defaults to `false` in every environment. Enable it once
the key-management UI ships. With the flag off, onboarding may omit
`encryption_key`; tenants without a key retain the existing platform-key/S3
storage path, including direct signed uploads and downloads. Missing bank
keys alone do not prevent startup or storage access. Local evaluation uses
that same path and needs no AWS KMS account; no tenant-local provider mode or
persistent fake master key is introduced. Configure local S3/MinIO as before.

The onboarding form reads the requirement from `/operator/health`. Key fields
stay optional while configuration is pending or unavailable. Failed requests
retry with bounded backoff; refocusing the window or coming back online restarts
retries if configuration is still unresolved. A confirmed requirement blocks
Review and submission until the key fields are complete. Entering any key field
also requires completing the whole key configuration, even during optional
rollout. The provisioning API remains authoritative: when enforcement is enabled,
a keyless submission fails the `kms` step, and the console displays that refusal
in the provisioning result.

A connected bank key always enables SDK envelope encryption for that bank's
objects, exports, filings and object backups. Revocation or an outage refuses
access even when `BANK_KEY_REQUIRED=false`; it never falls back to the
platform path. Encrypted objects also refuse reads if their bank-key record
is missing. Enabling the flag refuses onboarding and storage operations for
all tenants without a verified bank-owned key. Existing platform-only files
need a migration before connecting a key or enabling enforcement.

This stack supplies optional custody scaffolding. Mandatory bank-held custody
and full regulatory approval are deferred until the management UI and the
related database-field work are complete.
