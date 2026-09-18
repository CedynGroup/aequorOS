# Authorization property negative controls

The ownership negative control admits only `RoleBundle.ORG_OWNER` through a
test-scoped patch of `validate_public_grant`. It executes `create_scoped_grant`
with the real authority sentence, then requires the state machine's shared
ownership invariant to reject an owner in an unassigned organization. Rolling back the grant
and restoring validation must restore the invariant and the normal refusal.

The Postgres negative control first checks that each tenant sees exactly its own
current fact. It executes `ALTER TABLE current_financial_facts NO FORCE ROW LEVEL
SECURITY` in a transaction and requires that same isolation assertion to fail.
The transaction always rolls back; the assertion must then pass again. Neither
negative control changes product code.

The generated route proof now addresses a persisted tenant-B reporting period
and financial fact, with a tenant-B HTTP 200 positive control asserting the exact
returned IDs and category before tenant-A requests. Other UUID resource kinds
still use unknown IDs and do not prove isolation of existing child resources.
The strict `aeq-rbac-cross-tenant-route-404` expected failure remains intact.
Ownership transfer and owner deactivation still lack a product API to exercise.

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
