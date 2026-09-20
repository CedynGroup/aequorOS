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

## Object-reference (IDOR) tests

These recorded executions predate the single-foreign-child layout and the
deterministic per-table content hashes; their counts do not certify those later
changes. The current coverage contract is owned by
[the foundation document](../docs/authorization_foundation.md#executable-verification).

2026-09-19, Python 3.13. The deterministic coverage layer runs on the default
SQLite database; the generative layer runs against a disposable local
PostgreSQL 17 with the NOBYPASSRLS `risk_service_test` role (the CI role),
schema migrated to head.

```sh
uv run pytest tests/api/test_authorization_object_reference_coverage.py -q
TEST_DATABASE_URL=<disposable-postgres-url> uv run pytest \
  tests/db/test_authorization_object_reference_properties.py -q
```

Coverage layer result: **341 passed, 2 skipped** (~12s). The parametrization
enumerated the object-reference routes from the FastAPI registry and, for a
fully entitled bank-A caller, confirmed every cross-organization and
sibling-bank foreign reference is refused with no leak and no inserted row; the
two skips are the quarantined system-of-record routes.
`test_known_defects_are_still_reproduced` confirmed both defects still leak, and
`test_seeded_objects_exist_for_their_owners` proved the objects exist for their
owners.

Generative layer result: **2 passed** (~61s). `test_generated_callers_never_reach_foreign_objects`
ran 15 Hypothesis examples, each replacing bank A's bindings with a generated
set that varies bundle, module scope, sensitivity scope, institution scope and
lifecycle state, then sweeping the census under the generated layout and
asserting no leak, no accepted mutation, and no table-digest change in either
organization. The committed negative control
`test_object_reference_property_detects_weakened_package_guard` weakened
`regulatory_reporting.common.get_package_or_404` to ignore the path bank under a
rolled-back `monkeypatch` (no product code changed): the sweep then reported the
sibling-bank package leak, and passed once the patch was undone.

The deterministic layer reproduced the two same-organization cross-bank defects,
held in `object_reference_routes.KNOWN_DEFECTS` and asserted as still-defective
(a fix must promote them):
`POST /banks/{bank_id}/system-of-record/{declaration_id}/approve` and `.../revoke`
resolved the declaration by id within the organization only
(`system_of_record.get_declaration` ignored `bank_id`), so a caller bound to
bank A approved/revoked a sibling bank's declaration — a 200 that returned the
sibling bank's identifier and mutated its row. Recorded for the product owner;
no product code was changed in that test-only change. The disposable server was
stopped and its worktree-local data removed afterwards.

### Fix verification

2026-09-19, Python 3.13, disposable local PostgreSQL 17 with the same
NOBYPASSRLS `risk_service_test` role, schema migrated to head. The fix scopes
`system_of_record.get_declaration` (hence `approve`/`revoke`) and
`canonical_withdrawal.get_withdrawal` (hence withdrawal approve/reverse) by the
route's bank at the query, and makes a withdrawal request resolve any cited
`declaration_id` against the same bank-scoped lookup. A sibling bank's row now
gets the route's ordinary not-found shape before any state check, so the
refusal has no side effects and reveals nothing the caller did not send.

Order of evidence:

1. Unfixed base, quarantine in place: both routes still reproduced as
   defective (`test_known_defects_are_still_reproduced` passed).
2. Fix applied, quarantine still in place — `-k known_defects` on the coverage
   layer: **1 failed** (5.17s) on the promotion assertion, naming both routes
   (`documented object-reference defects are no longer reproduced; remove them
   from KNOWN_DEFECTS`), as the layer is designed to force.
3. Fix applied, `KNOWN_DEFECTS` emptied — coverage layer: **400 passed**
   (34.63s; the two former skips now run in the strict parametrization, with
   the nested single-foreign-child cases included); generative layer against
   the disposable Postgres: **2 passed** (46.73s).

The focused hermetic regression `tests/api/test_system_of_record_bank_scope.py`
(three tests: declaration approve/revoke, withdrawal approve/reverse, and a
withdrawal citing a foreign declaration; each with row-state, audit-event and
job-queue side-effect checks, a foreign-vs-unknown-id 404 shape comparison, and
an owning-bank positive control) failed **3/3** against the unfixed service and
passed **3/3** with the fix. The disposable server was stopped and its data
removed afterwards.
