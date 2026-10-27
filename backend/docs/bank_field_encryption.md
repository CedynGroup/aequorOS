# Bank-held field encryption

Covered fields written after a bank key is connected use that bank's encryption
key. Connecting an existing bank queues resumable re-encryption of its editable
fields and objects. Immutable evidence stays under its existing protection and
is explicitly reported as retained; it is never silently treated as migrated.

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
once. PostgreSQL units share a bank-scoped transaction advisory lock with object
publication. First connection, index cutover, and final migration verification
take it exclusively to drain earlier admitted writers. Request-bound storage
shares the session cache; standalone operations own short-lived units. Plaintext
key material is excluded from logs. Revocation refuses the next unit once KMS
enforces the permission change; an already admitted unit may complete.
Root transaction completion, rollback or session close discards session key
material. Isolated large-object migration retains only its
captured source and target object secrets for the transfer, as described below;
they are scoped to that dispatch. No persistent or process-wide plaintext cache
is permitted. Ordinary encryption takes no key-row locks.
Rotation creates a new current version; earlier versions remain available to read
earlier ciphertext. The operator API exposes an explicit re-encryption job;
rotation itself does not queue one.

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
Searches scope by organization and bank and confirm decrypted candidate values.
A stable index-version pointer keeps exact matches working across routine
payload-key rotation. First connection re-indexes every generation, including withdrawn rows,
atomically before publishing the connection. After a master-key replacement,
explicit re-encryption moves the index secret to an independent envelope under
the current key without changing tokens.
This prevents mixed raw/HMAC natural keys during re-import and withdrawal
reversal. The current-generation unique index also arbitrates concurrent
re-import/reversal; a losing reversal returns 409. Numeric concentration grouping uses opaque indexes.

Name index tokens are 64 characters for both connected and optional banks.
Optional banks use unkeyed SHA-256 tokens and retain plaintext names.
Counterparty names retain the plaintext length limit defined by
[`CounterpartyData`](../app/domain/ingestion/contracts.py), enforced during
ingestion and before persistence for both optional-key and connected-key banks.

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

## Editable-data re-encryption

On the staff operator API, an administrator with an active inspector session may
POST `/operator/v1/tenants/{org_id}/banks/{bank_id}/encryption-key/reencrypt`
to enqueue migration to the current data-key version. GET the same path to read
the latest job's status and progress. Connection of an existing bank enqueues this
job automatically; ordinary key rotation does not. No bank financial data is
created by key connection or migration.

For an existing aequorOS deployment, the schema upgrade backfills all five
identity indexes before switching their consumers. It records the current KMS
reference on each legacy object envelope, creates current/index data-key versions
for every connected bank (including empty banks), and reindexes connected banks
under their index key. The upgrade requires access to those bank KMS keys; a
provider refusal rolls back the upgrade. Legacy object envelopes continue to
authenticate their original per-object context and unwrap the original secret
until editable objects move; retained versions keep that decryption path.

The core worker runs `bank_reencrypt`. Progress records the target version,
phase, field-table/row checkpoint, exact object-version cursor, rewritten counts,
retained counts by type, and a human-readable summary. Each dispatch processes
at most 100 field rows or 10 object versions, checking a 30-second work budget
between atomic operations, then commits its checkpoint and queues its successor
in the same transaction. BI identity cleanup and catalogue cleanup also use
bounded batches. Verification uses the same persisted cursors rather than a
full-bank pass in one dispatch. The queue's ordinary 900-second reclaim window
applies to normal batches. Interrupted batches resume their committed checkpoint;
duplicate runners serialize on the shared job row and may return without further
work only after observing committed handoff or terminal state. A rolled-back
handoff leaves the batch reclaimable. Object retries discover already
published current-version ciphertext. A failed or exhausted job can be requested
again safely. If rotation changes the current version mid-run, the job restarts
its scan for that version. Old keys must remain decrypt-capable throughout migration.
Handed-off parents and completed progress cannot absorb a later request, even
when a worker died before recording their final queue status.

The job locks each editable row before replacing only its protected columns,
preserving logical values and timestamps. It updates neither saved BI version
specs nor regulatory package snapshots, even when the filing is unsigned. Other
editable protected columns on regulatory packages can move. Audit history is
unchanged, including its chain and captured columns. Audit records lack a
universal bank ID, so retained audit counts conservatively include the entire
organization's tenant and operator audit history, labelled as organization scope.

Editable BI identity copies are included bank-wide, across all historical dates:
counterparty dimension names and source references become UUID aliases, dimension
group references are cleared, and daily/EOM position group copies are cleared.
The builder participates in the same writer barrier, and final verification
checks these copies alongside protected fields. Numeric mart values remain intact.

Current editable stored objects are authenticated and conditionally replaced
using their ETag, including conditional multipart completion for large objects.
Uploads use bounded parts under the same size policy as ordinary storage writes.
Inventory requests examine one exact object version at a time, checking the
dispatch budget before requesting another. Their saved markers preserve the
literal `null` version of an unversioned predecessor.

Objects of at least 16 MiB run alone in a dedicated dispatch. Its persisted
payload records the source size before claim: the queue adds eight seconds per
MiB to the deployment's normal reclaim window, budgeting transfer and transform
work at 1 MiB/s with margin. An interrupted large dispatch remains reclaimable
after that finite window and retries the object from the beginning; there is
no resumable state inside an object.

The large dispatch captures authenticated source/current keys in a short
transaction, then releases the bank writer barrier for the download, transforms,
and multipart upload. The uploaded parts form an unpublished new object copy.
At publication it briefly reacquires the barrier, validates the current key and
database pins, and rechecks exact source version, ETag, metadata, retention and
legal hold. Conditional multipart completion atomically swaps the logical
object's pointer to the new version. A concurrent write or rotation aborts the
copy; the next batch inventories the source again. Historical versions retain
their exact identities. No bank-wide barrier spans the large transfer.
Legacy platform-protected content is accepted only by this
maintenance path after checksum verification; normal connected-bank reads keep
refusing it. A concurrent changed object defeats the conditional replacement and
is checked again. Historical versions, compliance/legal-hold versions, and any
version pinned by a database record remain intact and are reported by storage
tier. Creating a new current object version therefore retains the original when
the bucket is versioned. Physical historical backups are not rewritten.

Each verification batch drains admitted writers through the bank's writer
barrier. Writers admitted afterward use the current version and clear derived BI
identity copies, so older-key references cannot appear behind the saved cursor.
A target-version change restarts verification. Across these bounded batches,
verification checks both fields and the full object-version inventory.
With retained records, the summary is exactly
`complete except N retained immutable records`, with counts by type. It never
reports plain `complete` while any such records remain outside the target
version. This is completion of editable-data migration, not removal of every
old-key dependency. After verification, aequorOS removes catalogue envelopes in
bounded batches under the same writer barrier, retaining all collected references.
It removes only envelopes that no protected field or stored object version references, preserving
the current and blind-index versions. Retained immutable fields and historical,
pinned or locked object versions keep their envelopes. Retirement authorization
continues to refuse while any catalogue reference or backup hold requires the
key, and permits retirement of an unreferenced version after backup retention
expires. The job does not destroy keys. The worker can update only the key-version
pointers and timestamp; operator audit retention is counted through a read-only
audit-owner function without direct worker access to operator audit rows.
Guard changes and audit-chain migrations are outside this workflow.

## Protected presentation reads

Tenant GET/HEAD sessions disclose protected identity fields as locked placeholders
when a bank key is revoked or unreachable. Staff and Inspector presentation sessions
hide those fields even for optional, unconnected banks. Support filing reads expose
only filing metadata: snapshot and comparison bodies are locked, and validation,
approval and methodology details are withheld. Bank-user filing reads retain their
complete content. Numeric projections still
use their stored values and indexes. Lists of alerts, subscriptions, dashboards and
versions keep numeric entries available and mark only protected configurations
with `locked_reason`. Locked subscriptions return no query, and locked dashboards
return no widgets. Their editors show the reason instead of treating the masked
configuration as an empty draft. Alert evaluation records the protected item as
not evaluated while continuing with numeric alerts. Operator ingestion metadata
and subscription scheduling select no protected report or query payload.

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
