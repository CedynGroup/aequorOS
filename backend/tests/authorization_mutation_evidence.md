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

## Object-reference (IDOR) property

2026-09-19, Python 3.13, disposable local PostgreSQL 17 with the NOBYPASSRLS
`risk_service_test` role (the CI role), schema migrated to head:

```sh
TEST_DATABASE_URL=<disposable-postgres-url> uv run pytest \
  tests/db/test_authorization_object_reference_properties.py -q
```

Result: **3 passed** (~60s total). `test_foreign_object_references_are_refused_without_side_effects`
enumerated 219 object-reference routes from the FastAPI registry, exercised 378
foreign-reference requests across the cross-organization and sibling-bank
layouts with a fully entitled bank-A caller, and confirmed no foreign identifier
leaked, no mutation returned 2xx, no table digest in either organization moved,
and every seeded object was visible to its owning tenant (the data-layer
positive control). `test_generated_bank_a_callers_never_reach_foreign_objects`
repeated the sweep for 15 generated binding sets with the same result.

The committed negative control,
`test_object_reference_property_detects_weakened_package_guard`, weakened
`regulatory_reporting.common.get_package_or_404` to ignore the path bank under a
`monkeypatch` context (rolled back, no product code changed): the sweep then
reported the sibling-bank package leak, and the same sweep passed once the patch
was undone.

The property reproduced two genuine same-organization cross-bank defects, held
in `_KNOWN_DEFECTS` and asserted as still-defective (a fix must promote them):
`POST /banks/{bank_id}/system-of-record/{declaration_id}/approve` and
`.../revoke` resolve the declaration by id within the organization only
(`system_of_record.get_declaration` ignores `bank_id`), so a caller bound to
bank A approved/revoked a sibling bank's declaration — a 200 that returned the
sibling bank's identifier and mutated its row. Recorded for the product owner;
no product code was changed in this test-only change. The disposable server was
stopped and its data removed afterwards.
