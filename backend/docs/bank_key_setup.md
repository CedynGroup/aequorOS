# Bank key setup

Bank onboarding requires a bank-owned AWS KMS key. The operator console collects
its exact key ARN, bank AWS account ID and region, reviews those values, and sends
`encryption_key` in `POST /operator/v1/tenants`:

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
The bank account must differ from `ENCRYPTION_PLATFORM_AWS_ACCOUNT_ID` in deployed
environments. The workload uses its AWS role credentials; the bank must authorize
that role for key description, data-key generation, encryption, decryption and
re-encryption. Onboarding verifies key ownership, enabled state and a wrap/unwrap
probe; refusal rolls back tenant setup and removes buckets created by the attempt.
Transport requirements are governed by [the transport contract](transport_security.md).

Alias ARNs (`arn:…:alias/…`), alias names and bare key IDs are rejected with
“An exact AWS KMS key ARN is required; alias ARNs are not accepted.” An alias can
be repointed, so it cannot provide the fixed identity required for decryption
and audit evidence. Only `aws_kms` is accepted in runtime configuration; local
providers are injected test fixtures, never an AWS failure fallback.

Every bank object in raw, canonical, outputs and temp S3 tiers is an AWS Encryption
SDK message. Each object has an independent wrapping key held only as a bank-KMS
wrapped envelope in the database. S3 metadata holds the envelope UUID and format;
plaintext keys are never persisted or cached across operations. Optional bucket
SSE remains additional platform protection. Reads authenticate the entire SDK
message and checksum before returning plaintext. Legacy plaintext files are refused
and require an explicit migration before use.

Revoking or disabling the bank key refuses reads and writes, including duplicate
writes and application download-link redemption. Downloads pass through
`/api/v1/storage/download`; direct S3 uploads are refused because they would bypass
application encryption. Configure `STORAGE_DOWNLOAD_BASE_URL` to the HTTPS tenant
API origin in deployments. Backup copies must preserve ciphertext, object metadata
and the corresponding database envelopes.

To replace a key, use the operator-admin operation
`POST /operator/v1/tenants/{org_id}/banks/{bank_id}/encryption-key/rotate` during an
active tenant inspection. Supply the replacement exact ARN, the same bank account,
region and a reason. Rotation verifies the replacement key and atomically rewraps
all bank object envelopes while updating the connected key. Ciphertext and historic
object versions stay unchanged; retire the source key only after successful commit.
Failure leaves the source reference and envelopes intact. Existing provisioned
banks can connect their key using `PUT` on the same encryption-key resource.

Bank key health monitoring is follow-up work; there is no separate `/check` endpoint.
