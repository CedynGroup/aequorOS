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
contains three tests covering declaration approve/revoke, withdrawal approval
of pending and applied sibling rows, withdrawal reversal, and a withdrawal
request citing a foreign declaration. Every foreign refusal checks unchanged
row state, object audit-event and tenant job-queue counts, and compares its
404 envelope with the same route's response to a fresh unknown UUID, excluding
request_id and normalizing the caller-supplied id. The citation test also checks
that no withdrawal was created. Positive controls approve and revoke declarations
under the sibling's own path, approve and reverse a pending withdrawal against
the sample bank's duplicate book, retain the sibling's owning-path 409 on
reapproval, and accept the sample bank's own declaration as a citation.
The original three-test regression failed **3/3** against the unfixed service
and passed **3/3** with the fix; the response comparisons and successful
withdrawal lifecycle controls were completed during review. The disposable
server was stopped and its data removed afterwards.

Re-verified 2026-09-21 after rebasing onto `main` with the #230 filing chain and
the #233 census repair: census guard, focused regression and service suites
**23 passed**; coverage layer **433 passed, 0 skipped** (the two former
system-of-record skips now run in the strict parametrization); generative layer
on the disposable Postgres `risk_service_test` role **2 passed** (36.9s).

## Object-reference census after the filing chain (#230)

2026-09-20, Python 3.13, disposable local PostgreSQL (Docker, `postgres` role)
migrated to head `202609200065`. PR #226 (the census) and PR #230 (the filing
chain, ICAAP workspace and dynamic attestation) were each green on their own
base; on main together the census failed at collection with 126 object
identifiers across 91 routes that had no catalogue entry, so both suites
errored before running a case.

```sh
uv run pytest tests/architecture/test_object_reference_census.py -q
uv run pytest tests/api/test_authorization_object_reference_coverage.py -q
TEST_DATABASE_URL=<disposable-postgres-url> uv run pytest \
  tests/db/test_authorization_object_reference_properties.py -q
```

Catalogued with seeded fixtures: `filing_workflow_template`
(`/banks/{bank_id}/filing-workflow-templates/{template_id}` PATCH, `/submit`,
`/decision`) and `package_attachment`
(`/regulatory-packages/{package_id}/attachments/{attachment_id}/download` and
`/withdraw`). The filing-chain routes under `/regulatory-packages/{package_id}/workflow`
and the attachment list/upload routes reference only `package_id` and joined the
census through the existing `package` kind. Every ICAAP route carrying an object
identifier (86 routes) is listed one by one in `ICAAP_DEFERRED`, folded into
`KNOWN_UNCOVERED`, under the captain's 2026-09-20 deferral of ICAAP. The ongoing
[catalogue and deferral contract](../docs/authorization_foundation.md#executable-verification)
owns the requirements for extending coverage.

Guard result: **3 passed** (~24s). `test_every_object_reference_is_catalogued`
resolved the census on the live route table; the two companion checks confirmed
every `KNOWN_UNCOVERED` and `KNOWN_DEFECTS` entry still names a mounted route.

Coverage layer result: **431 passed, 2 skipped** (223s; 341 before #230). Every
newly enumerated filing-chain, template and attachment case was refused with
404 on the cross-organization, sibling-bank and single-foreign-child layouts
(probed statuses: `not_found` from the template lookup, "Regulatory package not
found." from `require_package_view`, and `Not Found` from the attachment lookup
under a home package), with no leaked foreign identifier and no table-content
change. No new defect was found; `KNOWN_DEFECTS` is unchanged and its two
system-of-record entries are still reproduced.

Generative layer result: **2 passed**. The negative control had to change with
#230, not because the product weakened but because it strengthened: the package
GET route now resolves its package inside `require_package_view`
(`deps._resolve_package_from_path`, bank in the `WHERE`) before the handler's
`get_package_or_404` runs, so weakening only the service lookup no longer
leaked and the control failed with "the weakened package guard went unnoticed".
It now weakens both layers under the same rolled-back `monkeypatch`, the sweep
reports the sibling-bank leak, and passes once the patch is undone. No product
code was changed. The disposable schemas were dropped by the fixture.
