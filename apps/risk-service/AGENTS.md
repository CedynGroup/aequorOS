# Risk Service Agent Guide

This file is the starting point for agents working in `apps/risk-service`.

## Important Documents

| Document                                           | Purpose                                                                          |
| -------------------------------------------------- | -------------------------------------------------------------------------------- |
| [README.md](README.md)                             | Local setup, run commands, environment variables, and demo tenant headers.       |
| [docs/architecture.md](docs/architecture.md)       | Service boundaries, tenant isolation, API versioning, and background job policy. |
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
mise run test
mise run test-postgres
mise run check
mise run bootstrap-db
```
