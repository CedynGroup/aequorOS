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
- Backend gates: `ruff check`, `basedpyright`, and
  `CASHFLOW_FAST_TEST=1 pytest` must be green; tests are hermetic (no ambient
  database) and Postgres-gated tests opt in via `TEST_DATABASE_URL`.
- Dashboard gates: `tsc --noEmit`; both dark and light themes must render (no
  raw hex — use the token classes).
- Commits follow Conventional Commits (`feat(scope): …`).
- Never commit credentials; `.env` is untracked by design and CI runs secret
  scanning on every push.
