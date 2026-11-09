# Live read enforcement

This document owns the live read authorization contracts: which modules each
live read surface serves a caller, how denial behaves, and what the route and
storage boundaries guarantee.

## Surface and decision

`GET /api/v1/banks/{bank_id}/freshness` serves only the modules the caller can
VIEW. Enforcement lives at the service layer (`app/live/service/freshness.py`
via the exact-binding decision helper `app/live/service/live_access.py`),
matching these service-layer authorization patterns:

- `app/data_engine/service/access.py` (DATA)
- `app/case/service/authorization.py` (cases)
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

## Tenant freshness semantics

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
- **Response shape and order.** Authorization filters the module list without
  changing its schema or relative order. Fully authorized tenant viewers receive
  all modules with their hash and staleness fields.
- **No reporting period is an empty map, not a denial.** Freshness is defined
  against reporting periods: when none exists for the bank, the service answers
  `modules: []` and `is_stale: false` before any binding evaluation. An empty
  payload can therefore mean "no period yet" as well as "no permitted modules";
  a missing period is data state, never an authorization verdict.
- **Opaque bank denial.** Unknown or other-tenant banks return 404
  before any module decision.

## Operator-board freshness

`GET /operator/v1/tenants` reads freshness through its staff gate
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

## Snapshot, alert and window read contract

The live-snapshot ladder (`live_view.list_live_snapshots`), the alerts
feed (`alerts.get_bank_alerts`) and the window analytics
(`window_analytics.compute_window`) enforce the exact per-module VIEW
binding, including the Capital entry. The ladder is live data:
`LiveMetricSnapshot` rows are the per-day closes of each live refresh, never
official regulatory results — an official run is an immutable run record read
through the freshness hashes, not a row on this ladder. Alerts and window
analytics use engine gate lists matching the freshness map; the snapshot
service has a separate module lookup, with additional route checks for
liquidity and IRRBB.

For an existing accessible bank, `GET /api/v1/banks/{bank_id}/live-snapshots`
returns 403 when the requested module lacks a complete active whole-institution
VIEW binding at aggregated sensitivity. By contrast, `/alerts` omits denied
modules from both items and counts, and `/analytics/window` omits their ratio
series and daily aggregates. With no permitted modules, alerts have zero total
and window `ratios` and `daily` are empty; window dates and `period_count` remain.
Authorized viewers retain their stored snapshots, findings and window results.

Unknown snapshot query modules return 422 through the route's closed pattern.
Unknown module rows are rejected by the module CHECK constraints on
`live_metrics`, `live_findings` and `live_metric_snapshots`. This is a route
and storage boundary, not a claim that the snapshot service's lookup or alerts'
exclusion query independently rejects arbitrary unknown module strings.

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
- `backend/tests/live/service/test_live_read_scope.py` — hermetic pins that
  the live-view, alerts and window engine gate lists match the freshness map
  and cover the live module vocabulary; the snapshot service's separate
  lookup is exercised by the API tests below.
- `backend/tests/live/api/test_live_read_scope_integration.py` — API-level
  READ-scope contract for those three surfaces: bound module served,
  closed maps for unbound, foreign-bank and suspended principals, stale
  authorization-version refusal, preserved shapes for a fully bound viewer,
  and the storage module CHECK refusing unknown module rows.
- `backend/tests/live/service/test_freshness_integration.py`
  verifies hash-staleness semantics for an authorized viewer.

These are automated fixture-based contracts, including TestClient API checks;
they do not constitute live deployment validation.
