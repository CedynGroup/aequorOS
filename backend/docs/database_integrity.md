# Database institution ownership and reference contracts

PostgreSQL is the backstop for live financial references. A record's organization
and bank must describe the same institution, even when two banks share one
organization and its RLS tenant. Application authorization and bank-scoped queries
remain required; foreign keys are an additional integrity guarantee.

## Enforced references

| Contract                                    | Tables and columns                                                                                                                                                                                                                                               | Revision                                       |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| Organization owns bank                      | `capital_plans`, `ilaap_snapshots`, `liquidity_ewi_indicators`, `contingency_funding_plans`, `cfp_activation_events`, `market_data_source_preferences`, `stress_scenarios`, `macro_scenarios`, `management_action_plans`: `(organization_id, bank_id)` → `banks` | `202610110093`                                 |
| Financial provenance owns bank              | All twelve canonical financial tables: batch references and lineage references; `lineage_records.ingestion_batch_id`                                                                                                                                             | `202610110094`, loan lineage in `202610110096` |
| Referenced entity owns bank                 | GL account hierarchy, yield-curve points, and snapshot position/counterparty/product/GL references                                                                                                                                                               | `202610110094`                                 |
| Loan lineage exists                         | `canonical_loan_events.lineage_id` → the same organization and bank's lineage node                                                                                                                                                                               | `202610110096`                                 |
| Live ILAAP period exists                    | `ilaap_snapshots.reporting_period_id` → the same organization and bank's reporting period                                                                                                                                                                        | `202610110097`                                 |
| Replacement exists in the same entity table | The eleven canonical entity tables with `superseded_by` → `(id, organization_id, bank_id)` in that same table                                                                                                                                                    | `202610110097`                                 |
| Withdrawal batch exists                     | Those eleven tables' `withdrawn_by_batch_id` → the same organization and bank's ingestion batch                                                                                                                                                                  | `202610110097`                                 |

Organization-wide macro scenarios and management plans retain nullable bank
scopes. Batchless lineage graph nodes may also remain organization-wide, but a
batch-bound node must carry its batch's bank and a financial row cannot use an
organization-wide node as its lineage. Bank ownership is backfilled from the
existing ingestion batch, never guessed from a canonical child.

Supersession is checked **at transaction commit**. The ORM may retire a predecessor
before inserting its replacement in the same flush, so its foreign key is
`DEFERRABLE INITIALLY DEFERRED`. A transaction containing a missing or differently
scoped replacement still fails to commit. Withdrawal and ILAAP links are checked
immediately. All keys preserve their parents; no cascade deletes are introduced.

Reference rows are batch-scoped raw payloads and have no supersession/withdrawal
columns. Loan events' `position_source_reference` is a deliberate business-key
association across ingestion generations, not a pointer to one physical position
UUID. Facility supersession does not rewrite historical loan events.

## Deliberate exclusions

The following are retained evidence, snapshots, or polymorphic locators rather
than live relational parents. This work does not add foreign keys to them or
change their retention rules. Existing foreign keys on these tables remain.

| Reference family                 | Concrete locators                                                                                                                                                                                                                               | Why it stays outside these live-link keys                                                                                                                                                                                                                  |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Polymorphic audit subjects       | `audit_events.entity_type` / `entity_id` and entity identifiers within audit `details`; operator audit subject identifiers                                                                                                                      | One locator can describe a bank/organization platform ID, a UUID entity of several types, or an external subject. Audit history must survive subject changes and retirement; no single parent table represents it.                                         |
| Actor and approval evidence      | Canonical `created_by`; `capital_plans.prepared_by` / `approved_by_user_id`; ILAAP `created_by`; analogous CFP/scenario actor markers                                                                                                           | These record attribution at the event or approval time. They are not authority for current access; scoped authorization bindings govern that. Historical attribution must remain readable after account retirement.                                        |
| Notification evidence            | `cfp_activation_events.regulator_notification_id`                                                                                                                                                                                               | The activation event records the first notification identifier as historical evidence, alongside its EWI snapshot. It is not a live notification ownership relation.                                                                                       |
| BI source provenance             | `bi_fact_position_daily` / `bi_fact_position_eom`: `snapshot_id`, `position_id`, `counterparty_id`, `ingestion_batch_id`; `bi_fact_loan_event`: `event_id`, `position_id`, `snapshot_id`, `counterparty_id`; source identities in BI dimensions | These identify the source of a copied projection. BI rebuild/retention and source history have separate lifecycles; these identifiers do not authorize a source lookup. BI rows retain their own institution keys and plane authorization.                 |
| Polymorphic BI engine provenance | `bi_fact_engine_metric.run_id` / `reporting_period_id` and engine/version/source markers                                                                                                                                                        | A mart contains copied results from several engine families, not one live run parent. Retained figures preserve their provenance independently of active engine state.                                                                                     |
| Signed attestation snapshots     | `attestation_signatures.signed_source_runs`, `attestation_payload`, `auth_evidence`, snapshot/content/register digests, and the historical `signer_user_id` / `artifact_version_id` locators                                                    | Signed evidence preserves the source and identity as they stood at signing. Its verification rests on the signed payload, digest and retained artifact contract, not rebinding to mutable current rows. The existing package foreign key remains enforced. |

This is the exclusion catalogue for the schema-review findings, not a declaration
that every other UUID in the repository is an approved unbound reference. New
live links must declare their scoped parent. A new historical or polymorphic
exception should name its retention/verification contract here.

DB roles and session cleanup remain separate workstreams. The JSON shape and
measured index contracts below create no seeding path and change no financial figures.

## Migration and validation evidence

The ownership and live-link revisions above lock the affected tables and verify
across tenants through
`force_rls_suspended`: table ownership with FORCE RLS temporarily lifted, or
existing BYPASSRLS visibility. It reports the table/reference plus invalid count
before installing its keys. A failure rolls the transaction back; it never deletes
or repairs financial data. The migrations are safe for empty/test-only deployments
but do not rely on emptiness. New identity unique keys are implied by existing id
primary keys.

The executable contracts live in:

- `tests/db/test_bank_organization_integrity.py`: the nine-table ownership cases,
  nullable scopes, verification against existing mismatches, and current-head FK parity.
- `tests/db/test_canonical_bank_integrity.py`: every widened reference, valid
  provenance, lineage scope/null rules, and existing-data backfill/verification.
- `tests/db/test_loan_event_lineage_integrity.py`: missing/sibling-bank lineage and
  the original schema omission.
- `tests/db/test_canonical_migration_structural_parity.py`: every canonical table,
  ingestion batches and lineage, comparing full FK signatures, primary/unique keys
  and declared index presence against Alembic output.
- `tests/db/test_live_financial_link_integrity.py`: missing/cross-bank/cross-organization
  live links, commit-time supersession refusal, and replacement insertion after retirement.

Organization-bank and canonical-bank verification fixtures migrate to their own
revision so later guards cannot interfere with testing those earlier upgrades.
Loan-lineage, structural parity and live-link fixtures migrate to current head.
Run these with native PostgreSQL 17 via
`AQS_LOCAL_SERVICES=auto uv run python scripts/local_services.py run --postgres --role-admin -- uv run pytest tests/db/test_*integrity.py tests/db/test_canonical_migration_structural_parity.py`
from `backend/`; the service wrapper and fixtures isolate the test schemas.

## Reference JSON shapes and column representations

The [API integration guide §3.5](../../docs/API_INTEGRATION.md#35-reference-datasets)
owns reference ingestion validation, consumed field names, retained alternatives
and adapter mapping requirements. Registered schemas live in
`app/domain/ingestion/reference_schemas/` and are enforced by `ReferenceRowData`.

At head before this change (`202610120099`), 25 bounded model string declarations
in `jobs` and the original case/document/assessment/finding vertical were actually
unbounded TEXT in PostgreSQL. Models now declare TEXT too. The deployed schema is
already the data-preserving representation, so no type-changing migration is
needed. No value is truncated or cast to a narrower type.

The intentional JSON/JSONB representation contract is owned by
[CODEBASE_CONVENTIONS.md §1](../../CODEBASE_CONVENTIONS.md#sqlalchemy-models-appmodelspy).
UUID, timestamp and binary representations compile to their PostgreSQL dialect
types. The whole-root-table
column type parity test normalizes only the documented JSON/JSONB difference;
other type/width differences fail. Canonical FK/key/index parity remains intact.

Coverage: `tests/domain/ingestion/test_reference_row_enforcement.py`,
`tests/adapters/test_reference_boundary.py`, adapter end-to-end tests and
`tests/db/test_column_representation_parity.py`.

## Measured reverse-lineage indexes (`202610140100`)

Measured on task-owned native PostgreSQL 17, port 5552, in a disposable schema
migrated through `202610120099`; no customer or primary database was accessed.
The generated distribution contains 300,000 positions, 900,000 snapshots across
three dates, and 1,000 batch-bound lineage nodes. One node owns 30,000 positions
(10% of the book); the remaining nodes own about 270 each. A real existing node
has no children, exercising the negative reverse-FK case. The history distribution
has 1,000 cases with 100 edits each and one case with 20,000 edits, non-sequential
UUIDs, randomized creation timestamps and approximately 280-byte reason text.
All financial data here is synthetic query-probe material, never provisioned bank data.

The reverse-FK workload is PostgreSQL's scoped referential-integrity lookup shape:

```sql
SELECT 1 FROM ONLY canonical_positions AS x
WHERE lineage_id = :lineage AND organization_id = :org AND bank_id = :bank
LIMIT 1 FOR KEY SHARE OF x;
```

The same query applies to `canonical_position_snapshots`. The history workload
comes from `financial_workspace.financial_stmt` / `financial_validation.financial_stmt`:

```sql
SELECT * FROM financial_manual_edit_history
WHERE organization_id = :org AND case_id = :case
ORDER BY created_at ASC, id ASC;
```

`EXPLAIN (ANALYZE, BUFFERS)` results from the initial before/after run:

| Query                      | Before plan / ms                                                             | After plan / ms                       | Before / after buffer blocks (hit + read) |
| -------------------------- | ---------------------------------------------------------------------------- | ------------------------------------- | ----------------------------------------- |
| Positions: typical lineage | Limit → LockRows → Seq Scan / 13.451                                         | Limit → LockRows → Index Scan / 0.021 | 3,840 / 5                                 |
| Positions: 10% fan-out     | Limit → LockRows → Seq Scan / 11.362                                         | Same sequential path / 0.016          | 3,154 / 2                                 |
| Positions: no children     | Limit → LockRows → Seq Scan / 24.887                                         | Limit → LockRows → Index Scan / 0.008 | 6,977 / 3                                 |
| Snapshots: typical lineage | Limit → LockRows → Seq Scan / 42.573                                         | Limit → LockRows → Index Scan / 0.014 | 12,777 / 5                                |
| Snapshots: 10% fan-out     | Limit → LockRows → Seq Scan / 36.267                                         | Same sequential path / 0.007          | 10,576 / 3                                |
| Snapshots: no children     | Limit → LockRows → Seq Scan / 81.986                                         | Limit → LockRows → Index Scan / 0.009 | 23,342 / 3                                |
| History: 100 edits         | Sort → Bitmap Heap Scan → Bitmap Index Scan (existing case index) / 0.090    | Same existing path / 0.042            | 8 / 8                                     |
| History: 20,000 edits      | Gather Merge → Sort → parallel Bitmap Heap Scan → Bitmap Index Scan / 27.590 | Same existing path / 16.176           | 1,100 / 1,100                             |

High fan-out existence checks retain a sequential plan: finding the first of many
children can already be cheap. Their faster second-run times reflect warming and
scan placement, not use of the new index. The typical and negative lineage checks
use the new indexes and reduce thousands of scanned buffers to at most five.

The history candidate `(case_id, created_at, id)` occupied 7,094,272 bytes and was
unused in both history plans. Its apparent time gain is cache warming, not index
benefit; it is **not shipped**. The existing `(case_id)` path serves the measured history queries. Snapshot-parent
references already have an `(organization_id, position_id)` path, left unchanged.
No index is inferred for the other canonical tables or the FK census.

Only `(lineage_id)` B-trees on positions and snapshots ship, sized at 2,293,760
and 6,832,128 bytes respectively (about 8.7 MiB total in this distribution).
The globally unique lineage UUID narrows the candidate set; organization and bank
remain checked by the exact query and existing composite FK. These indexes cover
all generations, including retained history, because an integrity check cannot
ignore superseded or withdrawn evidence. They add one index write per inserted
position/snapshot; no covering payload or speculative wide key is included.

The additive migration uses transactional index builds, taking PostgreSQL's
normal SHARE build locks on those two tables and therefore blocking concurrent
writes for the build duration. Schedule it with the existing migration maintenance
window; table size, I/O and actual deployment plans determine the duration.
The migration rewrites no deployed revisions, deletes no rows and changes no
FK, RLS, lifecycle, grants or parent-retention semantics. Downgrade removes only
the two indexes. Timings are development evidence, not a production latency promise.
