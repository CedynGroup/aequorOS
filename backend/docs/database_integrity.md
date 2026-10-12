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

DB roles, session cleanup, and JSON shapes/index optimization remain separate
workstreams. This change creates no seeding path and changes no financial figures.

## Migration and validation evidence

Each revision locks the affected tables, suspends FORCE RLS under table ownership
for cross-tenant verification, and reports the table/reference plus invalid count
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

Layer verification fixtures migrate to their own revision so later guards cannot
interfere with testing an earlier upgrade. Structural parity and live-link cases
migrate to current head. Run these with native PostgreSQL 17 via
`AQS_LOCAL_SERVICES=auto uv run python scripts/local_services.py run --postgres --role-admin -- uv run pytest tests/db/test_*integrity.py tests/db/test_canonical_migration_structural_parity.py`
from `backend/`; the service wrapper and fixtures isolate the test schemas.
