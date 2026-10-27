# Bank-held field encryption

Covered fields written after a bank key is connected use that bank's encryption
key. Existing rows are not migrated. Demo datasets are re-imported through the
Data Engine after key connection.

## Covered fields

| Table                      | Fields                                                                |
| -------------------------- | --------------------------------------------------------------------- |
| `canonical_counterparties` | `name`, `source_reference`, `group_reference`, `external_identifiers` |
| `related_parties`          | `full_name`, `contact`                                                |
| `related_party_roles`      | `other_responsibilities`, `term_of_appointment`, `icag_registration`  |
| `ingestion_batches`        | `validation_report`, `etl_report`, `error_message`                    |
| `translation_failures`     | `raw_record`, `error_message`                                         |
| `lineage_records`          | `details` for bank-scoped records                                     |
| `bi_dashboard_versions`    | identity-filter values in `spec`                                      |
| `bi_subscriptions`         | identity-filter values in `query`                                     |
| `bi_alerts`                | identity-filter values in `filters`                                   |

Numeric balances, positions, counterparty calculation attributes, filing payloads,
ownership percentages and calculation inputs retain ordinary database encryption
at rest. Foreign keys, status codes and dates remain queryable. New audit events
use opaque record references instead of copying names or whole reports; existing
audit records are unchanged.

## Keys and storage

Field and object encryption reuse `BankEncryptionKey`, `ObjectKeyEnvelope`,
`KeyProvider` and the existing encryption SDK. Each immutable data-key version is
wrapped by the bank's KMS key. Ciphertext records its version UUID.

A unit of work resolves the current version once and unwraps each required version
once. Request-bound storage shares the session cache; standalone storage operations
own short-lived sessions. Plaintext material is session-local and excluded from
logs. Root transaction completion, rollback or session close discards plaintext
key material. Field encryption itself takes no key-row or advisory locks;
presentation readiness checks take the shared bank-version transaction lock.
There is no persistent or process-wide plaintext cache.
Revocation refuses the next unit once KMS enforces the permission change; an
already admitted unit may complete. Rotation creates a new current version;
earlier versions remain available to read earlier ciphertext. There is no
re-encryption maintenance command.

Object writes use committed versions, so published ciphertext remains readable
if the caller rolls back. Authenticated context binds fields to organization,
bank, table, record and column, and objects to their storage context. Key
unavailability, damaged ciphertext and cross-bank substitution are rejected.

The ORM encrypts new records and changed covered columns before flush, then
restores their application values. Counterparty identity fields load on demand;
other entity loads decrypt covered ciphertext. Scalar SQL ciphertext projections
remain opaque. Complete-register attestation commitments follow the
[attestation digest contract](../../docs/attestation_esignature.md#31-what-gets-signed--two-artefacts-per-signature).
Optional banks without a connected key retain ordinary field storage.

## Identity indexes

Bank-scoped HMAC-SHA-256 indexes support counterparty names, source references,
external identifiers and related-party names. Names use Unicode normalization,
case folding and collapsed whitespace; identifiers retain case and punctuation.
A stable index-version pointer preserves tokens across payload-key rotation.
Numeric concentration grouping uses opaque indexes.

Name index tokens are 64 characters for both connected and optional banks.
Optional banks use unkeyed SHA-256 tokens and retain plaintext names.

## BI

Connected-key banks retain numeric BI. The builder stores opaque UUID aliases
instead of readable counterparty identity. Identity dimensions and filters,
including saved dashboard and notification queries, return an explanation asking
the reader to remove those fields. Only identity-filter values in saved BI
payloads use field encryption. Numeric configuration remains readable when the
identity key is unavailable. Secured identity BI is tracked in
[issue 595](https://github.com/CedynGroup/aequorOS/issues/595).

## Dedicated database option

A dedicated database per bank remains a plan only. It would retain platform IDs
and authorization, route authenticated bank work to its database, and use separate
credentials and backup settings. This change provisions no databases or routing.

## Verification

Synthetic tests cover raw SQL ciphertext for the field catalogue, scoped reads
and writes, cross-bank isolation, exact matching, old-version reads after
rotation, numeric BI and identity refusals. PostgreSQL/storage tests exercise
concurrent ingestion, push, exports, attachments and downloads, bounded unwrap
counts and published-manifest readability after rollback.

## Protected presentation reads

Tenant GET/HEAD sessions disclose protected identity fields as locked placeholders
when a bank key is revoked or unreachable. Staff and Inspector presentation sessions
hide those fields even for optional, unconnected banks. Support filing reads expose
only filing metadata: snapshot and comparison bodies are locked, and validation,
approval and methodology details are withheld. Bank-user filing reads retain their
complete content. Numeric projections still
use their stored values and indexes. Structured executable BI configuration refuses
with a locked panel instead of substituting an empty query.

The response header `X-Aequoros-Protected-Data` reports bank-scoped availability and
protected responses use `Cache-Control: no-store`. Revocation displays “Your bank
has turned off aequorOS access to protected data. Contact your bank administrator.”
An outage displays “Can't reach your bank's key service. Retrying.” The dashboard
clears affected cached data, rejects older in-flight disclosures, retries and
refetches locked data after recovery. Responses without an availability header and
failed recovery requests leave the lock in place. Form drafts remain mounted
through refusal.
Integrity failures remain verification errors, outside the key-outage presentation.

Manual **Retry** and five-second automatic retries share one in-flight recovery
attempt for the current bank. They refetch eligible active queries for that bank;
when none are eligible, they use `GET /api/v1/banks/{bank_id}` to observe readiness
without loading protected registers. Bank detail checks institution coverage before
observing keys and requires no key-administration or institution-profile permission.
Changing banks or unmounting cancels the fallback request. Available and support
states, or no current bank, suppress recovery probes.

Bank-wide readiness checks both the current payload key and the retained identity
index key, including on empty registers and after failed historical field reads.
Each prerequisite is observed once per request, with shared versions deduplicated.
Readable historical fields remain readable, but their successful decryption cannot
clear a prerequisite failure. Revocation takes precedence over an outage, and a
later successful observation cannot clear either within the same response.

Protected writes require usable keys and never persist placeholders. Related-party
`PUT /api/v1/banks/{bank_id}/related-parties/{party_id}` updates only supplied fields;
omitted protected fields remain unchanged, so non-protected updates can succeed
while keys are unavailable. Its response can contain locked placeholders. Supplying
`roles` replaces the role list; `role_updates` changes supplied fields on existing
role IDs scoped to this bank and party, preserving their IDs and omitted values.
The two role update forms cannot be combined. Protected changes still fail with
the appropriate key message. Shareholding creation and updates also allow
placeholder reads of party and UBO identities, so non-protected shareholding
changes can succeed while keys are unavailable. Bank, party and UBO scoping still
applies, and the response identities remain locked. Other mutation sessions remain
strict.

The related-party editor retains unsaved changes separately from refreshed server
records. Refetch and key recovery refresh untouched fields without overwriting
drafts; locked placeholders never become draft values. Routine saves send only
edited fields and scoped role updates, allowing numeric-only edits while locked.
Failed saves retain the draft, including protected edits that must wait for key
recovery. Adding or removing a role requires the server membership baseline to
be present; saving that replacement list also requires readable protected values.
An explicitly empty replacement still removes every role.

While locked, protected inputs in the related-party editor, including first-entry
forms, are disabled and greyed with a lock icon and the corresponding message above
as their tooltip. Untouched protected values show a locked placeholder; user-entered
dirty values remain preserved through lock and recovery. Numeric, status, select and
reason controls remain editable. A draft containing protected changes cannot be
saved until recovery; non-protected-only saves and Cancel remain available.
Shareholding labels and UBO candidates follow current query records while selected
IDs and numeric drafts are retained, so recovery replaces locked labels with
current names.

Concentration displays configured locked limits as not evaluated and withholds a
clean breach indicator while any configured evaluation is incomplete; independent
numeric limits still evaluate.

Recovery retries also read `GET /banks/{bank_id}` to observe the connected key
when protected collections are empty after a refused first save. A headerless
successful response does not clear an existing lock. Support reads never unwrap
a bank key or poll for recovery.
