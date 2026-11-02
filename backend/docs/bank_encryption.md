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
environment. Set `BANK_KEY_REQUIRED=true` at the deployment's enforcement cutover;
then onboarding, covered field access and storage require a bank key. Encrypted
objects also refuse reads when their bank-key record is missing. Missing bank
keys alone never prevent startup. Local evaluation uses the existing platform/MinIO
path without an AWS KMS account. The synthetic revocation walkthrough below
provides a separate, environment-restricted demo provider.

The bank creates and administers a customer-managed symmetric KMS key in its own
AWS account. AequorOS uses its workload role to request cryptographic
operations; it never creates, deletes, exports or administers that master key.
Connecting a key requires its exact ARN; aliases are rejected because they can
be repointed and cannot pin decryption or audit identity. Changing a connected
master key requires explicit rotation.

`app/core/key_management/types.py` owns the provider contract: describe,
generate a data key, wrap, unwrap and rotate. AWS calls belong in `aws.py`.
Storage and operator callers depend on this contract, not boto3 KMS operations.
Runtime configuration currently accepts only `aws_kms`. Azure, Google, Vault
and HSM adapters can implement the same conformance suite.

A bank data-key version is a random secret wrapped in a small Encryption SDK
message using the bank's KMS keyring. Each object's authenticated context derives
a distinct wrapping secret from that version; the object SDK message uses it
with a raw AES keyring. The SDK generates and wraps the actual payload key and
owns the encryption format and integrity checks. Only the wrapped version and
its exact KMS reference are persisted in `object_key_envelopes`; S3 metadata
holds the version UUID.

Field and object operations share the
[unit-of-work key lifetime and revocation boundary](bank_field_encryption.md#keys-and-storage).

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
    "Action": ["kms:GenerateDataKey", "kms:Encrypt", "kms:Decrypt"],
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
require data-key generation, encryption and decryption permissions for connection
probes and custody operations; wrapper rotation does not require `ReEncrypt`.

KMS context contains the public bank ID, a context digest and an operation
purpose. It contains no customer filename or financial value. The bank can
inspect cross-account use in its CloudTrail logs and revoke its grant or remove
the role from the key policy. Ensure no other policy or grant still authorizes
that role.

## Onboarding and operator API

Before upgrading a deployment with connected banks, configure
`ENCRYPTION_PLATFORM_AWS_ACCOUNT_ID` with the platform workload account and ensure
that the migration process has workload IAM credentials and network access to
every connected bank's KMS key. Those keys must be active and permit the
connection probes and custody operations described above, including unwraps of
retained index and field envelopes. Deployed environments require the account
setting when connecting or using bank keys and reject keys in the platform
account. No-key platform storage does not require it. Use workload IAM
credentials rather than bank access keys.

Apply all migrations through the current head (including `202610100088` and
`202610100089`). For banks connected before data-key versions, revision
`202610220102` copies each legacy envelope's wrapping reference from its scoped
bank registry, checks the active KMS key, generates and probes a current data-key
version, and reindexes every identity generation under the bank's index key. It
preserves an existing index envelope or uses the new current version when no
index envelope exists, including for empty connected banks. The version/index
pointer changes and keyed reindexing commit atomically; a provider refusal or
failed probe rolls back the upgrade. A later connection test is not required to
initialize these versions.

The upgrade does not rewrite protected payloads. Legacy objects retain their
original context and payload key, and rotation preserves the index secret.
Version initialization is idempotent. Downgrade refuses populated envelope
catalogues rather than discarding the metadata needed to read encrypted data.

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
wrap/unwrap round trip. Verification failure rolls back the attempted setup and
attempts to remove buckets created by the saga; cleanup failures are reported in
its result. It does not fall back to platform storage. Omitted keys are accepted
while
`BANK_KEY_REQUIRED=false`. `OPERATOR_AWS_KMS_ENABLED` is retired. AequorOS no
longer provisions its own per-bank KMS keys.

For an existing bank with provisioned storage, an operator administrator with an
active inspector session uses these routes on the separate operator API:

| Method | Route suffix                                       | Effect                                                                    |
| ------ | -------------------------------------------------- | ------------------------------------------------------------------------- |
| GET    | `/tenants/{org_id}/banks/{bank_id}/encryption-key` | Read custody reference and last checked status                            |
| PUT    | same                                               | Connect a bank key once; reject replacement without rotation              |
| POST   | same + `/rotate`                                   | Publish a new data-key version; see rotation below                        |
| POST   | same + `/retire`                                   | Authorize retirement only after current references and backup holds clear |

Rotation accepts the same key fields plus a meaningful `reason` and must retain
the bank's ownership account. Changing providers requires an explicit migration.
Routes verify the bank belongs to the organization, require staff inspection
authority and write operator audit events in their transaction. Bank administrators
use separate Account-administration endpoints and the guided Settings flow in
[bank key setup](bank_key_setup.md#bank-administrator-self-service); retirement
remains a staff operation. Status observations do not authorize access; provider
checks and key reuse follow the
[unit-of-work boundary](bank_field_encryption.md#keys-and-storage).

## Downloads

Bank-encrypted downloads pass through `/api/v1/storage/download`. Link issuance
uses object metadata to pin the current version; decryption, authentication and
key-access checks happen at redemption. A download link is a capability
credential: redemption needs no bearer session, and the link must be kept
private. Revoking or disabling the connected key refuses reads and writes,
including duplicate writes and download-link redemption. Direct signed uploads
through the bank storage interface are refused because they bypass application
encryption. Banks without keys retain direct signed transfers during optional
rollout. Legacy organization/case document transfer retains its existing signed
S3 URLs; document transfer under bank keys remains follow-up work.

## Rotation, backups and recovery

The bank grants access to its replacement key while retaining the old grant.
Rotation verifies the replacement and publishes a new immutable data-key
version in the existing envelope catalogue. Version reads and protected writes
acquire a bank-scoped shared transaction barrier; connection and rotation take
the exclusive barrier. Rotation waits for admitted transactions to finish, while
later work waits for the new version. Writers refresh bank-key and current-version
state after admission, including during optional rollout. Ciphertext records its
version UUID, and old versions keep their exact KMS
references. Failed rotation rolls back the new pointer/version. Object writes
reference committed versions, so business rollback cannot orphan published
ciphertext. See [the field design](bank_field_encryption.md#keys-and-storage)
for bounded key-cache lifetime and versioned encryption.

Keep the old key decrypt-capable while retained data or any backup requires its
versions, including [write-once database backups](tamper_evident_records.md#write-once-objects-and-backups).
Set `ENCRYPTION_BACKUP_RETENTION_DAYS` to at least the longest database
or object backup retention window before rotation. Rotation records a per-bank
retention hold; an unset setting keeps that hold indefinitely. Reusing a key
extends rather than shortens its hold, and a sibling bank still using it remains
available. Retirement refuses while any bank or retained envelope references the key or a
backup hold remains, including holds for other banks sharing the key. It checks
both the recorded deadline and the current retention setting; lowering the
setting cannot shorten the recorded hold. The bank administers the actual grant
and key state; the application neither disables nor schedules deletion of the
bank key. Cross-account `DisableKey` is unsupported by the
[AWS API](https://docs.aws.amazon.com/kms/latest/APIReference/API_DisableKey.html),
and the bank must apply these retention rules to its own AWS administration and
any longer-lived recovery copies. Exercise an encrypted read and a recovery
drill before removing old access.

`scripts/backup_storage.py --download` copies SDK ciphertext through raw S3 GET;
it never uses the application's decrypted read path. Its manifest records the
original user metadata and content type, including `key-envelope-id`, the
encryption format, and plaintext and ciphertext checksums. The backup preserves
existing platform-only objects during optional rollout as well as bank-encrypted
objects; it does not unwrap either. Format 3 inventories and downloads every
retained revision and delete marker. Replay verifies ciphertext checksums, restores
metadata and content type, and emits the old-to-new provider version mapping.
Ordinary PUT replay assigns new version IDs. Legacy sealed references therefore
still require the incident-authorized, audited version reconciler where deployed;
replay alone does not repair their database pins. The format does not capture or
replay per-version Object Lock deadlines or legal holds: recovery must separately
restore and verify those controls before resuming filing.

A recoverable object backup needs ciphertext, original bucket/key/metadata and
the matching `bank_encryption_keys` and `object_key_envelopes` catalogue from
the database backup. Use the restore command documented in
[bank key setup](bank_key_setup.md) to recreate missing buckets and restore
metadata unchanged, then read through `StorageClient`. Restore verifies
ciphertext checksums before uploading. Without the bank key, even restored
ciphertext and wrapped keys cannot be read. A copied object can use a newly
rotated live catalogue because its envelope UUID is stable, provided its recorded
KMS key remains decrypt-capable. An old database snapshot also requires its
recorded keys for its recovery lifetime; live rotation does not migrate those
wrappers. Physical historical database backups are immutable and are not
rewritten by live rotation.

The editable-data re-encryption job converts current editable platform-only objects.
Connected-key reads refuse legacy plaintext until that conversion. Retained and
version-pinned objects keep their existing protection and remain explicit old-key
dependencies; see [the migration contract](bank_field_encryption.md#editable-data-re-encryption).
Exported plaintext that a user already downloaded is outside object-storage
revocation.

## Stable sealed document reference contract

`sealed_document_objects` is an additive, immutable catalogue. Each UUID owns one
physical `sealed/v1/<uuid>` key in its bank's outputs tier, with an expected
plaintext SHA-256 and byte length. Object UUIDs are unique identities, without
cross-bank or global content deduplication. The original provider version is
write-time provenance; stable reads do not send it as a GET version token.

Four append-only association tables bind existing regulatory artifact versions,
package attachments, ICAAP attachments and supervisory letters to the catalogue.
They leave original paths, hashes, provider versions and timestamps untouched.
Real foreign keys preserve source/object existence and organization ownership;
the object FK includes bank, and an insert trigger checks the source bank,
plaintext hash/length, tier, original path and provider provenance. Artifact bank
scope is derived through its package. Published objects and bindings refuse
UPDATE, DELETE and TRUNCATE, including binding replacement. Binding a draft
supervisory letter also freezes its hash, length, path, tier and ownership; the
insert/update guards serialize that transition. A narrow definer guard preserves
existing worker updates without giving the worker catalogue access, and still
uses FORCE RLS. Tenant FORCE RLS is
mandatory; the API role has SELECT/INSERT only and other service roles have no
catalogue rights. No new authorization vocabulary or enforcement cutover occurs.

The versioned descriptor is stored as typed catalogue fields: `context_format`
(`aws-esdk-object-v1` or explicit `plaintext-v1`), original `context_path`, bank
storage slug/tier, expected plaintext hash and the original `envelope_id`.
The envelope FK includes organization and bank. Physical lookup is separate from
the authenticated path: copied legacy ciphertext is decrypted under its original
path, envelope and bank-key dependency. Headers must match the descriptor; a
replacement envelope or crypto format refuses. Plaintext descriptors never bypass
connected-key policy. This choice preserves historical key revocation semantics;
it does not decrypt/re-encrypt old documents under a newer key.

`app.storage.public.resolve_sealed_document` takes an explicit family/source
binding and a fresh complete bank-scoped VIEW authorization decision supplied by
the owning feature, with its normal whole-institution and presentation checks.
Artifact versions and package attachments use their persisted owning package's
return-family gate: ordinary returns require Regulatory Reporting/restricted,
while frozen ICAAP packages require Capital/confidential. Direct ICAAP attachments
and supervisory letters require Capital/confidential. Both module and sensitivity
must match exactly. Reporting and storage share the classification in
`app.core.return_family_access`; the storage kernel resolves package ownership
through bank- and organization-scoped metadata queries without importing a feature.
Queries constrain both organization and bank even with RLS. Missing bindings
refuse and do not select a legacy path or try a recorded provider version.
`StorageClient.read_sealed` fetches the immutable physical key, authenticates the
original context and decrypts normally. The resolver checks every plaintext byte
against the catalogue SHA-256 and length before returning a rewound file, using
an 8 MiB memory spool and bounded 1 MiB reads. Errors close both streams; callers
must close a successful file. The returned descriptor records the actual provider
version read for later access/retention evidence. Unsupported backends refuse.

This slice defines schema and resolution only: no existing writer or reader uses
stable semantics yet. Existing consumers retain their current legacy behavior.
No new HTTP route is added, so there is no new route IDOR census entry; resolver
isolation and exact family authority are exercised directly in
`tests/storage/test_sealed_documents.py` and
constraint/RLS/upgrade/downgrade behavior in
`tests/db/test_sealed_document_objects.py`.

Follow-up slices must implement atomic create-only publication, same-content
retry/conflict handling, audited binding publication with business rows and
committed envelopes, and conversion of every writer/reader (including clone,
freeze, signing and verification). Then inventory/backfill legacy revisions by
verified context-preserving copy, teach maintenance/key-retirement inventories
about stable objects, adapt retained-version and historical-manifest resolution,
and capture this catalogue with envelopes in coordinated backups. Keep the
audited reconciler as the legacy/exceptional-restore fallback. Do not enable
stable writers before immutable-key overwrite/deletion and maintenance guards
are enforced. No retention policy, backup format, signature, digest or historical
audit entry changes in this slice.

## Scope and executable evidence

The shared database and its storage encryption remain on the platform key.
Object artifacts and their object backups use the bank-held hierarchy above.
[Sensitive-field encryption](bank_field_encryption.md) reuses its `KeyProvider`
and envelope catalogue for newly written borrower identities and operational
payloads. That document lists the covered fields and numeric-only BI behavior.
Existing-data migration and retained-record reporting follow
[the editable-data re-encryption contract](bank_field_encryption.md#editable-data-re-encryption).
Signing keys and credential custody remain
[issue 355](https://github.com/CedynGroup/aequorOS/issues/355). Scheduled
key-health monitoring, alerting and support workflows remain
[issue 354](https://github.com/CedynGroup/aequorOS/issues/354). The provider
`describe` contract, onboarding probes, and administrator-triggered
`POST /api/v1/banks/{bank_id}/encryption-key/test` supply explicit observations,
not scheduled monitoring. These follow-ups are required before claiming
regulatory approval of the complete service.

`LocalKeyProvider` supports injected hermetic tests and the provider conformance
suite. The [synthetic revocation walkthrough](encryption_demo.md) owns the
file-controlled demo provider's setup and environment restrictions. It is not a
fallback for an unavailable connected bank-owned key.

Evidence lives in `tests/storage/test_key_providers.py` (local and moto AWS KMS
conformance, grant denial and outage), `test_bank_encryption.py` (all S3 tiers,
retained versions, substitution, revocation, downloads and backup recovery),
`test_key_rotation_concurrency.py` (PostgreSQL readers and concurrent writes),
`test_bank_key_rollout.py` (optional platform storage, required-key refusal and
connected-key failures in every environment),
`tests/operator/test_bank_encryption.py` (staff and bank scoping), and
`tests/db/test_tenant_rls_completeness.py` (migrated envelope RLS). The HTTP
provider contract and its CI service are documented in
[Local KMS emulator and CI](#local-kms-emulator-and-ci).

## Local KMS emulator and CI

The opt-in `encryption` profile in `backend/docker-compose.yml` runs
[local-kms](https://github.com/nsmithuk/local-kms), pinned by image digest. It is
for synthetic keys and disposable data only. Start just the emulator with:

```sh
docker compose -f backend/docker-compose.yml --profile encryption up -d risk-kms
```

For a locally launched API/worker, set `APP_ENV=local`,
`TLS_ALLOW_PLAINTEXT=1` and
`BANK_KMS_ENDPOINT_URL=http://127.0.0.1:18080`. A process inside the local compose
network may use `http://risk-kms:8080`. Restart long-lived processes after changing
configuration. The endpoint is optional: without it the provider uses real AWS
KMS. With it, the **same** `AwsKmsKeyProvider` and AWS Encryption SDK use the
emulator with hardcoded synthetic credentials, ignoring ambient AWS credentials
and SDK endpoint overrides. No fallback to AWS occurs when the emulator fails.

The override refuses deployed application startup and provider construction;
only `local`/`test` and loopback or the local compose service host are accepted.
Plain HTTP still requires the transport policy's explicit local opt-in. The
emulator service is absent from deployment compose files and application images.
Never connect retained bank data or real credentials to it. The local named
volume retains synthetic keys across restarts; keep it as long as its encrypted
disposable data is needed.

Run the provider's HTTP contract from the repository root:

```sh
TEST_KMS_ENDPOINT_URL=http://127.0.0.1:18080 mise -C backend exec -- uv run \
  pytest tests/storage/test_kms_emulator.py -q
```

The storage contract job and every dashboard journey CI shard own ephemeral
emulator services and run this contract; JUnit assertions disallow skipping it.
Journey shards run it before starting the browser stack. It covers real SDK
wrap/unwrap, authenticated context, disabled-key refusal, recovery, and data-key
rewrapping to a second key. AWS cross-account
policy and grant enforcement are outside the emulator's model and remain covered
by mocked provider tests. Guided key setup and historical re-encryption journeys
belong to their separate feature stacks.

### Browser encryption journeys

`backend/dashboard/e2e/bank-encryption.spec.ts` exercises the real AWS provider
through a loopback HTTP fault proxy. Set `BANK_KMS_ENDPOINT_URL` to the emulator
above before running `pnpm --filter @aequoros/dashboard e2e bank-encryption.spec.ts`.
A missing emulator fails the stories; they never silently substitute the local
in-memory provider. CI supplies one emulator per shard and keeps a single browser
worker, just like the other journeys.

Each story creates its own synthetic tenant, institution and analyst, using the
shared identity catalogue. The fixture ingests a synthetic identifier through
the Data Engine, copies the canonical test book's platform-derived current facts and numeric live
metrics with their analysis periods and parameter register into the isolated institution, and connects fresh emulator keys through
the operator key service. It refuses a disabled key before connecting the active
key. It accepts only the fixed disposable `e2e.db` file and `APP_ENV=test`.
Finally-based cleanup restores the fault proxy, restores key access, removes only
that story's tenant rows, and schedules its synthetic keys for deletion.

The journeys save and reread protected related-party names through the browser;
raw SQLite reads separately verify ciphertext envelopes and blind indexes, then
the exact-identifier search service checks the ingested identifier. They cover
bank revocation, retained drafts after a rejected write, disabled inputs and
save buttons, numeric liquidity continuity, outage-specific Retry and automatic
recovery, old and new records after master-key rotation, and staff-only
placeholders. Rotation leaves existing field ciphertext unchanged and retains
the old key for historical reads; it does not test the separate re-encryption
job.

Use `E2E_RUNTIME_DIR` for an external disposable runtime directory and
`E2E_EVIDENCE_DIR` for screenshots. Traces and failure artifacts live under the
runtime directory. Screenshots are review attachments, never repository assets.
The guided setup screen and historical re-encryption job remain follow-ups on
their sibling feature stacks; AWS cross-account grants remain mocked-test
coverage because the emulator does not implement their enforcement.
