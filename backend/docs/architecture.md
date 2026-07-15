# Risk Service Architecture

This service uses a small set of boundaries to keep API code thin and make future
domain logic easier to isolate.

## Layers

### API Layer

The API layer owns HTTP concerns:

- route paths and methods
- request parsing
- response schemas
- dependency resolution
- status codes exposed by FastAPI

API modules should not build SQL queries or mutate database models directly.
They should call service-layer functions and return response models.

Examples:

- `app/features/cases.py`
- `app/features/documents.py`
- `app/features/assessments.py`
- `app/features/findings.py`
- `app/features/manage_scenarios.py`
- `app/features/run_calculations.py`
- `app/features/manage_capital.py`
- `app/features/review_liquidity.py`

### Service Layer

The service layer owns application use cases. It coordinates persistence,
tenant context, audit events, storage clients, and transaction boundaries.

Examples:

- creating a risk case
- requesting a document upload
- completing an upload after object storage confirms the object exists
- creating a parse job and running the Phase 1 parser stub
- creating an assessment run and running the Phase 1 assessment stub
- updating a finding disposition
- managing scenario and assumption lifecycles
- creating, rerunning, and reading immutable calculation runs
- projecting capital indicators and generating evidence-backed findings
- calculating liquidity metrics and reviewing generated liquidity findings

Service modules may use SQLAlchemy sessions, models, and infrastructure clients.
They should keep all tenant-scoped lookups explicit and should not rely on entity
IDs alone.

Examples:

- `app/features/cases_service.py`
- `app/features/documents_service.py`
- `app/features/assessments_service.py`
- `app/features/findings_service.py`
- `app/features/jobs_service.py`
- `app/services/scenarios.py`
- `app/services/calculations.py`
- `app/services/capital.py`
- `app/services/liquidity.py`

### Domain Layer

The domain layer should hold business rules that are true regardless of FastAPI,
SQLAlchemy, Postgres, or S3.

Good candidates for the domain layer:

- document status transition rules
- assessment lifecycle rules
- risk taxonomy rules
- finding severity/status rules
- parser interfaces
- assessment engine interfaces
- future scoring logic
- deterministic balance-sheet projection rules
- deterministic liquidity metric and finding rules

Domain code should not depend on:

- FastAPI request or response objects
- SQLAlchemy sessions
- S3 clients
- Alembic migrations
- HTTP status codes

Phase 1 has only thin business rules, so most workflow logic currently lives in
the service layer. As real parsing, extraction, and scoring logic is added, move
reusable business rules into `app/domain/...` and keep services as orchestration
wrappers.

### Infrastructure Layer

The infrastructure layer owns concrete external systems.

Examples:

- SQLAlchemy database sessions
- Alembic migrations
- S3/MinIO object storage client
- future queues/workers
- future model or OCR clients

Infrastructure should be accessed through narrow abstractions when practical.
For example, document code should use the object storage abstraction rather than
calling boto3 directly.

## Tenant Isolation

Every tenant-owned table has `organization_id`, and application queries must
filter by `organization_id`.

The tenant dependency requires `X-Org-Id` and validates that the organization
exists. When `X-User-Id` is present, it must identify an active user in the same
organization. Invalid tenant context returns `401` before service code runs.

The API session dependency also sets Postgres RLS context with:

```sql
set_config('app.organization_id', '<organization uuid>', true)
```

Postgres row-level security is the hard safety net. Explicit service-layer
filters are still required for readability, index usage, and test compatibility.

## Request Flow

Typical request flow:

```text
FastAPI route
  -> dependency resolves TenantContext and tenant-aware DB session
  -> service function coordinates use case
  -> service performs tenant-scoped queries and mutations
  -> service records audit events for meaningful changes
  -> route returns response schema
```

## Core Data Concepts

### Risk Cases

`RiskCase` is the top-level review workspace. Most tenant-owned workflow data is
scoped to a case through `case_id`, including uploaded documents, assessments,
findings, financial workspace records, scenarios, calculation runs, and capital
projections.

### Documents

`Document` represents a file known to the service, usually an uploaded PDF,
spreadsheet, or other supporting artifact. The document row stores workflow
state and metadata such as tenant, case, filename, upload status, parse status,
and the backing object-storage reference.

The original file bytes live in object storage. The `Document` row is the
database handle used by API workflows.

### Document Chunks

`DocumentChunk` represents text extracted from a document during parsing. Chunks
are useful for review, search, scoring, and future model prompts. They are not
the canonical structured representation of a financial statement or table.

### Document Extractions

`DocumentExtraction` represents structured data extracted from a document. It is
linked to `Document` by `document_id` and stores the parser output in
`extracted_json`, along with `extraction_type`, `schema_version`, `status`,
confidence, error details, and creation time.

A document can have multiple extractions over time. For example, a later parser
version can create a new extraction while preserving the older one for audit and
reproducibility. Internal parser/extraction changes should use
`document_extractions.schema_version`; they do not require an HTTP API version
bump unless the external API contract changes.

### Financial Workspace Records

The financial workspace is the canonical, case-local representation of financial
data after mapping. It is intentionally separate from raw document extraction
payloads.

Canonical records include:

- `FinancialInstitution`
- `FinancialAccount`
- `FinancialReportingPeriod`
- `FinancialBalance`
- `FinancialCashFlow`
- `FinancialObligation`
- `FinancialCovenant`

These records are used by downstream review, validation, and future risk
analysis workflows. They should be created or reused using tenant- and
case-scoped lookups.

### Source Rows And Traceability

`FinancialSourceRow` stores each parsed row that the mapper considered. It keeps
the original row payload in `raw_payload`, plus row location details in
`locator`, so unknown or unmapped fields are not silently lost.

`FinancialRecordSourceLink` connects canonical financial records back to the
source row and field that produced them. Mapper-created links should include
field-level details such as `field_name` and `source_field` so reviewers can
trace a canonical value back to the originating extraction row.

### Financial Mapping Flow

The financial workspace mapper consumes completed `DocumentExtraction` rows and
maps supported structured payload shapes into canonical records:

```json
{ "rows": [{ "Bank": "Aequor Bank", "Balance": "250000", "Currency": "GHS" }] }
```

```json
{
  "tables": [
    {
      "rows": [
        { "Lender": "ABC Bank", "Committed": "1000000", "Drawn": "250000" }
      ]
    }
  ]
}
```

The mapper endpoint is:

```text
POST /api/v1/cases/{case_id}/financial-workspace/map
```

The request accepts exactly one of `document_id` or `document_extraction_id`.
When `document_id` is provided, the service uses the newest completed extraction
for that document. When `document_extraction_id` is provided, that extraction
must be completed and must belong to the requested tenant and case through its
document.

The response groups operational counts under `summary`, `created`, and `reused`.
Summary counts use source-row terminology, for example `source_row_count`,
`mapped_source_row_count`, and `unmapped_source_row_count`.

The mapper must preserve idempotency for repeated runs against the same
extraction and must preserve unmapped rows in `FinancialSourceRow`.

Rows containing covenant name, metric, comparison operator, and threshold are
also mapped into `FinancialCovenant`. Supported operators normalize to `lt`,
`lte`, `eq`, `gte`, and `gt`. Covenants retain the raw source record and
field-level source links, and may link to the obligation/facility and reporting
period mapped from the same row. When no valid source compliance status is
present, status is computed deterministically from the operator, threshold, and
actual value; a missing actual value produces `unknown`.

### Canonical Manual Entry And Correction

Manual creation and correction use resource-specific routes rather than a
generic entity mutation endpoint:

```text
POST  /api/v1/cases/{case_id}/financial-workspace/{resource}
PATCH /api/v1/cases/{case_id}/financial-workspace/{resource}/{record_id}
```

Supported resources are `institutions`, `accounts`, `reporting-periods`,
`balances`, `cash-flows`, `obligations`, and `covenants`. Request schemas
explicitly allowlist the writable fields for each resource and reject unknown
fields. Both `X-Org-Id` and an active, same-tenant `X-User-Id` are required.
Every create or update requires a non-empty `reason` explaining the manual
change.

Create operations mark record metadata with manual provenance. Corrections
preserve existing metadata and source links, mark the record as corrected, and
write one `FinancialManualEditHistory` row per changed field with the previous
value, new value, reason, actor, and timestamp. Creates and updates also emit
audit events. Tenant, case, and linked-record
ownership are checked before a write, so cross-case or cross-tenant references
are not accepted.

Each successful mutation returns:

```json
{
  "record": {},
  "validation": {
    "case_id": "...",
    "organization_id": "...",
    "issue_count": 0,
    "summary": { "total": 0, "error": 0, "warning": 0, "info": 0 },
    "issues": []
  }
}
```

Validation is refreshed after the write and the transaction commits only after
the record, history, audit event, and validation state are ready. Covenant
validation checks required identifying fields, recommends an obligation or
facility link, and reports an error if a supplied compliance status disagrees
with the deterministic comparison. The full workspace read includes covenants,
manual edit history, source links, validation issues, and validation summary.

### Scenarios And Structured Assumptions

`RiskScenario` represents an active or archived baseline, downside, or custom
case scenario. Baseline and downside initialization is idempotent for active
defaults and creates five system-provenance assumptions for each scenario:
growth, expenses, cash-flow timing, credit usage, and repayment behavior.
Custom scenarios start empty. Copying any active scenario creates a custom
scenario, preserves source identifiers in assumption provenance, and resets all
copied assumptions to `draft`.

The initialized defaults are:

| Assumption      | Unit    | Baseline | Downside |
| --------------- | ------- | -------: | -------: |
| Revenue growth  | `ratio` |      0.0 |     -0.1 |
| Expense growth  | `ratio` |      0.0 |      0.1 |
| Cash-flow delay | `days`  |        0 |       30 |
| Credit usage    | `ratio` |      0.0 |      0.8 |
| Repayment rate  | `ratio` |      1.0 |      0.5 |

`ScenarioAssumption` stores a typed JSON scalar value, unit, provenance, and
review state. Assumption keys are unique within a scenario. Manual creation,
editing, and review require a non-empty reason and an active same-tenant user.
An edit records reviewer-edit provenance and clears the previous reviewer and
review timestamp, so the changed value must be reviewed again. Each assumption
mutation writes `ScenarioAssumptionHistory`; scenario and assumption mutations
also emit audit events in the same transaction.

Scenario resources use these routes:

```text
GET   /api/v1/cases/{case_id}/scenarios
POST  /api/v1/cases/{case_id}/scenarios/initialize
POST  /api/v1/cases/{case_id}/scenarios
GET   /api/v1/cases/{case_id}/scenarios/readiness
GET   /api/v1/cases/{case_id}/scenarios/{scenario_id}
PATCH /api/v1/cases/{case_id}/scenarios/{scenario_id}
POST  /api/v1/cases/{case_id}/scenarios/{scenario_id}/copy
POST  /api/v1/cases/{case_id}/scenarios/{scenario_id}/archive
GET   /api/v1/cases/{case_id}/scenarios/{scenario_id}/validation
POST  /api/v1/cases/{case_id}/scenarios/{scenario_id}/assumptions
PATCH /api/v1/cases/{case_id}/scenarios/{scenario_id}/assumptions/{assumption_id}
POST  /api/v1/cases/{case_id}/scenarios/{scenario_id}/assumptions/{assumption_id}/review
```

List requests omit archived scenarios unless `include_archived=true`; archived
scenarios remain readable but are immutable. Scenario validation requires at
least one non-null assumption in every required category and requires every
assumption, including `other` assumptions, to be reviewed. Case calculation
readiness is true only when at least one active scenario exists and every active
scenario passes validation. Mutation responses contain the updated scenario,
its refreshed validation, and refreshed case readiness.

### Calculation Runs And Forecast Outputs

`CalculationRun` is an immutable, tenant- and case-scoped record of one
balance-sheet forecast attempt. It stores lifecycle status, the scenario and
optional source-run relationship, the requested horizon and as-of date, engine
and schema versions, the canonical input snapshot, its SHA-256 hash, actor and
timestamps, and persisted error diagnostics. Successful annual projections are
stored as `CalculationForecastPeriod` rows. PostgreSQL RLS protects both tables,
and service queries still filter explicitly by organization and case.

Calculation resources use these routes:

```text
GET  /api/v1/cases/{case_id}/calculation-runs
POST /api/v1/cases/{case_id}/calculation-runs
GET  /api/v1/cases/{case_id}/calculation-runs/{run_id}
POST /api/v1/cases/{case_id}/calculation-runs/{run_id}/rerun
```

The list route returns newest-first summaries and supports `scenario_id`,
`active_scenarios_only`, `limit` (1-100), and `offset`. It also returns
`latest_successful_run_id` for the selected case and optional scenario filter.
When active-only filtering is enabled, archived scenarios are excluded and
`latest_successful_runs_by_scenario` returns one successful run per active
scenario using the same pagination. Fetch an individual run to read the full
immutable snapshot and output periods. Starting and rerunning require an active
same-tenant actor. A rerun appends a new row linked through
`rerun_of_run_id`; it uses the original scenario with current canonical inputs
and reviewed assumptions. An empty rerun body reuses the original horizon and
defaults the as-of date to today; either value can be supplied explicitly.

The first engine is synchronous. It commits `queued`, then `running`, before
opening a repeatable-read snapshot transaction. Success and failure are both
persisted and returned with `201`; a failed attempt does not replace the latest
successful output. Lifecycle and input-snapshot establishment or rejection emit
audit events. Unexpected failures expose a sanitized diagnostic rather than the
underlying exception.

Input assembly selects the latest effective balance date on or before the
requested as-of date. When those balances identify one reporting period,
cash-flow and active-obligation inputs are selected from that period; otherwise
the latest eligible dated records are used. Active obligations must provide
principal and outstanding amounts, every selected financial input must use the
same currency, balance types must be classifiable, and the scenario must resolve
to one reviewed value in each required category.

For each annual period, the deterministic engine applies revenue growth and
cash-flow delay to inflows, expense growth to outflows, credit usage to the
initial available draw, and repayment behavior to scheduled debt repayment.
It persists assets, liabilities, equity, cash, inflows, outflows, credit draw,
debt repayment, and component details at four-decimal precision. The stored
engine, input-schema, and output-schema versions make later engine changes
distinguishable from reruns with changed canonical data.

### Capital Projections And Findings

`CapitalProjection` is an immutable, tenant- and case-scoped attempt to derive
capital pressure from one successful `CalculationRun`. It copies the source
run's scenario, SHA-256 input hash, and reporting currency, records its own
engine version and lifecycle, and persists successful period results as
`CapitalIndicator` rows. `CapitalProjectionFinding` links generated
`RiskFinding` rows to the projection that produced them. PostgreSQL RLS protects
all three capital tables, while service queries also scope by organization and
case.

Capital resources use these routes:

```text
GET  /api/v1/cases/{case_id}/capital-projections
POST /api/v1/cases/{case_id}/capital-projections
GET  /api/v1/cases/{case_id}/capital-projections/{projection_id}
GET  /api/v1/cases/{case_id}/capital-summary
GET  /api/v1/cases/{case_id}/capital-comparison
```

Creating a projection requires an active same-tenant actor and a successful
calculation run whose case and active scenario match the request. The attempt
derives equity, equity-to-assets, liabilities-to-assets, and equity change for
each immutable forecast period. Monetary values are rounded half-up to four
decimal places and ratios to eight decimal places before persistence,
classification, and finding generation. Pressure is `critical` for negative
equity, `high` below a 10 percent equity-to-assets ratio, `medium` below 20
percent or when equity declines, and `low` otherwise. Non-positive projected
assets, missing opening-balance evidence, and numeric overflow persist a failed
attempt with the affected forecast-period identifiers and corrective details.

Successful attempts can generate deterministic negative-equity, thin-buffer,
and final-period erosion findings. Finding details and
`RiskFindingEvidence.locator` trace the projection, calculation run, scenario,
input hash, indicator, and forecast period. A new successful projection marks
only unreviewed findings from older projections for the same scenario as
superseded; reviewed findings remain unchanged.

Reviewing a finding through `PATCH /api/v1/findings/{finding_id}` requires an
active same-tenant actor and a non-archived case. The request must change status
or disposition reason, dismissals require a reason, and successful reviews
record the reviewer and timestamp so later projections preserve the reviewed
finding.

The projection list is newest-first and supports `limit` (1-100) and `offset`.
The summary returns the latest successful projection, optionally filtered by
`scenario_id`. The comparison selects the latest successful projections for
active baseline and downside scenarios and returns period equity and ratio
deltas only when as-of date, reporting currency, and forecast horizon match;
otherwise it returns a named basis-mismatch diagnostic and corrective action.
List, detail, and summary reads preserve archived scenario and case history.
Archived scenarios reject new projections; archived cases reject new
projections, comparisons, and finding reviews. Comparisons omit archived
scenarios.

### Liquidity Analysis And Findings

Every successful calculation run creates one immutable
`LiquidityAnalysisResult`, versioned independently from the forecast engine and
linked to the run through a tenant- and case-scoped foreign key. The stored
result contains five deterministic metrics: minimum cash balance, peak
liquidity gap, minimum sources coverage, credit reliance, and cash runway.
Sources coverage is inflows plus credit draws divided by outflows plus debt
repayment; credit reliance is total credit draws divided by those total uses.
If any forecast period has non-positive uses, both ratio metrics are marked
unavailable with an explicit diagnostic instead of substituting a value.

Version `liquidity-v1.0.0` publishes findings for three conditions:

| Rule                         | Condition                                | Severity                                 |
| ---------------------------- | ---------------------------------------- | ---------------------------------------- |
| `liquidity.negative_cash`    | Projected cash falls below zero          | `critical` in period 1; otherwise `high` |
| `liquidity.sources_coverage` | Minimum sources coverage is below 1.20x  | `high` below 1.00x; otherwise `medium`   |
| `liquidity.credit_reliance`  | Credit funds more than 25% of total uses | `high` above 50%; otherwise `medium`     |

Each finding stores its calculation run, scenario, rule version, input hash, and
contributing metric snapshot. `RiskFindingEvidence` rows link it to the relevant
forecast periods, canonical reporting period, balances, cash flows,
obligations, and reviewed scenario assumptions. Evidence locators carry the
same input hash and provide case-workspace deep links. Finding publication is
serialized per tenant, case, and scenario; a newer successful run supersedes
only prior open or needs-review liquidity findings for that scenario.

Liquidity resources use these routes:

```text
GET  /api/v1/cases/{case_id}/liquidity/summary
POST /api/v1/cases/{case_id}/liquidity/findings/{finding_id}/review
```

The summary route accepts optional `scenario_id` and `run_id`. It selects the
newest matching successful run and returns `not_calculated` when no persisted
analysis exists, including for successful runs created before liquidity
analysis was introduced. Findings are returned in severity order. The review
route accepts `acknowledge` or `dismiss`, requires an active same-tenant user,
and requires a non-empty reason for dismissal. Only open or needs-review
findings on active scenarios can be reviewed; reviewed, superseded, and archived
scenario findings are read-only. Reviews and automatic publication or
supersession emit audit events. Liquidity workflow findings are also protected
from mutation through the generic findings update route.

## API Versioning

The service uses URL path major versioning for HTTP contracts.

- Current business API: `/api/v1`
- Health checks remain outside the business version at `/api/health/...`
- Backward-compatible additions stay in the current major version.
- Breaking request or response contract changes require a new major path, such
  as `/api/v2`.

Examples of compatible `v1` changes:

- adding optional request fields
- adding response fields
- adding endpoints
- adding enum values that clients are expected to tolerate

Examples of breaking changes:

- removing or renaming fields
- changing the meaning of an existing field
- making optional fields required
- changing status-code semantics
- changing pagination or filter behavior incompatibly

Internal artifact versions are tracked separately from the HTTP API. Parser,
extraction, prompt, assessment engine, and calculation engine changes should
use fields such as
`document_extractions.schema_version`, `risk_assessment_runs.engine_version`,
`risk_assessment_runs.prompt_version`, and the calculation run's engine,
input-schema, and output-schema versions. Those internal versions do not imply
an API version bump unless the external HTTP contract changes.

## Background Jobs

Phase 1 creates job records but runs parse and assessment stubs synchronously in
process. This keeps tests deterministic and avoids adding queue infrastructure
before real OCR, model calls, or long-running scoring logic exists.

Future worker-backed behavior should preserve the same API contract:

```text
endpoint creates queued job
endpoint returns job ID immediately
worker updates running/completed/failed
client polls /api/v1/jobs/{job_id}
```
