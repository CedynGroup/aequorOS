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
| register `ecl-assumptions`, `crm-haircuts`                              | `credit`                                                                                                                           | restricted   |
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

### The cited registers are readable, inside ONE bank (issue 182)

The bundle cites six register endpoints rather than copying their rows. That
made a staff session's own catalogue unopenable: the session holds no tenant
binding, so `GET /banks/{id}/liquidity-thresholds`, `/liquidity-haircuts`,
`/liquidity/ewis`, `/ecl-assumptions`, `/crm-haircuts` and `/capital-plan` all
answered `403` — the ECL and CRM two only since the Phase 5 credit cutover
replaced their tenancy-only gate, the liquidity half for longer.

`app/core/examiner.py` is the fix, and it is deliberately the ONLY widening:

- **The allow-list is the route.** `EXAMINER_REGISTER_ROUTES` maps
  `(method, templated path)` to the register it serves. Nothing else is admitted,
  so a gate shared with an uncited surface cannot leak: `/concentration-limits`,
  `/credit-thresholds`, `/sdi/large-exposures`, `/sdi/loan-classification`,
  `/liquidity/cfp` and `/capital-plan/ilaap` sit on the SAME dependencies as cited
  routes and stay refused.
- **The session is bound to ONE bank.** `operator_inspector_sessions.bank_id`
  records the examined institution when the session is opened
  (`202611030002`), and `POST …/sessions/{id}/act-token` carries it into the
  signed impersonation token as the `bank` claim. The tenant API reads a cited
  register only when that claim equals the bank in the path. Two banks of one
  organization share an RLS tenant, so the claim — not `org` — is the boundary.
- **No bank means no read.** The column is nullable only because sessions already
  open when it landed cannot be back-filled: one organization can hold several
  banks, so an organization id names no institution. A token without a `bank`
  claim reads nothing. There is no organization-wide fallback.
- **The rest of the inspection is unchanged.** Provenance and the request-wide
  conditions are re-checked per request; the seat stays read-only (every register
  PUT is still `403`); a foreign bank still resolves `404` first; and a tenant
  principal gains nothing from any of it, because its own exact binding sentence
  is what decides.
- **One copy of the list.** The bundle's `SOURCE_REGISTERS` is derived from the
  kernel table, so the module and sensitivity the catalogue advertises for a
  register are the ones its gate enforces, and the two cannot drift.
  `tests/architecture/test_examiner_register_routes.py` pins the table, its
  routes, the gates behind them and that derivation;
  `tests/api/test_examiner_register_reads_integration.py` pins the behaviour.

### The bank claim bounds the register reads, NOT the bundle

The binding is a **register-read** guarantee, and the boundary is worth stating
plainly because it is narrower than "the session is confined to one bank". The
bundle's own gates — the entry gate `require_evidence_view` / `staff_inspection`
and the per-source sentences on `EvidenceAuthority` — admit a verified read-only
inspection on operator provenance, the request-wide conditions and the AUDIT
entry sentence. **Neither consults `bank_id`.** Two consequences follow, and
neither is a defect this change introduces (before the binding landed, a session
named no institution at all and reached every bank of its organization):

- A session bound to bank A can request bank B's evidence bundle within the SAME
  organization, and the bundle's entry sentence admits it. Its six cited register
  reads then return `403` for `bank B`, because `app/core/examiner.py` compares
  the claim with the bank in the path. So the citations the bundle advertises are
  openable only for the bank the session is examining.
- A session opened before the binding landed carries no `bank` claim and is in the
  same position: it receives the bundle, sees the six citations disclosed under
  its held authority, and can open none of them.

Both fail closed on the register surface and neither widens the organization-wide
read, but an operator reading this page should not assume the bank binding limits
the bundle itself: to read bank B's bundle in full, open a session bound to bank B.
Tightening the bundle entry gate to the claim is a separate, deliberate decision:
it would NARROW examiner reach rather than preserve it, so this change leaves the
bundle gate exactly as it found it and does not claim the binding covers it.

## Operation matrix

All paths are prefixed with `/api/v1`. Every row also requires the entry
sentence; the "source" column is the additional per-object sentence.

| Route                                                      | Permission | Sensitivity | Source sentence                                                 |
| ---------------------------------------------------------- | ---------- | ----------- | --------------------------------------------------------------- |
| GET `/banks/{bank_id}/examiner/runs`                       | view       | restricted  | none on the route; rows filtered per run module at confidential |
| GET `/banks/{bank_id}/examiner/documentation-package`      | view       | restricted  | none on the route; every embedded block filtered                |
| GET `/banks/{bank_id}/examiner/runs/{run_id}/reproduction` | view       | restricted  | the run's module view at confidential, else opaque 404          |

These are the only three examiner routes, and
`tests/architecture/test_examiner_authorization.py` pins that census.

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
| Reads Credit sources (ECL assumptions, CRM haircut register)                            | `viewer` or `analyst` | as granted                      | `credit`               | `restricted`      |
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

- `tests/reporting/api/test_examiner_authorization_integration.py` exercises the scalar-role
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
  (`tests/reporting/testing/authorization_matrix.py`) over complete, partial,
  irrelevant, sibling-scoped and inactive human binding sets, including an
  `ALL`/`ALL` row and an empty set. Each example runs in a rolled-back savepoint
  and asserts that binding and run counts remain unchanged by the requests.
- `tests/architecture/test_examiner_authorization.py` pins the three-route
  census and each route's dependency set: exactly one
  `require_examiner_evidence`, `resolve_tenant_bank`, no mutation-context
  dependency and no `require_role_*`.
- `tests/reporting/api/test_examiner_mode.py` keeps its read-model
  invariants (hash reproduction, tamper detection, package/CFP/audit bundling,
  tenant isolation), now acting as a principal that explicitly holds the entry
  and source rows alongside stored evidence inside the disposable fixture
  transaction. It does not use the primary database.
- `tests/scripts/test_authorization_access_impact_integration.py` pins the new `examiner`
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
