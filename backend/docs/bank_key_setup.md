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
`kms:DescribeKey`, `kms:GenerateDataKey`, `kms:Encrypt` and `kms:Decrypt` on the
exact bank key ARN. Cross-account use requires both policies; see
[AWS cross-account key permissions](https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-modifying-external-accounts.html).
No bank key material or AWS credentials are entered into aequorOS.

**Test connection** describes the proposed key, wraps and unwraps random synthetic
bytes with an authenticated bank/organization context, then compares the result.
A successful probe does not connect or replace a key. Connect and rotation repeat
their own custody probe. Disabled keys, incorrect ARNs/accounts/regions and
operation-specific permission failures produce actionable, sanitized messages.
Connected-key tests update the last checked state; failures preserve the last
successful-use timestamp. Successful provider use in committed registry
operations records that timestamp. Existing deployments have no historical
successful-use timestamp until a new successful operation occurs.

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
