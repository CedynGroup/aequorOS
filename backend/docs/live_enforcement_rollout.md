# Live READ enforcement rollout — freshness (issue #177 backend slice)

Status: planned (PR-A of the #177 backend pilot). This document records the
contract the freshness read surface follows once its module filter ships, and
the rollout evidence still owed before any deployment.

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

## Mapping (existing authority; nothing invented)

| Freshness module | Authorization module |
| --- | --- |
| liquidity | `Module.LIQUIDITY` |
| capital | `Module.CAPITAL` |
| credit | `Module.CREDIT` |
| irr | `Module.IRRBB` |
| fx | `Module.FX` |
| ftp | `Module.FTP` |
| rating | `Module.MARKETS` |
| forecast | `Module.FORECASTING` |

The `rating → MARKETS` pairing is the live summary's existing authority for
implied-rating figures. Any future mint/read work reuses this mapping rather
than inventing a RATING enum value; a genuine new module vocabulary is a
CHECK-widening migration decision that belongs to root, not to a surface PR.

## Semantics

- **One complete active binding per module.** VIEW, aggregated sensitivity,
  whole-institution grain, exact match; partial rows never compose; no scalar
  role or token-claim shortcut; evaluator failures deny closed.
- **Default deny, closed map.** Unknown or unmapped module names are omitted,
  never surfaced with empty fields. Responses omit unauthorized modules rather
  than labeling them; no hash, timestamp or staleness byte leaks for them.
- **Denial before protected reads.** Unauthorized modules never trigger engine
  hash computations or official-run lookups.
- **Aggregate honesty.** `is_stale` aggregates only the served (authorized)
  modules; a restricted viewer's flag describes exactly the payload served.
- **Opaque bank denial unchanged.** Unknown or other-tenant banks still 404
  before any module decision.

## Scope of this slice (and what it is not)

This slice covers the freshness read only. The official-snapshot read loop,
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

- `backend/tests/live/api/test_live_engine_authorization_integration.py` —
  API-level contract: bound module present, unbound modules absent without
  leakage, bindings-free principal closed map, bank-scoped isolation, schema
  and order preserved for a fully bound viewer.
- `backend/tests/live/service/test_live_authorization.py` — hermetic pins for
  the mapping and deny-closed helper semantics.
- The existing `backend/tests/live/service/test_freshness_integration.py`
  keeps proving hash-staleness semantics for an authorized viewer.
