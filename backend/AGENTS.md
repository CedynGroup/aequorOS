# Risk Service Agent Guide

This file is the starting point for agents working in `backend`.

## Important Documents

| Document                                           | Purpose                                                                          |
| -------------------------------------------------- | -------------------------------------------------------------------------------- |
| [README.md](README.md)                             | Local setup, run commands, environment variables, and API authentication.        |
| [../ARCHITECTURE.md](../ARCHITECTURE.md)           | Service boundaries, tenant isolation, API versioning, and background job policy. |
| [docker-compose.yml](docker-compose.yml)           | Local Postgres and MinIO services for demos and Postgres-backed tests.           |
| [scripts/bootstrap_db.sh](scripts/bootstrap_db.sh) | Database role setup, migrations, grants, and demo tenant seeding.                |
| [alembic/versions](alembic/versions)               | Schema migrations, including Phase 1 tables and RLS policies.                    |
| [tests](tests)                                     | API, service, schema-default, tenant isolation, and health regression coverage.  |

## Working Guidelines

- Keep business routes under `/api/v1`; health routes stay under `/api/health`.
- Keep route handlers thin. Put orchestration and mutations in service modules.
- Scope every tenant-owned query by `organization_id`, even with Postgres RLS
  enabled.
- Use the storage abstraction in `app/integrations/storage`; do not call boto3
  from feature code.
- Preserve deterministic in-process job stubs until worker infrastructure is introduced.
- Add regression tests for tenant isolation, state transitions, and
  storage/database edge cases.
- Canonical financial manual entry and correction use resource-specific
  `/api/v1/cases/{case_id}/financial-workspace/*` routes. Keep their OpenAPI and
  generated-client contracts aligned; successful mutations return the record plus
  refreshed validation and persist per-field history with actor and reason.
- Scenario mutations use resource-specific `/api/v1/cases/{case_id}/scenarios/*`
  routes. Keep assumption history, audit events, validation, and readiness in the
  same transaction; editing an assumption resets it to draft until reviewed.
- Calculation runs use `/api/v1/cases/{case_id}/calculation-runs`; persist the
  complete deterministic input snapshot and SHA-256 input hash with engine,
  input-schema, and output-schema versions. Reruns must append history and never
  replace prior successful forecast periods.
- Commit queued and running lifecycle states before synchronous execution. Build
  each financial snapshot in a repeatable-read transaction from one effective
  reporting period, and return paginated summaries from history endpoints.
- Capital projection attempts use `/api/v1/cases/{case_id}/capital-projections`
  and must consume successful immutable calculation runs. Preserve projection,
  indicator, generated-finding, evidence, and failed-diagnostic history; compare
  only matching baseline and downside forecast bases.
- Generate liquidity metrics and findings in the successful calculation transaction. Persist
  evidence through the shared tenant-scoped finding tables, and bind every evidence locator to the
  calculation run and immutable input hash.

## Legacy case vertical

- Scenario resources live under `/api/v1/cases/{case_id}/scenarios`. Calculation
  readiness requires every active scenario to contain growth, expenses,
  cash-flow timing, credit-usage, and repayment-behavior assumptions, with each
  assumption explicitly reviewed after its latest edit.
- The case-based financial-review UI lived in the removed `aequoros-web` SPA
  (see git history). If that vertical returns in `backend/dashboard`, it must
  call `FinancialDataApi` from `packages/risk-service-api`; do not duplicate
  OpenAPI payloads or hand-roll financial workspace requests.
- Canonical institution, account, reporting-period, balance, cash-flow, obligation, and covenant
  mutations require a non-empty reason and return the record plus refreshed validation. Their
  review forms support manual entry and correction through the generated contracts.
- Constrain account and obligation statuses to generated contract values;
  automatic covenant compliance recalculation must omit `complianceStatus` so
  the backend derives it from the covenant inputs.
- Balance-sheet forecast attempts live under `/api/v1/cases/{case_id}/calculation-runs`.
  Runs are immutable snapshots: reruns create a new row with current canonical
  financial data and reviewed scenario assumptions, while prior successful
  outputs and failed-run diagnostics remain available.
- Forecast snapshots use the latest effective balance reporting period on or
  before the requested as-of date. Only active obligations participate, and
  active obligations require both principal and outstanding amounts.
- Calculation history endpoints return paginated run summaries; fetch a run by
  ID for its immutable input snapshot and forecast outputs.
- Capital projection attempts live under `/api/v1/cases/{case_id}/capital-projections`
  and consume a successful calculation run. They persist period indicators and
  generated case findings with calculation-run, forecast-period, and input-hash evidence.
- Capital summaries return the latest successful projection, while
  `/capital-comparison` pairs the latest baseline and downside projections by period.
  The MVP pressure rules use equity-to-assets, liabilities-to-assets, and equity change;
  non-positive projected assets fail with named forecast-period diagnostics.
- Successful forecast runs automatically calculate deterministic liquidity metrics and generate
  tenant-scoped liquidity findings. Liquidity evidence locators bind forecast periods, canonical
  inputs, and reviewed scenario assumptions to the calculation input hash.
- Liquidity summaries and acknowledge/dismiss review actions live under
  `/api/v1/cases/{case_id}/liquidity`; reuse the shared case-finding review card in SPA analysis
  verticals.

## Commit Messages

Use conventional commits with `risk-service` as the scope:

```text
feat(risk-service): add tenant-scoped risk persistence and RLS

Refs AEQ-6
```

Keep Linear ticket IDs out of the scope and subject unless there is a repo-wide
reason to do otherwise. Put the ticket reference in the commit body or footer.

## Common Commands

```bash
mise run risk-service:test
mise run risk-service:test-postgres
mise run risk-service:check
mise run risk-service:bootstrap-db
```
