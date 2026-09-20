# Authorization property negative controls

Coverage, negative-control contracts, and known gaps are owned by
[Authorization foundation: Executable verification](../docs/authorization_foundation.md#executable-verification).
This document records the executed mutation-proof evidence.

## Focused execution

2026-09-18, Python 3.13.13, disposable local PostgreSQL 17.11:

```sh
TEST_DATABASE_URL=<disposable-postgres-url> uv run pytest \
  tests/api/test_authorization_state_machine.py \
  tests/db/test_authorization_tenant_isolation_properties.py -q
```

Initial result: 11 passed, 1 failed, 1 xfailed (183.28s). The RLS negative control
passed, as did the generated route property with tenant-B positive controls and
both state machines. The strict route-404 xfail remained expected. The initial
ownership negative control attempted a second owner, which the database's unique
owner constraint correctly rejected before the invariant could run. The corrected
control uses an unassigned organization, keeping that independent guard intact.
Focused correction verification:

```sh
TEST_DATABASE_URL=<disposable-postgres-url> uv run pytest \
  tests/api/test_authorization_state_machine.py -k ownership -q
```

Result: **2 passed, 8 deselected** (7.76s), including
`test_ownership_invariant_detects_public_owner_grant_mutation` and the ownership
state machine. The negative control observed the expected invariant failure
under the patch, then passed the invariant and normal refusal after restoration.
Both runs emitted the existing Starlette/httpx deprecation warning.

These executions cover both mutation proofs; the first run also executed
`test_current_fact_isolation_detects_no_force_rls_mutation`, including successful
isolation checks before weakening and after rollback. The disposable server was
stopped and its worktree-local data removed. No product code was changed.
