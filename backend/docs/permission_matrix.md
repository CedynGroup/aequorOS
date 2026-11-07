# Authorization permission matrix (characterization)

[`tests/fixtures/permission_matrix.json`](../tests/fixtures/permission_matrix.json)
is a **test oracle**, not an authorization engine or runtime policy source.
It records observed behavior at `71fce713`, after the route-declaration guard
and the opt-in role/scope factories. Production authority still comes from
complete stored bindings and the production evaluator.

## Represented surface

The file owns three independent sets of expected outcomes:

- **182 bundle/resource/action cells:** all 14 current bundles, each on its
  named resource, with an explicit allow or deny for every one of 13 actions.
  Resource tuples distinguish organization-wide Account/restricted from
  exact-bank LIQ/confidential, REG/restricted, Audit/restricted,
  Data/restricted, Credit/restricted and Markets/restricted. This is **not**
  every bundle crossed with every module, sensitivity or endpoint.
- **33 scoped service scenarios:** exact and sibling banks, foreign tenants,
  nonexistent banks, exact sensitivity (not a hierarchy), wrong modules,
  explicit broad scopes, no grants despite scalar Admin, Owner with and
  without a separate read sentence, non-composition of incomplete bindings,
  approval/submission context, inactive identity, demo-mode veto, Credit
  branch/region permission semantics, and Configurator configure-only
  authority on Data and Markets (view, run and sibling-bank writes refused).
- **Seven real HTTP bank-detail probes:** explicitly granted reads succeed;
  sibling/foreign/no-grant/Owner-only reads remain hidden with 404.
  Credentials are issued after grants at the real authorization version.

The typed consumer in
[`tests/support/permission_matrix.py`](../tests/support/permission_matrix.py)
checks explicit targets, unique bundle rows, complete action partitions and
references. It expands cells without consulting `ROLE_PERMISSIONS`.
Tests create fresh identities without ambient grants using the landed
`role_scope` factories, persist grants through `create_role_binding`, and call
`identity.service.authorization.evaluate_permission`. Expected actions, reasons
and HTTP statuses come from the JSON, never from a production permission map.
Both human and machine identity types are real; machine rows here do **not**
issue integration keys or prove access through machine HTTP dependencies.

### Explicit preconditions and limits

Role cells assume an active identity and binding, a current validity window,
demo mode off and the exact scope written in that row. Passing maker/checker
and step-up **verdicts are explicitly supplied** for bundle characterization.
Separate scenarios omit or veto maker/checker context for REG approval and
submission. A passing verdict tests evaluator consumption, not the correctness
of the workflow that computes it.

The resource names are test locators, not product resource types or route
aliases. Grant profiles are complete scope sentences, not permission presets.
Scope scenarios use fresh human identities with legacy scalar `admin`;
that label conveys nothing. Baseline Member and audit-officer grants retain
their real creation restrictions. All represented grant rows are active.

Credit branch/region scenarios prove only that a narrow binding grants the
named permission. They do not prove row filtering or whole-institution access;
those remain in Credit/BI data-scope and institution-grain tests. This stage
does not exercise lifecycle transitions, assignment-time SoD, ownership
designation, delegation, object-state rules, field masking, pagination,
grant/session state machines, RLS, migration DDL or financial computations.

## Relationship to existing evidence

- The [authorization foundation](authorization_foundation.md) owns policy and
  rollout contracts. Source pointers in the matrix name the production
  evaluator/grant service and independent existing bundle/factory evidence.
- [`route_authorization.json`](../tests/fixtures/route_authorization.json) and
  `tests/architecture/test_route_authorization_coverage.py` still own exact
  tenant/operator endpoint declaration coverage. This matrix neither replaces
  that inventory nor claims exhaustive endpoint authorization.
- [Factory documentation](authorization_test_factories.md) owns setup and
  credential semantics. Existing operator factory tests retain staff/tenant
  credential separation, examiner exact-bank assignment, read-only operation,
  session ownership/expiry and break-glass checks. Those surfaces are **not**
  represented by this matrix and are not collapsed into tenant bundle rules.
- The project organization audit
  (`data/aqs-code-organization-audit/report.md`, outside the public repository),
  F3/P07, calls for non-collected support rather than test-to-test imports or
  universal fixture amplification. These tests opt in locally. P06 remains a
  separate curve-test relocation; no tests, fixtures or suite ownership move.
  No suite census or test registry is introduced.

## Held product calls and sensitive seams

These are evidence-backed boundaries, not new product decisions:

| Seam               | Current characterization / held work                                                                                                                                                                                                                                                                                    |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Configuration      | The Configurator bundle grants exactly `configure` and nothing else: Data and Markets connection configuration denies view, run and sibling-bank writes in the same matrix (the [foundation](https://github.com/CedynGroup/aequorOS/pull/776), [LIQ cutover](https://github.com/CedynGroup/aequorOS/pull/781) and [DATA/MARKETS cutover](https://github.com/CedynGroup/aequorOS/pull/798) own the vocabulary, migrations and register changes). REG, BEH and Credit configure cutovers remain future work; their absence here is not authorization evidence. |
| Signing            | The Signer bundle grants exactly `sign_off` and nothing else; Approver remains denied signing and Signer remains denied approval. [Signer work](https://github.com/CedynGroup/aequorOS/issues/185) owns routed recipient, digest, step-up and route enforcement. Bundle characterization is not proof of those workflow controls.                              |
| REG export         | Viewer and Validator lack EXPORT; view/submit never imply export. A read-only regulatory exporter requires a separate decision, not a matrix correction.                                                                                                                                                                |
| Owner reads        | Owner carries Account administration, not product view. Only an explicit separate read binding enables the represented bank-detail read. No automatic grant or backfill is added.                                                                                                                                       |
| Notifications/jobs | [Provenance work](https://github.com/CedynGroup/aequorOS/issues/186) owns stored source attributes and visibility; no expectations are inferred from labels or links here.                                                                                                                                              |
| Filing and audit   | REG approve/submit and Audit/restricted resources are regulated seams. These tests consume verdicts but never approve, file, sign, export or mutate real records.                                                                                                                                                       |
| Persistence        | New bundle/module vocabulary requires CHECK-widening migrations. This slice changes no enums, schema, migrations, production policy, key custody or logging.                                                                                                                                                            |

## Running and extending

From the repository root, without a live service or Postgres:

```sh
uv run --frozen --project backend pytest \
  backend/tests/identity/service/test_permission_matrix.py \
  backend/tests/identity/service/test_permission_matrix_integration.py -q
```

Filename ownership is unchanged: the data-consumer checks are unit tests;
database/HTTP characterizations are `_integration.py` and use the existing
rollback-isolated database fixture (SQLite by default).

Two committed negative controls run the **same** assertions before,
during and after temporary runtime mutations. Adding Viewer EXPORT must make
the denied cell fail; ignoring institution scope must make the sibling-bank
scenario fail. The mutations are rolled back and never edit production files.

When extending the file, first inspect actual runtime checks and their existing
behavioral evidence, add a resource and independently reviewed expected cells,
then execute them through the production path. New bundle/action vocabulary
fails closed at matrix validation until explicitly characterized. Do not update
expectations merely to make an enforcement change pass: changes in who can do
what require a product decision and the owning rollout, including impact and
migration evidence. This matrix alone provides neither deployment approval nor
a compliance attestation.
