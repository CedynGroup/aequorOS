# Bank-held sensitive-field encryption

Design for [issue 353](https://github.com/CedynGroup/aequorOS/issues/353),
building on [bank-held object encryption](bank_encryption.md). This is the
design layer, not a claim that database fields are already encrypted. Historical
audit treatment below needs a decision before implementation can establish the
complete protection boundary.

## Covered data

Cover natural persons and legal entities alike; changing a party's type or role
must not expose its identity. Encrypt historical and superseded generations as
well as current rows. The initial field catalogue is:

| Record | Fields | Reason |
| --- | --- | --- |
| `canonical_counterparties` | `name`, `external_identifiers`, `group_reference`, `attributes`, `source_reference` | Names, national/tax/registration identifiers, connected-person relationships and free-form identity attributes; a source customer reference can itself be a personal identifier. |
| `related_parties` | `full_name`, `contact` | The shared record for directors, shareholders, beneficial owners and other registered parties. Protect the entire contact object, including arbitrary source-specific identity fields. |
| `related_party_roles` | `other_responsibilities`, `term_of_appointment`, `icag_registration` | Free text and professional identifiers can identify the person even when the party name is encrypted. Resolve the owning bank through the organization-scoped parent party. |
| Ingestion and transformation copies | Sensitive values in translation failures, validation/ETL reports, lineage details and persisted matching/review payloads | Encrypt whole free-form payloads when they can contain covered values; protecting only the final canonical row is insufficient. Inventory producers before the implementation cutover. |
| Bank audit evidence | Covered identity values in event details, including the `full_name` currently written by related-party create/update | Avoid retaining readable duplicates of the protected register. Historical handling is the decision below. |

Numeric balances, notionals, positions, rates, share quantities, ownership
percentages and allowances retain ordinary database encryption at rest. Keep
foreign-key UUIDs, platform IDs, role/status codes, dates and provenance links
queryable. This protection does not promise relationship unlinkability: the
database still shows which opaque party owns a position or shareholding. Adapter
and reporting review must also identify copies of customer references in
position attributes and other free-form payloads rather than treating those
copies as safe merely because the source column has a different name.

Uploaded inputs, canonical artifacts and exports continue through `StorageClient`
under the existing bank key. No plaintext identity should be added to logs,
exception messages, job progress, search indexes or calculation snapshots as a
side effect of this work. Existing retained artifacts and physical backups need
the migration/retention treatment below; encrypting a live row does not rewrite
them.

## Envelope and application boundary

Reuse `app/core/key_management/types.py`'s `KeyProvider`, the connected
`BankEncryptionKey`, and the committed-message helpers in `sdk.py`. Extend the
existing envelope catalogue and rotation path, rather than adding another master
key registry or provider. Each protected record gets random data-key material
wrapped by its bank's master key. Store only ciphertext and the wrapped-key
reference. Authenticate organization, bank, table, record ID, field/payload name
and format version; reject ciphertext moved between banks, records or fields.
Encryption context must contain no identity values.

Reads and writes use explicitly scoped application services. Unavailable
connected keys, missing envelopes, context mismatch and corrupt ciphertext fail
closed with a safe error. Never fall back to a platform key or serve a legacy
plaintext value for a connected bank. No plaintext-key cache survives an
operation. Bankless rollout must follow the existing optional-key contract and
must never be presented as bank-held protection; enable protection only after
that bank's migration and verification complete.

Keep API field shapes unchanged. Inventory both full-model reads and scalar SQL
projections: reports, AI deny-term collection, deduplication and behavioral
history all read identity fields today. Fetch/decrypt under bank scope before
formatting or matching. Move name ordering into the application. Group numeric
SQL aggregates by opaque identities or a dedicated grouping index, then decrypt
labels; randomized ciphertext is not a stable SQL grouping key.

## Exact-match indexes

Create a random, per-bank index secret wrapped through the same provider and
envelope catalogue. Derive domain-separated HMAC-SHA-256 values over a versioned,
length-delimited encoding of organization, bank, field identity and normalized
query value. No platform-wide secret and no unkeyed hashes of names or national
identifiers. Use an independent secret from payload encryption material.

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
   batch. Coordinate writers with the same lock/cutover protocol. No real bank
   data or credentials enter tests or checked-in migration fixtures.
3. Resume by format/version, authenticate completed envelopes and skip already
   migrated rows. Re-running must not change ciphertext, create extra wrappers
   or erase data. Provider failure rolls back the batch. Never silently skip a
   bank whose key is unavailable or report a partial RLS-visible scan as complete.
4. Compare scoped counts and product reads, verify every covered raw SQL column
   and payload lacks plaintext, then enforce ciphertext-only reads/writes for
   the protected bank. Remove plaintext compatibility columns only after the
   fleet completes migration. Downgrading must never silently decrypt data.

Master-key rotation re-wraps payload and index secrets in the existing atomic
bank-key rotation transaction; ciphertext, blind-index values and envelope IDs
stay stable. Readers and writers share its bank-key locking protocol. Keep old
master keys decrypt-capable for the full backup-retention hold, as already
required by `bank_encryption.md`. Automatic KMS material rotation under one ARN
does not rebuild data or indexes.

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

## Historical audit decision

`identity/service/institution_profile.py` currently includes `full_name` in
`related_party.created` and `related_party.updated` audit details. Migration
`202610100090` hashes `to_jsonb(NEW)` into `aequoros-audit-v1` chain entries;
`core/audit_integrity.py::verify_chains` recomputes that hash from the stored
source row. Migration `202610110095` forbids updating/deleting those events.
Application-level decryption does not preserve this SQL verifier's input.

Two materially different outcomes need firstmate direction:

- **Complete historical confidentiality:** authorize an explicit audit-chain
  format transition. Verify and independently retain the old chain checkpoint,
  encrypt historical sensitive event payloads, and bind the new representation
  and predecessor checkpoint in a migration receipt/new chain epoch. Specify
  how an authorized examiner validates continuity without publishing plaintext
  and how old retained backups are handled. This changes the existing evidence
  contract and needs approval before changing guards or verification.
- **Preserve historical source bytes:** leave existing sealed events unchanged,
  stop copying names into new events (use opaque party references), and encrypt
  operational identity fields. Historical audit names remain readable in the
  shared database. This is an explicit scope exception, so it cannot satisfy
  issue 353's complete covered-identity protection claim without acceptance of
  that limitation.

Do not disable append-only guards, rehash old history in place or silently omit
the historical audit copies while waiting for that decision.

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
