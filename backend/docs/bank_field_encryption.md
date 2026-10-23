# Bank-held field encryption

Covered fields written after a bank key is connected use that bank's encryption
key. Existing rows are not migrated. Demo datasets are re-imported through the
Data Engine after key connection.

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

A unit of work resolves the current version once and unwraps each required version
once. Request-bound storage shares the session cache. Root transaction completion,
rollback or session close discards plaintext key material. Encryption takes no
key-row or advisory locks. Rotation creates a new current version; earlier
versions remain available to read earlier ciphertext. There is no re-encryption
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
rotation. Numeric concentration grouping uses opaque indexes.

Name index tokens are 64 characters for both connected and optional banks.
Optional banks use unkeyed SHA-256 tokens and retain plaintext names.

## BI

Connected-key banks retain numeric BI. The builder stores opaque UUID aliases
instead of readable counterparty identity. Identity dimensions and filters,
including saved dashboard and notification queries, return an explanation asking
the reader to remove those fields. Saved payloads written after key connection
use the same field encryption. Secured identity BI is tracked in
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
Session-bound staff downloads return a placeholder without decrypting object
contents, including when presentation state is missing. An encrypted filing snapshot remains protected; its filing
status and source-run references remain readable.

The bank UI distinguishes revocation from a service outage, offers Retry, and
refetches active queries every five seconds while a bank is locked. Successful
protected reads clear the notice and restore values. Unrelated numeric responses
do not clear a notice. Mutation errors keep the editor mounted and its draft
values intact. Protected report previews show a locked panel, never an empty or
successful validation report.
