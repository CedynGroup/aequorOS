# Converting seed-dependent tests to the real-DB pattern (Q06 completion)

**Decision (founder, 2026-08-15):** tests use data from the database, not `data/` and not a
seed. The retired `POST /banks/seed-demo` route is gone; ~21 test files still call it and fail
at setup with `405`. Each is converted to run against the ACTUAL primary via the `real_client`
foundation — opt-in, transaction-isolated (rolled back on teardown; prod is never mutated).

## The pattern (reference conversion: `backend/tests/api/test_regulatory_liquidity.py`)

- `from tests.real_data import REAL_BANK_ID, REAL_ORG_ID, REAL_USER_ID, real_headers,
  other_headers, requires_real_data`; module-level `pytestmark = requires_real_data`.
- Fixtures: `real_client` (TestClient wired to the primary through ONE connection whose outer
  transaction is rolled back — reads see real data, writes work inside the test, nothing commits)
  and `real_session` (a DB session sharing that transaction; set
  `session.info["organization_id"] = REAL_ORG_ID` before touching RLS tables). Defined in
  `tests/conftest.py`.
- Replace `_seed…(db_client)` / `POST /banks/seed-demo` with reads of the real bank: latest period
  via `GET /api/v1/banks/{REAL_BANK_ID}/reporting-periods`, real users via `real_headers(...)`,
  cross-tenant isolation via `other_headers()` (Horizon Bank's ACTIVE admin — authenticates, RLS
  hides Sample Bank ⇒ 404; the isolated org's only user is inactive ⇒ 401, don't use it).
- **Assertions become invariants and relationships, never frozen golden magnitudes** (the real
  book changes as data is ingested): ratio = numerator/denominator, statuses consistent with
  thresholds, sections populated, determinism (identical rerun → identical input_hash), tenant
  isolation, 404/422 paths. Where the old test pinned a seed number, assert the *relationship*
  that number was demonstrating.
- Mutation tests are fine (they roll back): create connections/keys/packages/runs, then assert.
  Anything the old test did by direct DB write, do via `real_session` on the shared transaction.
- One real official run takes ~30 s (570k positions over a ~40 ms link) — prefer the inline
  dashboards / previews where the test's point isn't the run itself; keep the number of official
  runs per test file low.
- Run: `REAL_DATA_DATABASE_URL="$(uv run python -c "from app.core.config import get_settings;
  print(get_settings().database.database_url)")" uv run pytest <file> -q -p no:cacheprovider`
  (the tenant-scoped app-role URL — RLS enforced). Without the env var the file skips (hermetic CI
  stays green). Never point a mutating test at the primary outside `real_client`.

## Deliverable per file
The converted file (same path), every test kept (rewritten, not deleted) unless it tested the
seed route itself; a two-line header docstring naming the invariants; gates: ruff + basedpyright
clean, the file green under `REAL_DATA_DATABASE_URL`, and still collectable/skipping without it.
**Do not commit.** Report: file, tests converted/kept/dropped (why), invariants asserted, runtime.
