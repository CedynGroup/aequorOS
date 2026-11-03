# Examiner evidence scoped-binding enforcement (AUDIT gate)

Issue [#180](https://github.com/CedynGroup/aequorOS/issues/180) restricts the
tenant examiner evidence surfaces to authorised auditors. It is PR 18 of 25 in
the scoped-binding enforcement program under epic
[#163](https://github.com/CedynGroup/aequorOS/issues/163) and follows the merged
human DATA cutover ([#173](https://github.com/CedynGroup/aequorOS/issues/173),
[PR 759](https://github.com/CedynGroup/aequorOS/pull/759) at
`32cf063451f24f98691f46337628430aed8bbe0c`) and the case workspace RISK cutover
([#179](https://github.com/CedynGroup/aequorOS/issues/179),
[PR 766](https://github.com/CedynGroup/aequorOS/pull/766) at
`9be4a717`). Those two cutovers established the model this one reuses: typed
exact permission checks, three-level sensitivity, per-module filtering before
composition, and an independent operation-matrix oracle in the tests.

Before this cutover the three examiner routes carried only the tenant context:
any authenticated principal of the tenant could read the whole supervisory
bundle, and the scalar `examiner` token role was treated as if it were audit
authority. The bank's internal auditor was not separated from every other
signed-in user.

## Authority contract

The bundle is the bank's audit record, so reading it takes **two independent
sentences**. They are never composed from parts of each other, and a reader
holding one does not acquire the other.

**Entry.** One complete active **human** binding, matching every dimension:

- organization = the authenticated tenant;
- institution = the path bank. An organization-wide row covers it; a
  bank-scoped row must name that exact `BK-*`. A sibling institution of the same
  organization never covers it;
- module = `audit`, sensitivity = `restricted`, permission = `view`;
- data coverage = the **whole institution**. A branch- or region-narrowed Audit
  row cannot produce an institution's evidence bundle, because the bundle is the
  institution's book, not a slice of it. (Branch/region narrowing remains a
  Credit-only mechanism today, so this is a statement of intent as well as of
  code.)

**Source.** For every source the bundle embeds, the reader's own module
sentence. Seeing the bundle must not reveal a module the reader cannot open:

| Embedded source                                                         | Module                                                                                                                             | Sensitivity  |
| ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| stored runs (`regulatory_runs`)                                         | the canonical run-module map (below)                                                                                               | confidential |
| filed packages, family `icaap`                                          | `cap`                                                                                                                              | confidential |
| filed packages, every other family                                      | `reg`                                                                                                                              | restricted   |
| register `liquidity-thresholds`, `liquidity-haircuts`, `liquidity/ewis` | `liq`                                                                                                                              | confidential |
| register `ecl-assumptions`, `crm-haircuts`                              | `credit`                                                                                                                           | confidential |
| register `capital-plan`                                                 | `cap`                                                                                                                              | confidential |
| contingency funding plan posture                                        | `liq`                                                                                                                              | confidential |
| ICAAP cycles                                                            | the `icaap` family gate (`cap`/confidential, bank regime only)                                                                     | confidential |
| audit volume                                                            | no separate sentence: retained tenant events attributable to this institution, excluding the operator stream (not period-filtered) | restricted   |

The run-module map is **not restated here or in the service**. The canonical map
is `liquidity.service.regulatory_liquidity._REGULATORY_RUN_AUTHORIZATION`,
exposed through `app.liquidity.public` as `run_authorization_module(s)`.

An unknown or future stored run module has no established authority and is
denied, never guessed. A new run module therefore stays hidden from examiners
until that map is amended, which is the intended direction of failure.

Evaluator failure denies. Scalar roles, token claims, machine bundles, and
partial rows are not authority: an Audit row missing sensitivity and a
Liquidity row with the right sensitivity are still two rows, and the entry gate
sees neither.

### Denial semantics

- Missing/partial/inactive entry authority → `403` with one uniform sentence
  (`Examiner evidence requires an active scoped binding for this institution.`),
  without disclosing which binding dimension failed.
- The run list and the documentation package filter runs in SQL. The package
  also filters filed packages by family authority, omits unreadable registers
  and ICAAP cycles, and skips unreadable CFP retrieval before composing the
  response. Both routes answer `200` with the readable subset.
- The by-id run reproduction is the one route whose body discloses a stored
  snapshot's shape, so its source sentence is enforced on the object: a run whose
  source module is not held answers the **same opaque `404`** a nonexistent run
  does, with the same `code`/`message`.
- A foreign or unknown institution resolves `404` before any authority decision
  (`resolve_tenant_bank`), unchanged from every other bank route.
- Nothing in the cutover mutates. There is no write path, no grant creation, and
  no machine entry point: the surfaces are read-only by construction.

### Disclosed withholding

A filtered bundle must never render a withheld source as an absent fact. The
documentation package therefore carries `withheld_sources`: prefixed keys
`run:<module>`, `package:<family>`, `register:<name>`, and `cfp`. An empty list
is a complete bundle, so a reader checks it before interpreting a filtered block
as an absent fact. When the CFP posture is withheld,
`cfp_approved_version` is null and `cfp_active` is false **because the plan was
not readable**, and `cfp` appears in `withheld_sources`; false therefore means
"readable and inactive" only when `cfp` is absent from that list.

### Operator inspection is a separate lifecycle

An act-as-examiner staff session (operator control plane, `authv` 0) still reads
the complete bundle: a supervisor reads the whole institution by construction,
and that seat is exactly the exclusion
`identity.service.authorization.project_examiner_authority` already states (it
projects every institution module **except** `audit`). Consequences:

- a tenant AUDIT grant adds nothing to a staff session and is not required by it;
- a session without verified operator provenance (`impersonation_context` and
  `actor_operator`) or with a failing request-wide condition (demo mode, for one)
  is treated as an ordinary principal and refused without a binding;
- the seat stays read-only: every mutation still refuses, and the session is
  still tenant-isolated (`404` for another tenant's institution).

## Operation matrix

All paths are prefixed with `/api/v1`. Every row also requires the entry
sentence; the "source" column is the additional per-object sentence.

| Route                                                      | Permission | Sensitivity | Source sentence                                                 |
| ---------------------------------------------------------- | ---------- | ----------- | --------------------------------------------------------------- |
| GET `/banks/{bank_id}/examiner/runs`                       | view       | restricted  | none on the route; rows filtered per run module at confidential |
| GET `/banks/{bank_id}/examiner/documentation-package`      | view       | restricted  | none on the route; every embedded block filtered                |
| GET `/banks/{bank_id}/examiner/runs/{run_id}/reproduction` | view       | restricted  | the run's module view at confidential, else opaque 404          |

These are the only three examiner routes, and
`tests/unit/architecture/test_examiner_authorization.py` pins that census.

## Deny impact and explicit operator rows

No production identities, grants or deployment state were inspected or changed.
This is an immediate-default-deny code cutover, not a runtime activation claim.
No migration and no backfill grants authority; there is no legacy-role fallback
and no compatibility grant.

Before deployment, run the read-only inventory that now reports the examiner
column, and retain its dated output privately with the deployment record:

```sh
uv run python scripts/authorization_access_impact.py --organization OR-XXXXXXXX --json
```

`examiner` lists the institutions whose evidence bundle each active human may
open, i.e. the whole-institution `audit`/`restricted` `view` sentence. It
deliberately does not include the source sentences: a user in that column can
still open an empty or partial bundle. Enumerate each active human's exact user
UUID, organization, confirmed audit duty, existing matching rows, and the
sources they must also hold. Record machine principals separately: every machine
principal remains denied. Do not infer examiners from scalar roles — the
`examiner` token role carries no authority at all after this cutover.

For every confirmed human evidence reader, operators must explicitly approve
rows from this table. Every row carries that user's UUID, their exact
organization ID, `principal_type=human`, active status and approved validity.

| Confirmed duty                                                                          | role_bundle           | institution_scope               | module_scope           | sensitivity_scope |
| --------------------------------------------------------------------------------------- | --------------------- | ------------------------------- | ---------------------- | ----------------- |
| Opens the supervisory bundle (bank internal auditor, audit liaison)                     | `auditor` or `viewer` | organization, or the exact bank | `audit`                | `restricted`      |
| Reads Liquidity sources (run snapshots, threshold/haircut registers, EWI register, CFP) | `viewer` or `analyst` | as granted                      | `liq`                  | `confidential`    |
| Reads Capital sources (capital-plan register, ICAAP packages and cycles)                | `viewer`              | as granted                      | `cap`                  | `confidential`    |
| Reads filed returns (every non-ICAAP family)                                            | `viewer`              | as granted                      | `reg`                  | `restricted`      |
| Reads Credit sources (ECL assumptions, CRM haircut register)                            | `viewer` or `analyst` | as granted                      | `credit`               | `confidential`    |
| Reads Forecasting runs (projection, optimizer, what-if, reverse stress)                 | `viewer` or `analyst` | as granted                      | `fcst`                 | `confidential`    |
| Reads IRRBB / FX / FTP runs                                                             | `viewer` or `analyst` | as granted                      | `irrbb` / `fx` / `ftp` | `confidential`    |
| Reads enterprise-stress runs                                                            | `viewer` or `analyst` | as granted                      | `risk`                 | `confidential`    |

`auditor` and `viewer` are permission-equivalent for this cutover: both bundles
carry exactly `{view}`, and the sensitivity dimension is what separates an audit
reader from a general one. The two audit officer bundles (`security_officer`,
`compliance_officer`) also carry `view` and satisfy the same sentence when their
whole-institution Audit coverage invariant holds.

Reading rows take `viewer` wherever the bundle is read-only. `analyst` also
carries `run`, `create` and `edit`, and two of these modules are already
binding-cut-over: an `analyst` row at `reg`/`restricted` can generate regulatory
packages (`require_generation_authority` asks for `run` on ordinary families),
and one at `cap`/`confidential` can create or edit a capital-plan draft. Grant
`analyst` only where the user genuinely authors those artefacts, through a
separate approval.

Every source row needs effective `data_scope_kind=all` as well. The source gate
asks for whole-institution coverage, so a branch- or region-narrowed `liq`,
`cap`, `reg`, `credit`, `fcst`, `irrbb`, `fx`, `ftp` or `risk` row leaves that
source withheld even though the row exists. That includes the Credit registers:
a narrowed Credit grant does not open `ecl-assumptions` or `crm-haircuts`.

An organization with no `audit`/`restricted` row after this cutover loses every
examiner route; an organization whose auditor holds only the entry row keeps the
routes and opens an empty bundle that lists what it withheld. Neither state
requires a code change to correct — only an approved binding.

## Verification and release boundary

- `tests/integration/reporting/api/test_examiner_authorization.py` exercises the scalar-role
  refusal (including `examiner`), partial-row non-composition, wrong
  module/sensitivity, sibling-institution separation, binding lifecycle
  (future/expired/suspended/revoked), an `account_admin` bundle without `view`,
  cross-tenant `404`, SQL pre-filtering of the run list, the identical `404` for
  an unreadable run module, per-source filtering of every embedded block
  (runs, packages, registers, CFP), the institution-attributable audit count,
  Auditor/Viewer response equivalence, and operator inspection (reads without a
  tenant grant, gains nothing from one, stays read-only and tenant-isolated).
- The generated HTTP property compares every examiner operation against an
  independently stated oracle
  (`tests/unit/reporting/testing/authorization_matrix.py`) over complete, partial,
  irrelevant, sibling-scoped and inactive human binding sets, including an
  `ALL`/`ALL` row and an empty set. Each example runs in a rolled-back savepoint
  and asserts that binding and run counts remain unchanged by the requests.
- `tests/unit/architecture/test_examiner_authorization.py` pins the three-route
  census and each route's dependency set: exactly one
  `require_examiner_evidence`, `resolve_tenant_bank`, no mutation-context
  dependency and no `require_role_*`.
- `tests/integration/reporting/api/test_examiner_mode.py` keeps its read-model
  invariants (hash reproduction, tamper detection, package/CFP/audit bundling,
  tenant isolation), now acting as a principal that explicitly holds the entry
  and source rows alongside stored evidence inside the disposable fixture
  transaction. It does not use the primary database.
- `tests/integration/scripts/test_authorization_access_impact.py` pins the new `examiner`
  column, and `scripts/authorization_access_impact.py` documents it.
- The two new test modules are classified in `scripts/test_suites.json`
  (`integration` for the HTTP matrix, `unit` for the architecture ratchet).

No dashboard change is required: no page consumes the generated
`ExaminerApi` client, so this cutover has no user-visible UI surface and no
browser evidence to attach. The generated TypeScript client is regenerated for
the one contract addition (`withheld_sources` on the documentation package).

Before runtime activation, retain the target inventory, the approved exact rows,
the expected denials, a new-session verification, and the deployment evidence.
None of those runtime steps are claimed by a passing test suite or by this
document.
