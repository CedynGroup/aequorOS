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

Service modules may use SQLAlchemy sessions, models, and infrastructure clients.
They should keep all tenant-scoped lookups explicit and should not rely on entity
IDs alone.

Examples:

- `app/features/cases_service.py`
- `app/features/documents_service.py`
- `app/features/assessments_service.py`
- `app/features/findings_service.py`
- `app/features/jobs_service.py`

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
extraction, prompt, and assessment engine changes should use fields such as
`document_extractions.schema_version`, `risk_assessment_runs.engine_version`,
and `risk_assessment_runs.prompt_version`. Those internal versions do not imply
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
