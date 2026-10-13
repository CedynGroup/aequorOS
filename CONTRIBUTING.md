# Contributing to AequorOS

AequorOS is proprietary source-available software (see [LICENSE](LICENSE)).
External contributions are accepted by invitation; issues and security reports
are always welcome.

## Reporting

- **Bugs/ideas**: open a GitHub issue with reproduction steps or context.
- **Security**: never a public issue — see [SECURITY.md](SECURITY.md).

## Development conventions (for invited contributors)

- Read [ARCHITECTURE.md](ARCHITECTURE.md) and
  [CODEBASE_CONVENTIONS.md](CODEBASE_CONVENTIONS.md) first — they are the law of
  the repo (tenancy/RLS patterns, immutable calculation runs, adapter
  boundaries, design tokens).
- Backend gates: `ruff check`, the basedpyright strict
  [type-check baseline](CODEBASE_CONVENTIONS.md#type-check-baseline), and
  `CASHFLOW_FAST_TEST=1 pytest` must be green; tests are hermetic (no ambient
  database) and Postgres-gated tests opt in via `TEST_DATABASE_URL`.
- Backend database fixture isolation and parallel execution are documented in the
  [backend test database guide](backend/README.md#test-databases-and-the-primary-database).
  `db_client` and `db_session` are the defaults.
  Mark a test with `@pytest.mark.committing_db` when real commits are required
  for DDL, independent connections, raw commit visibility, query-count
  transaction boundaries, or concurrency/lock behavior. Tests under `tests/db/`
  select the marker automatically. The executable contract for both families
  and for safe application reuse is
  `tests/test_database_fixture_isolation.py`.
- Dashboard gates: `tsc --noEmit`; both dark and light themes must render (no
  raw hex — use the token classes).
- Commits follow Conventional Commits (`feat(scope): …`).
- Use the [pull request description template](.github/PULL_REQUEST_TEMPLATE.md) for every PR.
- Never commit credentials; `.env` is untracked by design and CI runs secret
  scanning on every push.
