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

From `backend/`, download current objects into a distinct directory for each
backup generation:

```bash
uv run python -m scripts.backup_storage --out-dir <backup-directory> --download
```

Using recovery endpoint credentials, restore those objects into the recovery object store:

```bash
uv run python -m scripts.backup_storage --out-dir <backup-directory> --restore-manifest <manifest-path>
```

Use the `storage-inventory-<timestamp>.json` manifest reported by the download. Missing
buckets are recreated using the provisioning rules for dialect, versioning, temp
expiry and configured default SSE. Restore the matching database envelopes to read
current objects without a version pin. Inventory-only or older manifests are not
sufficient for encrypted-object recovery.

Backup catalogue requirements and the current-object-only recovery limitation are
owned by [rotation, backups and recovery](bank_encryption.md#rotation-backups-and-recovery).

## Connect, rotate and retire

To replace a key, use the operator-admin operation
`POST /operator/v1/tenants/{org_id}/banks/{bank_id}/encryption-key/rotate` during an
active tenant inspection. Supply the replacement exact ARN, the same bank account,
region and a reason. Existing provisioned banks can connect their key using `PUT`
on the same encryption-key resource.

Before rotating or retiring a key, follow the
[backup retention and recovery contract](bank_encryption.md#rotation-backups-and-recovery).

After the window expires, an operator admin in an active tenant inspection can call
`POST /operator/v1/tenants/{org_id}/banks/{bank_id}/encryption-key/retire` with the source
ARN, account, region and reason. The application authorizes retirement and records
an audit event; the bank then administers its key in AWS under the retention
contract above.

## Rollout: bank keys are optional until the management UI ships

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

For connected-key failures, existing-file migration and enforcement cutover, follow
[the bank encryption contract](bank_encryption.md#standing-contract) and its
[scope and follow-ups](bank_encryption.md#scope-and-executable-evidence).
