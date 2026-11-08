# Live READ enforcement rollout — freshness (issue #177 backend slice)

Status: implemented in this slice (PR-A of the #177 backend pilot). This
document owns the freshness read authorization contract and records the
rollout evidence still owed before deployment.

## Surface and decision

`GET /api/v1/banks/{bank_id}/freshness` serves only the modules the caller can
VIEW. Enforcement lives at the service layer (`app/live/service/freshness.py`
via the exact-binding decision helper `app/live/service/live_access.py`),
matching the existing precedents:

- `app/data_engine/service/access.py` (DATA, PR 759)
- `app/case/service/authorization.py` (cases, PR 766)
- the live summary's per-module VIEW gate (`app/live/service/live_view.py`
  `_GATED_ENGINE_MODULES`), which freshness reuses for its module mapping.

The API dependency stays the plain tenant dependency; narrowing at the service
layer is explicitly permitted by the route-authorization census basis
("handler and service checks can narrow this access further").

## Mapping authority

`FRESHNESS_VIEW_MODULES` in `app/live/service/live_access.py` owns the exact
freshness-to-authorization module mapping. It matches the live summary's
`_GATED_ENGINE_MODULES`, including MARKETS authority for implied ratings.
`test_mapping_covers_every_freshness_module` in
`backend/tests/live/service/test_live_authorization.py` checks coverage of the
freshness module vocabulary. A new authorization enum value requires a
CHECK-widening migration, not just a surface mapping change.

## Tenant semantics

- **One complete active binding per module.** VIEW, aggregated sensitivity,
  whole-institution grain, exact match; partial rows never compose; no scalar
  role or token-claim shortcut; evaluator failures deny closed.
- **Default deny, closed map.** Unknown or unmapped module names are omitted,
  never surfaced with empty fields. Responses omit unauthorized modules rather
  than labeling them; no hash, timestamp or staleness byte leaks for them.
- **Denial before per-module computation and official reads.** Unauthorized
  modules never trigger engine hash computations or official-run lookups. The
  service still loads bank-scoped live rows before filtering the response.
- **Aggregate honesty.** `is_stale` aggregates only the served (authorized)
  modules; a restricted viewer's flag describes exactly the payload served.
  With no permitted modules, `modules` is empty and `is_stale` is false.
- **Response compatibility.** The response schema and module order are unchanged.
  Fully authorized tenant viewers retain all modules and the existing hash and
  staleness behavior.
- **Opaque bank denial unchanged.** Unknown or other-tenant banks still 404
  before any module decision.

## Operator-board freshness

`GET /operator/v1/tenants` reads freshness through its existing staff gate
(`get_operator_context`). Its service selects the explicit `staff_board` module
authority in `get_bank_freshness`, serving the full freshness module vocabulary
for each bank with reporting periods. This selection is internal, never request
input: the staff seat is not a tenant principal, and no tenant grant is created.
The default `tenant_bindings` authority retains the tenant semantics above,
including denial for an actor-less context.

The board aggregates the returned modules, so drift is reported as stale rather
than a fresh summary with zero modules. A bank with no reporting periods has no
freshness summary; a per-bank evaluation failure also yields no summary without
failing the whole board.

## Remaining pilot scope

Further pilot work on the official-snapshot read loop,
alerts and board-pack window predicates (PR-C), the refresh/mint mutation
gates (PR-B — mint authority already decided: the existing per-module RUN loop
plus the `Module.CAPITAL` entry) and the impact-evidence tooling (PR-D) are
later PRs of the same pilot. Issue #177 remains open until the full surface
set and the UI compounds land.

## Rollout evidence owed before deployment

Per the authorization verification contract and the pilot plan:

- dated deny-impact output for the freshness surface (PR-D tooling),
- exact-grant artifacts showing which bundles change behavior,
- reviewer-visible before/after dashboard evidence for the restricted-caller
  FreshnessStrip state (synthetic fixtures only; no production data).

None of these block the code PR; they are deployment-gate artifacts, and no
deployment is authorized by this pilot.

## Test evidence

- `backend/tests/operator/test_operator_freshness_plane_integration.py` —
  operator-route coverage for drifted modules, absent live state, and denial
  without staff authentication.
- `backend/tests/live/api/test_live_engine_authorization_integration.py` —
  API-level contract: bound module present, unbound modules absent without
  leakage, bindings-free principal closed map, same-organization bank-scoped isolation, schema
  and order preserved for a fully bound viewer.
- `backend/tests/live/service/test_live_authorization.py` — hermetic pins for
  the mapping and deny-closed helper semantics.
- The existing `backend/tests/live/service/test_freshness_integration.py`
  keeps proving hash-staleness semantics for an authorized viewer.
