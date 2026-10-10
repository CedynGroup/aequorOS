# Bank-held object encryption

## Standing contract

For banks with a connected key, every object written through `StorageClient` to
S3 or MinIO is an AWS Encryption SDK committed message, including raw uploads,
canonical artifacts, exports, filed packages and temporary objects. Reads
authenticate the complete message and its bank, tier, path and plaintext
checksum before releasing bytes. Unavailable connected keys, legacy plaintext
for a connected bank, and corrupt objects refuse access without a platform-key
fallback. Bank keys are optional during rollout: `BANK_KEY_REQUIRED=false`
preserves the existing platform storage path for tenants without keys in every
environment. Enable it once the key-management UI ships; then onboarding and
storage require a bank key. Missing bank keys alone never prevent startup. Local
evaluation uses the existing platform/MinIO path without an AWS KMS account. No
persistent local provider mode is introduced.

The bank creates and administers a customer-managed symmetric KMS key in its own
AWS account. AequorOS uses its workload role to request cryptographic
operations; it never creates, deletes, exports or administers that master key.
Connecting a key requires its exact ARN; aliases are rejected. Changing a
connected master key requires explicit rotation.

`app/core/key_management/types.py` owns the provider contract: describe,
generate a data key, wrap, unwrap and rotate. AWS calls belong in `aws.py`.
Storage and operator callers depend on this contract, not boto3 KMS operations.
Azure, Google, Vault and HSM adapters can implement the same conformance suite.

Each object has a random wrapping secret, itself protected in a small Encryption
SDK message using the bank's KMS keyring. The object SDK message uses that
secret with an SDK raw AES keyring; the SDK generates and wraps its payload data
key and owns all encryption formats and integrity checks. AequorOS persists only
the wrapped secret in `object_key_envelopes`; S3 metadata holds its UUID. This
extra indirection allows rotation without modifying large object ciphertext,
locked objects or historical versions. Plaintext secrets live only during one
operation.

There is no plaintext data-key cache across operations. Every bank-encrypted
read, including a previously issued application download link, unwraps through
the provider again. SDK caching would retain plaintext key material and extend
access after the bank removes its grant, so immediate refusal takes precedence
over the optional cache. See
[AWS Encryption SDK data-key caching](https://docs.aws.amazon.com/encryption-sdk/latest/developer-guide/data-key-caching.html).
Revocation blocks subsequent unwraps once KMS enforces the permission change; it
cannot recall plaintext already delivered or stop an operation that already
obtained its data key.

## Bank-side AWS setup

1. The bank creates an enabled `SYMMETRIC_DEFAULT`, `ENCRYPT_DECRYPT`,
   customer-managed key in its chosen region. Key administrators remain bank
   principals. Confirm the workload role ARN with AequorOS engineering.
2. Add the statements below to the bank key policy, replacing the workload role
   and bank ID. Keep the bank's administration and recovery policy statements.
   For a new institution, its platform bank ID is assigned by provisioning:
   initially authorize only the exact workload role on this exact key with the
   listed actions, omitting the context condition. After provisioning returns
   the ID, install the `bank_id` condition and remove any unconstrained grant
   before the bank begins ingestion. Verify an encrypted upload and download
   afterward.
3. AequorOS attaches the corresponding IAM permissions to its workload role,
   scoped to the full bank key ARN. Both accounts must authorize cross-account
   use; neither policy alone suffices. The bank can instead issue a scoped grant
   with an `EncryptionContextSubset` constraint on `bank_id`, while retaining
   permission for `DescribeKey`. See
   [AWS cross-account key access](https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-modifying-external-accounts.html).

Example key-policy statements (all identifiers are synthetic):

```json
[
  {
    "Sid": "AllowAequorOSBankCryptography",
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::444455556666:role/aequoros-bank-data" },
    "Action": ["kms:GenerateDataKey", "kms:Decrypt"],
    "Resource": "*",
    "Condition": {
      "StringEquals": { "kms:EncryptionContext:bank_id": "BK-SAMP0001" }
    }
  },
  {
    "Sid": "AllowAequorOSKeyDescription",
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::444455556666:role/aequoros-bank-data" },
    "Action": "kms:DescribeKey",
    "Resource": "*"
  }
]
```

In the workload IAM policy, omit `Principal`, set `Resource` to the exact bank
key ARN instead of `*`, and retain the same actions and context condition.
`DescribeKey` is separate because it has no encryption context; see
[AWS least-privilege permissions](https://docs.aws.amazon.com/kms/latest/developerguide/least-privilege.html).
No `CreateKey`, `PutKeyPolicy`, `CreateGrant`, `ScheduleKeyDeletion` or
key-export permission is required by the application. These SDK keyrings
generate data keys and decrypt their wrappers; wrapper rotation does not require
`ReEncrypt`.

KMS context contains the public bank ID, a context digest and an operation
purpose. It contains no customer filename or financial value. The bank can
inspect cross-account use in its CloudTrail logs and revoke its grant or remove
the role from the key policy. Ensure no other policy or grant still authorizes
that role.

## Onboarding and operator API

Apply all migrations through the current head (including `202610100088` and
`202610100089`), then set `ENCRYPTION_PLATFORM_AWS_ACCOUNT_ID` to the platform
workload account. Deployed environments require this setting when connecting or
using bank keys and reject keys in the platform account. No-key platform storage
does not require it. Use workload IAM credentials rather than bank access keys.
Set `STORAGE_DOWNLOAD_BASE_URL` to the public HTTPS tenant API origin; download
capabilities use `AUTH_JWT_SECRET` and expire within fifteen minutes.
`STORAGE_KMS_KEY_ID` may provide additional bucket SSE, including platform audit
logs, but does not replace the bank-held key.

Provisioning accepts an optional `encryption_key` object in
`TenantProvisionCreate`. It becomes required only when `BANK_KEY_REQUIRED=true`:

```json
{
  "provider": "aws_kms",
  "key_id": "arn:aws:kms:us-east-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab",
  "region": "us-east-1",
  "owner_account": "111122223333"
}
```

When a key is supplied, provisioning verifies ownership, key state and a live
wrap/unwrap round trip. Verification failure rolls back the attempted setup; it
does not fall back to platform storage. Omitted keys are accepted while
`BANK_KEY_REQUIRED=false`. `OPERATOR_AWS_KMS_ENABLED` is retired. AequorOS no
longer provisions its own per-bank KMS keys.

For an existing bank with provisioned storage, an operator administrator with an
active inspector session uses these routes on the separate operator API:

| Method | Route suffix                                       | Effect                                                                    |
| ------ | -------------------------------------------------- | ------------------------------------------------------------------------- |
| GET    | `/tenants/{org_id}/banks/{bank_id}/encryption-key` | Read custody reference and last checked status                            |
| PUT    | same                                               | Connect a bank key once; reject replacement without rotation              |
| POST   | same + `/rotate`                                   | Re-wrap all bank envelopes and atomically switch the key                  |
| POST   | same + `/retire`                                   | Authorize retirement only after current references and backup holds clear |

Rotation accepts the same key fields plus a meaningful `reason`. Routes verify
the bank belongs to the organization, require staff inspection authority and
write operator audit events in their transaction. The tenant API mounts none of
these routes. Status observations do not authorize access: storage contacts the
provider on every operation, and recovers after the bank restores permission.

## Rotation, backups and recovery

The bank grants access to its replacement key while retaining the old grant.
Rotation verifies the replacement can encrypt and decrypt, locks the bank key
record, re-wraps every envelope and commits all wrappers and the reference
together. Readers hold a shared bank-key lock through unwrap; rotation takes an
exclusive lock. Readers and uploads arriving during rotation wait and use the
replacement after commit. Existing operations complete before rotation takes its
lock. Failed rotation rolls back the transaction. S3 bytes and version IDs do
not change. KMS automatic material rotation within the same key ARN needs no
application re-wrap.

Keep the old key decrypt-capable until every backup with its old wrappers has
aged out. Rotation records a per-bank retention hold; an unset
`ENCRYPTION_BACKUP_RETENTION_DAYS` keeps that hold indefinitely. Reusing a key
extends rather than shortens its hold, and a sibling bank still using it remains
available. Retirement refuses while any bank currently references the key or a
backup hold remains. The bank administers the actual grant and key state; the
application neither disables nor schedules deletion of the bank key. Exercise an
encrypted read and a recovery drill before removing old access.

`scripts/backup_storage.py --download` copies SDK ciphertext through raw S3 GET;
it never uses the application's decrypted read path. Its manifest records the
original user metadata and content type, including `key-envelope-id`, the
encryption format, and plaintext and ciphertext checksums. The backup preserves
existing platform-only objects during optional rollout as well as bank-encrypted
objects; it does not unwrap either. The script copies current objects.
Version-pinned backup and recovery remain a known pre-existing gap tracked in
[issue 501](https://github.com/CedynGroup/aequorOS/issues/501); do not claim
that these backups recover historical versions.

A recoverable object backup needs ciphertext, original bucket/key/metadata and
the matching `bank_encryption_keys` and `object_key_envelopes` catalogue from
the database backup. Use the restore command documented in
[bank key setup](bank_key_setup.md) to recreate missing buckets and restore
metadata unchanged, then read through `StorageClient`. Restore verifies
ciphertext checksums before uploading. Without the bank key, even restored
ciphertext and wrapped keys cannot be read. A copied object can use a newly
rotated live catalogue because its envelope UUID is stable. An old database
snapshot, however, contains old wrappers: restore and re-wrap that catalogue
under bank-approved access before retiring its old key, or retain the old key
for the snapshot's recovery lifetime. Physical historical database backups are
immutable and are not rewritten by live rotation.

The legacy organization/case document routes retain their previous behavior;
**document transfer under bank keys** remains a follow-up.

Existing platform-only/plaintext objects need an explicitly planned migration
before connecting a bank key or enabling mandatory enforcement; the new reader
refuses them. Do not turn the legacy reader back on to complete onboarding.
Exported plaintext that a user already downloaded is outside object-storage
revocation.

## Scope and executable evidence

The shared database and its storage encryption remain on the platform key. This
change protects object artifacts and their object backups, not sensitive columns
in physical database backups. Field-level encryption is
[issue 353](https://github.com/CedynGroup/aequorOS/issues/353); its provider
seam is `KeyProvider`. Signing keys and credential custody remain
[issue 355](https://github.com/CedynGroup/aequorOS/issues/355). Scheduled
key-health monitoring, alerting and support workflows remain
[issue 354](https://github.com/CedynGroup/aequorOS/issues/354); this change
supplies the provider `describe` contract, onboarding probe results and checked
timestamps. No standalone health-probe endpoint is exposed. These follow-ups are
required before claiming sensitive database backup protection or regulatory
approval of the complete service.

`LocalKeyProvider` supports injected hermetic tests and the provider conformance
suite. Local evaluation without an AWS KMS account uses the existing platform
storage path described in [bank key setup](bank_key_setup.md). There is no
persistent per-tenant local provider and no fallback for an unavailable
connected bank-owned key.

Evidence lives in `tests/storage/test_key_providers.py` (local and moto AWS KMS
conformance, grant denial and outage), `test_bank_encryption.py` (all S3 tiers,
retained versions, substitution, revocation, downloads and backup recovery),
`test_key_rotation_concurrency.py` (PostgreSQL readers and concurrent writes),
`test_bank_key_rollout.py` (optional platform storage, required-key refusal and
connected-key failures in every environment),
`tests/operator/test_bank_encryption.py` (staff and bank scoping), and
`tests/db/test_tenant_rls_completeness.py` (migrated envelope RLS). CI uses fake
providers and moto, never real AWS KMS calls.
