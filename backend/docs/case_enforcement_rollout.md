# Case workspace scoped-binding enforcement

Issue [179](https://github.com/CedynGroup/aequorOS/issues/179) governs the advisory
case plane. It follows the human DATA cutover in
https://github.com/CedynGroup/aequorOS/pull/759 as the next independently
reviewable RBAC slice. Its shared capability foundation landed in
https://github.com/CedynGroup/aequorOS/pull/188.

The captain resumed this stack on 2026-11-02 (Firstmate inbox014):
"Stack 1 RBAC: PR 759 (#173 data engine) base; #179 cutover stacks on 759" and
"the next PR in a stack starts while the base PR is still awaiting merge; do not
hold stacked dependents for base merges, only for their semantic dependency being
available on the base branch." The stack initially used #759 at
`4707d4c2bc20d68d87898d007fd0dc97de11c0a2`. This later human decision resumed
#179 without requiring the parent merge before child publication. The DATA parent
has since merged into `main` at `32cf063451f24f98691f46337628430aed8bbe0c`;
the case-only child now targets `main` and preserves the merged DATA implementation.

## Authority contract

Every supported case API entry requires one complete active **human** binding:
organization = the authenticated tenant, institution scope = `organization`,
institution ID = `NULL`, module = `risk`, and the action and sensitivity below.
All data coverage must be `all`. No case has a bank ID. A bank-scoped RISK grant,
including a grant on every bank, never grants organization-scoped case authority.
The evaluator applies principal identity/activity, lifecycle and request-wide
conditions. Scalar roles, partial rows, machine grants and impersonation cannot
substitute for that sentence. Evaluator failure denies closed.

The API dependencies check before the case service handler, including storage
URL creation, object metadata reads, parsing/enqueue, calculations and writes.
Trusted worker continuations retain tenant scoping; this cutover adds no machine
entry point or machine grant. Tenant and case/child query constraints remain in
place after authorization.

A collection without the required organization-wide grant refuses with 403,
before query construction, aggregation, counts or pagination. Opaque identifiers
in paths or filters refuse with 404; document/assessment creation and bulk actions
also hide their body references. Foreign and nonexistent objects remain 404.
The two unsupported financial entity catch-alls stay unauthenticated, side-effect
free 404 handlers with no tenant/database/storage dependency.

Sensitivities match exactly. Restricted does not imply confidential. Ordinary
case/queue/score/finding views and calculation/projection history summaries use
confidential. Documents, financial workspace and assumptions, immutable assessment
inputs, calculation/projection details, evidence and exports use restricted.
The report's JSON and HTML representations both require restricted `export`.
A restricted viewer cannot download a document or export a report merely by
holding `view`.

Confidential responses project copies of stored records. Raw case structured
metadata, score inputs/rule details, finding details, rationale and disposition
reason, assessment inputs/errors, and
calculation diagnostics require an independently
complete restricted `view` binding, including on confidential mutation returns.
Summary identity, status, scores, categories and counts remain available.
Finding summaries follow the captain-approved source rule: `manual` summaries
require independently complete restricted `view`; other sources retain their real
summary for any permitted reader, including confidential readers. Without
restricted `view`, manual summaries use the category placeholder. Known limitation:
confidential creators no longer see their own manual summary back unless they
hold restricted `view`. This rule applies to lists, detail and mutation returns;
it does not classify individual text or add a sensitivity flag.
Assessment and document-parse job polling also requires restricted `view`, with
opaque denial; starting a job grants no polling authority.

The inherited DATA Temenos polling observation (R2) remains parent/follow-up work
for #173. The subsequent review follow-up also corrects the DATA dashboard loading
observation (R4): pending profile or selected-bank authority renders loading
feedback before a resolved grant decision. These UI changes do not alter grants
or server permission semantics.

## Operation matrix

All paths have `/api/v1` prepended. C = confidential; R = restricted.

| Routes                                                                                                                                                                                                                    | Permission | Sensitivity |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ----------- |
| GET `/cases`, `/cases/summary`, `/cases/{case_id}`, `/cases/{case_id}/scores`                                                                                                                                             | view       | C           |
| POST `/cases` and `/cases/{case_id}/decisions`                                                                                                                                                                            | create     | C           |
| GET `/cases/{case_id}/decisions`                                                                                                                                                                                          | view       | C           |
| PATCH `/cases/{case_id}`; POST `/cases/{case_id}/assign`, `/cases/{case_id}/archive`; POST `/cases/bulk-actions`                                                                                                          | edit       | C           |
| GET `/findings`, `/findings/{finding_id}`, `/cases/{case_id}/findings`, `/assessments/{assessment_id}/findings`                                                                                                           | view       | C           |
| POST `/cases/{case_id}/findings`                                                                                                                                                                                          | create     | C           |
| PATCH `/findings/{finding_id}` (disposition/status review only)                                                                                                                                                           | review     | C           |
| GET `/findings/{finding_id}/evidence`                                                                                                                                                                                     | view       | R           |
| POST `/documents/upload-request`                                                                                                                                                                                          | create     | R           |
| POST `/documents/{document_id}/complete-upload`; DELETE `/documents/{document_id}`                                                                                                                                        | edit       | R           |
| GET `/documents/{document_id}`, `/documents/{document_id}/parse-status`, `/cases/{case_id}/documents`                                                                                                                     | view       | R           |
| POST `/documents/{document_id}/parse`                                                                                                                                                                                     | run        | R           |
| GET `/documents/{document_id}/download-url`                                                                                                                                                                               | export     | R           |
| POST `/assessments`                                                                                                                                                                                                       | create     | R           |
| GET `/assessments`, `/assessments/{assessment_id}` (includes input snapshot)                                                                                                                                              | view       | R           |
| POST `/assessments/{assessment_id}/run`                                                                                                                                                                                   | run        | R           |
| GET `/assessments/{assessment_id}/runs`, `/assessment-runs/{run_id}`                                                                                                                                                      | view       | C           |
| GET `/cases/{case_id}/financial-workspace`, `/cases/{case_id}/financial-data/validation-issues`                                                                                                                           | view       | R           |
| POST `/cases/{case_id}/financial-workspace/map`, `/cases/{case_id}/financial-data/validate`                                                                                                                               | run        | R           |
| POST financial-workspace `cash-flows`, `institutions`, `accounts`, `reporting-periods`, `balances`, `obligations`, `covenants`                                                                                            | create     | R           |
| PATCH the seven financial resources above by their record ID                                                                                                                                                              | edit       | R           |
| GET `/cases/{case_id}/scenarios`, `/cases/{case_id}/scenarios/{scenario_id}`, `/cases/{case_id}/scenarios/{scenario_id}/validation`                                                                                       | view       | R           |
| GET `/cases/{case_id}/scenarios/readiness`                                                                                                                                                                                | view       | C           |
| POST `/cases/{case_id}/scenarios`, `/cases/{case_id}/scenarios/initialize`, `/cases/{case_id}/scenarios/{scenario_id}/copy`, `/cases/{case_id}/scenarios/{scenario_id}/assumptions`                                       | create     | R           |
| PATCH `/cases/{case_id}/scenarios/{scenario_id}`, `/cases/{case_id}/scenarios/{scenario_id}/assumptions/{assumption_id}`; POST `/cases/{case_id}/scenarios/{scenario_id}/archive`                                         | edit       | R           |
| POST `/cases/{case_id}/scenarios/{scenario_id}/assumptions/{assumption_id}/review`                                                                                                                                        | review     | R           |
| GET `/cases/{case_id}/calculation-runs`, `/cases/{case_id}/capital-projections` (summaries only)                                                                                                                          | view       | C           |
| POST `/cases/{case_id}/calculation-runs`, `/cases/{case_id}/calculation-runs/{run_id}/rerun`, `/cases/{case_id}/capital-projections`                                                                                      | run        | R           |
| GET `/cases/{case_id}/calculation-runs/{run_id}`, `/cases/{case_id}/capital-projections/{projection_id}`, `/cases/{case_id}/capital-summary`, `/cases/{case_id}/capital-comparison`, `/cases/{case_id}/liquidity/summary` | view       | R           |
| POST `/cases/{case_id}/liquidity/findings/{finding_id}/review`                                                                                                                                                            | review     | R           |
| GET `/cases/{case_id}/report` (JSON and HTML)                                                                                                                                                                             | export     | R           |

These are 74 supported method-routes plus the two public no-op catch-alls. The
three static taxonomy endpoints are separate from this 76-route census.

Recording a case decision preserves the existing Analyst preparation/triage
workflow (`create`); this change introduces no new terminal-decision or
maker-checker workflow. Dedicated finding and assumption reviews require `review`.
GET scenario validation is a side-effect-free read; POST financial validation
persists issues and requires `run`. Existing archived-state, decision readiness,
validation, immutable run and concurrency rules still apply.

## Deny impact and explicit operator rows

No production identities, grants or deployment state were inspected or changed.
This is an immediate-default-deny code cutover, not a runtime activation claim.
No migration/backfill grants authority. Before deployment, run the existing
read-only inventory with the explicitly approved target demo database:

```sh
uv run python scripts/authorization_access_impact.py --organization OR-XXXXXXXX --json
```

Retain its dated output privately. `case_permissions` reports organization RISK
`sensitivity/permission` pairs separately from bank product access; an empty
list means all supported case operations refuse. Enumerate each active human's
exact user UUID, organization, confirmed case duty, existing matching rows and
expected denials. Record machine principals separately: all remain denied case
access. Do not infer case users or create grants from scalar roles.

For every confirmed human case user, operators must explicitly approve rows from
this table. All rows carry that user's UUID, their exact organization ID,
`principal_type=human`, `institution_scope=organization`, `institution_id=NULL`,
`module_scope=risk`, `data_scope_kind=all`, active status and approved validity.

| Confirmed duty                                          | role_bundle | sensitivity_scope | Expected denials without another complete row        |
| ------------------------------------------------------- | ----------- | ----------------- | ---------------------------------------------------- |
| Case/queue reader                                       | viewer      | confidential      | financial/documents/evidence, all mutations, exports |
| Financial/document/evidence reader                      | viewer      | restricted        | confidential views, mutations, exports               |
| Case preparation and triage                             | analyst     | confidential      | restricted work and all review actions               |
| Financial preparation, parsing, calculation and exports | analyst     | restricted        | confidential work and all review actions             |
| Finding disposition reviewer                            | approver    | confidential      | preparation/run/export and restricted work           |
| Assumption/cash-flow finding reviewer                   | approver    | restricted        | preparation/run/export and confidential work         |

Confidential and restricted duties require separate rows unless broad `all`
sensitivity is explicitly approved. Grant administration must enforce its existing
assignment-time separation rules; these examples do not authorize incompatible
same-user combinations. Each grant needs the normal reason, grantor, validity,
previewed authority sentence and authorization-version/session transition.

The disposable regression principals are `OR-DEM00001` /
`aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa` and `OR-1S000002` /
`bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb`. Their workflow fixtures explicitly grant
RISK Analyst and Approver at organization scope/all sensitivity. Refusal tests
remove the first user's fixture grants and independently exercise exact rows.
These are test-only principals and rows, not deployment grants or a substitute
for the target demo inventory. No real identifiers are published here.

## Verification and release boundary

`tests/integration/case/api/test_case_authorization.py` exercises zero grants despite scalar
claims, complete rows, partial-row refusal, institution-vs-organization separation,
wrong principal/module/sensitivity, binding lifecycle, cross-tenant counts and
object hiding, permission-specific review/export, machine refusal, global demo
conditions, evaluator failure and the complete 76-route no-side-effect sweep.
The existing case suites retain explicit fixture grants and exercise successful
storage, parsing, financial edits, assumptions, calculations and state transitions.
The object-reference catalogue retains its routes and explicit organization RISK
positive-control grants. The shared access-impact report now exposes case tuples.

The generated HTTP property covers all 74 supported method-routes using an
independent operation-matrix oracle, with complete, partial, irrelevant and
inactive human binding sets. Each generated example uses a rolled-back savepoint;
permitted malformed requests reach input validation while denied requests retain
opaque 404 or collection 403 responses without writes.

The review follow-up includes DATA dashboard loading-state regressions. Browser
checks used a disposable fixture account; no production grants or data were
changed. The report HTML template is unchanged.

Before runtime activation, retain the target demo inventory, approved exact rows,
expected denials, new-session verification and deployment evidence. None of those
runtime steps are claimed by a passing test suite or by this document.
