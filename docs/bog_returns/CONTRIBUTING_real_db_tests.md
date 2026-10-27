# Converting seed-dependent tests to the real-DB pattern (Q06 completion)

The retired `POST /banks/seed-demo` route is gone. Current fixture selection and
primary-database restrictions are owned by the
[backend test database guide](../../backend/README.md#test-databases-and-the-primary-database).
Connection construction and ownership are documented in the
[test connection guide](../../backend/tests/support/DATABASE.md).
Those guides supersede this conversion recipe's former primary-database directions.

## The pattern (reference conversion: `backend/tests/api/test_regulatory_liquidity.py`)

- `from tests.real_data import REAL_BANK_ID, REAL_ORG_ID, REAL_USER_ID, real_headers,
other_headers, requires_real_data`; module-level `pytestmark = requires_real_data`.
- Legacy fixture ownership and validation are described in the
  [test connection guide](../../backend/tests/support/DATABASE.md).
- Replace `_seed…(db_client)` / `POST /banks/seed-demo` with reads of the real bank: latest period
  via `GET /api/v1/banks/{REAL_BANK_ID}/reporting-periods`, real users via `real_headers(...)`,
  cross-tenant isolation via `other_headers()` (Horizon Bank's ACTIVE admin — authenticates, RLS
  hides Sample Bank ⇒ 404; the isolated org's only user is inactive ⇒ 401, don't use it).
- **Assertions become invariants and relationships, never frozen golden magnitudes** (the real
  book changes as data is ingested): ratio = numerator/denominator, statuses consistent with
  thresholds, sections populated, determinism (identical rerun → identical `input_hash`), tenant
  isolation, 404/422 paths. Where the old test pinned a seed number, assert the _relationship_
  that number was demonstrating.
- One real official run takes ~30 s (570k positions over a ~40 ms link) — prefer the inline
  dashboards / previews where the test's point isn't the run itself; keep the number of official
  runs per test file low.
- Run commands and database selection follow the
  [backend test database guide](../../backend/README.md#test-databases-and-the-primary-database).

## Deliverable per file

The converted file (same path), every test kept (rewritten, not deleted) unless it tested the
seed route itself; a two-line header docstring naming the invariants; gates: ruff clean, the type-check
baseline (`uv run python scripts/type_check_baseline.py check`) clean, the file green under
`REAL_DATA_DATABASE_URL`, and still collectable/skipping without it.
**Do not commit.** Report: file, tests converted/kept/dropped (why), invariants asserted, runtime.
