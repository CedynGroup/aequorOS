# Bank-held sensitive-field encryption

Design for [issue 353](https://github.com/CedynGroup/aequorOS/issues/353),
building on [bank-held object encryption](bank_encryption.md). This is the
design layer; implementation follows in the stacked change. The historical
audit scope exception below is an explicit captain decision.

## Covered data

Cover natural persons and legal entities alike; changing a party's type or role
must not expose its identity. Encrypt historical and superseded generations as
well as current rows. The initial field catalogue is:

| Record | Fields | Reason |
| --- | --- | --- |
| `canonical_counterparties` | `name`, `external_identifiers`, `group_reference`, `attributes`, `source_reference` | Names, national/tax/registration identifiers, connected-person relationships and free-form identity attributes; a source customer reference can itself be a personal identifier. |
| `related_parties` | `full_name`, `contact` | The shared record for directors, shareholders, beneficial owners and other registered parties. Protect the entire contact object, including arbitrary source-specific identity fields. |
| `related_party_roles` | `other_responsibilities`, `term_of_appointment`, `icag_registration` | Free text and professional identifiers can identify the person even when the party name is encrypted. Resolve the owning bank through the organization-scoped parent party. |
| `ingestion_batches` | `validation_report`, `etl_report`, `error_message` | Validation findings and matching samples can copy source identities. |
| `bi_dashboard_versions`, `bi_subscriptions`, `bi_alerts` | `spec`, `query`, `filters` respectively | Saved identity-filter values, including historical views. Connected banks refuse identity BI until issue 595. |
| `translation_failures` | `raw_record`, `error_message` | Preserve rejected input for authorized review without exposing its identity fields. |
| `lineage_records` | `details` | Transformation and deduplication evidence can copy identities. Organization-wide, bankless nodes remain outside this bank-scoped boundary. |
| `regulatory_packages` | `snapshot`, `validation_report`, `notes` | Generated corporate and large-exposure returns contain registered names and named findings. Protect the complete document payload; numeric canonical balances remain queryable. |

Numeric balances, notionals, positions, rates, share quantities, ownership
percentages and allowances retain ordinary database encryption at rest. Keep
foreign-key UUIDs, platform IDs, role/status codes, dates and provenance links
queryable. This protection does not promise relationship unlinkability: the
database still shows which opaque party owns a position or shareholding. Source account/position locators and numeric classification attributes remain
operational metadata; direct borrower identity belongs in the protected
counterparty record. This catalogue protects the listed fields and their named
report/ingestion copies, not arbitrary personal information placed in unrelated
free-text fields by a user. Expanding the catalogue requires a reviewed producer
and consumer inventory.

Uploaded inputs, canonical artifacts and exports continue through `StorageClient`
under the existing bank key. No plaintext identity should be added to logs,
exception messages, job progress, search indexes or calculation snapshots as a
side effect of this work. Existing retained artifacts and physical backups need
the migration/retention treatment below; encrypting a live row does not rewrite
them.

## Envelope and application boundary

The captain's revised decision (review R1, review-1, R7 and R8) replaces
transaction-held encryption locks with immutable, versioned data keys. This
supersedes the earlier shared-lock/rotation-exclusion decision. Reuse
`BankEncryptionKey`, `ObjectKeyEnvelope`, `KeyProvider` and the existing SDK;
there is no parallel master-key hierarchy.

`BankEncryptionKey` points to the current version in `ObjectKeyEnvelope`.
Each version contains a random bank data key wrapped by the bank's KMS key,
its exact KMS reference, and an opaque version UUID. Field ciphertext and object
metadata already carry an envelope UUID; that UUID now identifies the immutable
version. Legacy envelopes retain their recorded KMS references and remain
readable. The schema migration records those references without accessing KMS.
Existing connections initialize their first current version through the explicit
maintenance/backfill entry point before accepting new encrypted writes.

Each database unit of work resolves its current version once and unwraps each
required version once. Only authenticated material is retained in session-local
memory, excluded from representations and logs; root transaction completion,
rollback and session close discard it. A new unit of work must contact KMS
again, so disabled keys fail closed. A revocation during an already admitted
unit of work takes effect at its next boundary. Bulk entity loads and storage
bound with `StorageClient.for_session(db)` share this cache. Standalone storage
operations own a short-lived unit of work; download dependencies bind storage
to the request session. No global plaintext-key cache is permitted.

Payload keys are domain-separated by their authenticated encryption context.
Messages additionally authenticate organization, bank, table, record and field,
or the complete storage context. The initial version also supplies a separately
domain-separated blind-index secret; its stable version pointer preserves exact
matches across payload-key rotation. Existing independent index envelopes are
retained unchanged. Each required historical/index version adds at most one
unwrap per unit of work, rather than one unwrap per entity.

Encryption reads and writes acquire no key-row or advisory locks. Optional
plaintext field writes retain the bank-row cutover guard, before any encryption:
a stale no-key session refuses if a connection appeared, and registration waits
for earlier plaintext writers before checking immutable history. Connected-key
field/object operations never take that guard. Rotation serializes its bookkeeping
and atomically publishes a new wrapped version and current pointer on commit.
Concurrent work may finish with its previously resolved version; new work uses
the new version. Rotation never rewrites existing
ciphertext or envelopes. Prior KMS keys and versions remain decrypt-capable.
Retirement refuses any key still referenced by an envelope as well as any
unexpired backup hold.

A published object must reference an already committed version. Object writes
create no transactional envelope that could disappear when the business
transaction rolls back. Even if publication succeeds and the follow-up stat
fails, overwritten push manifests and all other published versions remain
readable. An uncommitted connection or rotation cannot publish an object using
its uncommitted version.

Unavailable keys, unknown versions, scope/context mismatch, corrupt ciphertext
and legacy plaintext at a connected bank fail closed. Optional banks retain
their existing behavior. API field shapes, numeric aggregation, audit source
bytes, immutable-history guards and the numeric-only BI restriction are unchanged.

## Exact-match indexes

Use the domain-separated secret from the fixed index version defined above,
distinct from every payload key. Derive domain-separated HMAC-SHA-256 values over
a versioned JSON encoding of organization, bank, field identity and normalized
query value. No platform-wide secret and no unkeyed hashes of names or national
identifiers.

Names use Unicode normalization, case folding and collapsed whitespace;
source-system identifiers retain case and significant punctuation. External
identifier indexes include the identifier namespace. Record normalization as a
versioned contract. Scope every lookup by organization and bank, compute the
blind index only after unwrapping that bank's secret, and decrypt candidates to
confirm the normalized equality. Preserve canonical source-reference uniqueness
using its blind index with the existing source system/date/current-generation
predicate. Neither ciphertext nor an index permits substring or fuzzy SQL search;
existing deduplication operates on authorized decrypted values.

An index leaks equality and frequency within its bank and field. It must not
correlate identical identities across banks. Do not index fields without a
demonstrated exact-match use. Changing normalization or rotating the index secret
requires a versioned, resumable index rebuild; use a bounded dual-index transition
until all rows are rebuilt, then retire the old index.

## Migration, rotation and verification

1. Add envelope references, ciphertext capacity and versioned blind-index
   columns using additive schema migrations. Inventory every persisted copy
   listed above, including historical generations and retained evidence.
2. Run an explicit per-bank backfill with bank-approved key access, tenant RLS
   context, bank-scoped queries and bounded row locks/batches. Encrypt, index,
   clear the old plaintext and mark the completed format in one transaction per
   batch. Require completed registration/version initialization before accepting protected writes. No real bank
   data or credentials enter tests or checked-in migration fixtures.
3. Resume by format/version, authenticate completed envelopes and skip already
   migrated rows. Preserve business timestamps and value digests during the
   storage-only change. Re-running must not change ciphertext, create extra wrappers
   or erase data. Provider failure rolls back the batch. Never silently skip a
   bank whose key is unavailable or report a partial RLS-visible scan as complete.
4. Compare scoped counts and product reads, verify every covered raw SQL column
   and payload lacks plaintext, then enforce ciphertext-only reads/writes for
   the protected bank. Remove plaintext compatibility columns only after the
   fleet completes migration. Downgrading must never silently decrypt data.

Rotation creates a new current version; it does not re-wrap old versions.
The separate resumable `--reencrypt` maintenance mode migrates mutable fields
to the current version while preserving blind indexes and business timestamps.
Already migrated fields are skipped on repeat. Immutable snapshots and dashboard
versions keep their recorded versions and unchanged guards; they require the
separately approved retained-evidence transition before any old key can retire.
Object versions also retain their original version until an explicit retained
object migration. Old physical backups and stable index versions remain key
references. Automatic KMS material rotation under one ARN does not change these
version pointers or rebuild indexes.

Old physical backups can contain plaintext even after live backfill. Inventory
them, restrict restore/use, establish a verified post-migration recovery point
and expire or replace old copies under the approved retention policy. A restore
from an old copy must repeat migration before serving protected data. Do not
claim historical-backup protection until those copies age out or are separately
protected. Immutable filing evidence needs its own approved transition, never a
silent rewrite.

Required synthetic evidence: raw SQL ciphertext for every catalogue field and
copy; bank A unable to decrypt bank B's data; record/field substitution and
tamper refusal; product ingestion, register CRUD, reports and exact-match search;
unchanged numeric calculations; unavailable-key refusal; rotation preserving
reads and searches; interrupted/repeated backfill producing identical completed
state. Exercise migrated PostgreSQL constraints/RLS as well as hermetic provider
tests.

## Historical audit scope exception (approved)

The captain chose to preserve existing audit source bytes: no audit-chain epoch
transition, rehash or append-only guard change. No banks are live; existing audit
rows contain only test data. Those historical test audit names remain readable
in the shared database and are an explicit exception to historical identity
confidentiality. Do not describe them as bank-key protected.

New `related_party.created` and `related_party.updated` audit events omit
`full_name` and retain the opaque party ID, bank, role codes and mutation reason.
New ETL audit events retain counts and opaque batch references instead of copying
whole reports; detailed reports remain in the protected operational payloads.
User-entered reasons remain audit evidence, so officers should not enter borrower
identity into those reasons.

The reason for this boundary is structural: `202610100090` hashes `to_jsonb(NEW)`
into the audit chain, and `core/audit_integrity.py::verify_chains` recomputes from
raw stored source bytes. Replacing historical names with ciphertext would break
verification even if application reads decrypted them. Existing audit guards and
verification remain intact.

The captain also approved a clean-history cutover precondition: a bank may
connect its key only when it has no legacy plaintext documents in
`bi_dashboard_versions.spec` or `regulatory_packages.snapshot`. These whole
protected documents are checked conservatively, without guessing which nested
fields contain identities. Key registration and backfill explicitly refuse such
history with a clear reason before modifying data. A bank with these records
needs a separately approved retained-evidence storage transition. Existing guards
and privileges remain unchanged; no reset is required for synthetic history.
The pilot starts with newly encrypted filings and dashboard versions, rather
than treating a failed backfill as successful.

## BI identity boundary (approved numeric-only pilot)

For connected-key banks, numeric BI remains available. Identity dimensions
(`counterparty.id`, `name`, `source_reference`, `group`) and queries that use
them are explicitly refused, including filters in saved dashboards,
subscriptions, hierarchies, pivots and sort keys. Numeric concentration
measures continue to aggregate opaque UUIDs under their usual authorization.
The response explains bank-held encryption and asks the reader to remove the
identity fields or filters; granting roles cannot remove this restriction.
Full secured identity BI is follow-up
https://github.com/CedynGroup/aequorOS/issues/595.

The builder writes opaque canonical UUID aliases in
`bi_dim_counterparty.name` / `source_reference`, with NULL group references.
Daily and end-of-month position facts carry NULL `counterparty_group`.
Classification dimensions (type, country, rating) and numeric facts retain their
normal behavior. Bank backfill removes existing derived identity copies, scoped
to that bank, and can safely repeat. Keyless optional-rollout banks retain their
existing BI behavior. No bank may claim cutover until backfill succeeds.

Saved dashboard-version `spec`, subscription `query` objects and alert `filters` lists use the same
bank-held field encryption as operational records, protecting historical filter
values. New saves and executions evaluate the same identity-member restriction.
Append-only historical dashboard versions follow the approved clean-history
precondition above; this change never bypasses their guards.
The query log stores digests and member IDs, never raw filter values.

## Strict-bank dedicated-database option (plan only)

Offer a separately provisioned database/storage encryption boundary per bank,
under a bank-controlled key and bank-approved region, in addition to field
encryption. Keep platform IDs and tenant authorization unchanged. A routing
catalogue selects the database by authenticated bank identity; never accept an
arbitrary client-provided connection target. Give API/worker/reporting pools
scoped credentials, independent migrations and backup/recovery catalogues. Keep
the shared control plane free of bank identity payloads.

Before offering it, design bank-scoped routing for jobs and reporting, prevent
cross-database joins, define control-plane outage behavior and key revocation,
test restore/rotation, and document cost, residency, retention and operational
ownership. A migration needs reconciled counts, bank approval and a controlled
cutover; all old shared copies and backups need a retention decision. This issue
does not provision dedicated databases or change deployment routing.

## Rollout entry point and evidence

After applying the additive schema migration, run the dedicated maintenance
session from `backend/` with the owning bank's grant available:

```sh
PYTHONPATH=. uv run python scripts/encrypt_bank_fields.py \
  --organization-id OR-DEM00001 --bank-id BK-SAMP0001 --batch-size 100
```

The IDs above are synthetic examples. The command accepts no key material and
prints only counts. It authenticates completed ciphertext on every repeat,
commits bounded batches, and fails on unavailable keys or protected historical
records. Backfill the bank before serving it with a connected key; normal reads
refuse legacy plaintext for a connected bank. A downgrade refuses encrypted
columns rather than silently discarding or decrypting them.

The codec lives in `app/core/key_management/fields.py`, shares
`object_key_envelopes` and the session-scoped version cache, and rejects bulk writes
that bypass scoped ORM encryption. Scalar SQL projections of protected columns
are opaque; product consumers load authenticated, bank-scoped entities instead.
Exact-match services confirm decrypted candidates after keyed-index lookup.
`tests/core/test_bank_field_encryption.py` exercises the field catalogue and
product behavior with synthetic providers and data; PostgreSQL migration and
retained-evidence suites exercise the deployed schema contracts.
