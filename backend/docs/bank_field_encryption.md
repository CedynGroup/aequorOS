# Bank-held field encryption

Covered fields written after a bank key is connected use that bank's encryption
key. Existing rows are not migrated. Demo datasets are re-imported through the
Data Engine after key connection, following the
[deployment prerequisite](bank_encryption.md#onboarding-and-operator-api).

## Covered fields

| Table                      | Fields                                                                              |
| -------------------------- | ----------------------------------------------------------------------------------- |
| `canonical_counterparties` | `name`, `source_reference`, `group_reference`, `external_identifiers`, `attributes` |
| `related_parties`          | `full_name`, `contact`                                                              |
| `related_party_roles`      | `other_responsibilities`, `term_of_appointment`, `icag_registration`                |
| `ingestion_batches`        | `validation_report`, `etl_report`, `error_message`                                  |
| `translation_failures`     | `raw_record`, `error_message`                                                       |
| `lineage_records`          | `details` for bank-scoped records                                                   |
| `regulatory_packages`      | `snapshot`, `validation_report`, `notes`                                            |
| `bi_dashboard_versions`    | Identity filter `values` within `spec`                                              |
| `bi_subscriptions`         | Identity filter `values` within `query`                                             |
| `bi_alerts`                | Identity filter `values` within `filters`                                           |

Numeric balances, positions, ownership percentages and calculation inputs retain
ordinary database encryption at rest. Foreign keys, status codes and dates remain
queryable. New audit events use opaque record references instead of copying names
or whole reports; existing audit records are unchanged.

## Keys and storage

Field and object encryption reuse `BankEncryptionKey`, `ObjectKeyEnvelope`,
`KeyProvider` and the existing encryption SDK. Each immutable data-key version is
wrapped by the bank's KMS key. Ciphertext records its version UUID.

A unit of work resolves the current version once and establishes access to it
before opening any historical payload or index version. Each required version is
unwrapped at most once. Revoking the current key therefore blocks fresh protected
reads, exact searches and object access once KMS enforces the permission change,
even while an older key remains enabled; an already admitted unit may complete.
Request-bound storage shares the session cache; standalone operations own
short-lived units. Root transaction completion, rollback or session close discards
plaintext key material, which is excluded from logs. No persistent or process-wide
plaintext key cache is permitted. Encryption takes no key-row or advisory locks.
Rotation creates a new current version; earlier versions remain available to read
earlier ciphertext. There is no re-encryption
maintenance command.

Object writes use committed versions, so published ciphertext remains readable
if the caller rolls back. Authenticated context binds fields to organization,
bank, table, record and column, and objects to their storage context. Key
unavailability, damaged ciphertext and cross-bank substitution are rejected.

The ORM encrypts new records and changed covered columns before flush, then
restores their application values. Entity loads decrypt covered ciphertext;
scalar SQL ciphertext projections remain opaque. Optional banks without a
connected key retain ordinary field storage.

## Exact-match search

Bank-scoped HMAC-SHA-256 indexes support counterparty names, source references,
external identifiers and related-party names. Names use Unicode normalization,
case folding and collapsed whitespace; identifiers retain case and punctuation.
Searches scope by organization and bank and confirm decrypted candidate values.
A stable index-version pointer keeps exact matches working across payload-key
rotation. Numeric concentration grouping uses opaque indexes derived from the
connected-group reference, falling back to the counterparty name. Positions
without a counterparty retain literal `pos:<source_reference>` keys.

Name index tokens are 64 characters for both connected and optional banks.
Optional banks use unkeyed SHA-256 tokens and retain plaintext names.

## BI

Connected-key banks retain numeric BI. The builder stores opaque UUID aliases
instead of readable counterparty identity. The protected identity member policy
is defined in [`app/bi/domain/identity.py`](../app/bi/domain/identity.py); it applies
across measures, dimensions, filters, sorting, pivots and top-N queries. Queries
requesting those members, including saved dashboard and notification queries,
return an explanation asking the reader to remove those fields. Aggregate
counterparty dimensions outside that policy remain available. Saved payloads
written after key connection use the same field encryption. Secured identity BI
is tracked in [issue 595](https://github.com/CedynGroup/aequorOS/issues/595).

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

## Locked presentation reads

Tenant GET/HEAD requests carry request-local disclosure state. If a provider is
unreachable or disabled, covered text becomes `🔒 Protected data`; covered report
objects carry `_aequoros_protected_data` with `unavailable` or `revoked`. The
`X-Aequoros-Protected-Data` response header reports state by bank. Numeric columns,
record identities, dates and statuses retain their original values. Integrity
failures still refuse access. Background work and mutation requests never use
presentation substitution, and submitted placeholders cannot overwrite fields.

Staff inspection sessions substitute covered fields without unwrapping bank
keys, including fields predating key connection. Protected executable BI payloads
refuse with a named locked response rather than being treated as empty queries or
filters. Dashboard layouts, numeric queries and numeric filter values remain
readable without a key, as do numeric alert and subscription configurations.
Session-bound staff downloads and download-link issuance return the named support
refusal without decrypting object contents, including when presentation state is
missing. Integrity consumers receive that refusal instead of placeholder bytes.
An encrypted filing snapshot remains protected; comparison and attestation seal
recomputation refuse locked snapshots with the named locked-data response before
producing equality or integrity verdicts. Filing status and source-run references
remain readable.

The bank UI displays separate messages:

- Revoked: "Your bank has turned off aequorOS access to protected data. Contact your bank administrator."
- Unreachable: "Can't reach your bank's key service. Retrying."

Both states offer Retry and refetch active locked or failed protected queries
every five seconds. Each recovery attempt also reads `GET /banks/{bank_id}` to
confirm current connected-key access, even when protected collections are empty
after a refused first save. Its disclosure header reports the result; a headerless
success cannot clear a lock. Staff support placeholders do not poll for recovery.
Successful protected reads clear the notice and restore values; cached locked or
failed reads, including inactive screens, are invalidated so returning to a
screen refreshes it immediately. Unrelated numeric responses and responses from
requests started before the current lock cannot clear that lock. Shared fetch
boundaries reject stale protected response bodies, including after a repeated
lock response with the same state, before they can repopulate caches or
notifications.

Mutation errors and late key-outage or revocation responses preserve unsaved
editor drafts. The related-party editor disables and greys its fieldset and submit
button once protected data is locked; Cancel remains usable. Starting an edit of
a locked related-party row is disabled until disclosed data returns. Recovery
re-enables an already-open editor with its draft intact and refreshes shareholding
candidate labels without discarding numeric drafts. BI catalogue data and saved
dashboard definitions without protected identity members remain cached, retaining numeric
dependencies, dashboard title, widgets and change note, and the alert's selected
threshold basis. Protected cached payloads and read-only labels are still removed or
replaced by placeholders, scoped to the locked bank. Protected report previews
show a locked panel, never an empty or successful validation report.

The concentration monitor continues computing numeric exposures, shares and HHI
when labels are locked. Named single-name or connected-group limits that require
the index key appear as locked and are not evaluated; they remain included in the
registered limit count. Literal positional limits and limits that do not need
identity matching remain evaluable.
