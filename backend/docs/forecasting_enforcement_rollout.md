# Forecasting scoped-binding enforcement rollout

This is the deployment gate for the Forecasting cutover: the balance-sheet
projection, the strategic optimizer, the what-if lab, and reverse stress
testing. It does not authorize a backfill. Operators must inventory real
principals, confirm duties with the institution, and create each approved
binding explicitly before deployment.

## Affected surfaces

| Surface                                                                                                                    | Required complete binding      |
| -------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| Scenario presets (`GET …/forecast/scenarios`) and the forecast run index (`GET …/forecast/runs`)                           | FCST / `aggregated` / `view`   |
| Forecast rows in the live summary, live snapshots, alerts, window analytics, and the regulatory-run registry               | FCST / `aggregated` / `view`   |
| The capital-plan projection section (`GET …/capital-plan`), which is rendered from Forecasting runs                        | FCST / `aggregated` / `view`   |
| Full forecast run detail (`GET …/forecast/runs/{run_id}`) and the same run through `GET …/regulatory-runs/…`               | FCST / `confidential` / `view` |
| The latest reverse-stress frontier (`GET …/reverse-stress/latest`)                                                         | FCST / `confidential` / `view` |
| Create a forecast run, run the optimizer, run a what-if shock, run reverse stress                                          | FCST / `confidential` / `run`  |
| Activation with calculations and on-demand or scheduled official runs, when `module_scope.runs_module` includes `forecast` | FCST / `confidential` / `run`  |

The four persisted run modules — `forecast`, `optimizer`, `whatif`, and
`reverse_stress` — answer to the one Forecasting authority. The shared
regulatory-run registry filters all four before total counts, offsets, and
limits; a run the caller may not open returns the same 404 as an unknown or
cross-tenant id. For callers without confidential view, registry metrics contain
only allowlisted headline scalars; forecast paths and assumptions, optimizer
candidates and decisions, what-if paths and comparisons, and reverse-stress axes
and narrative are withheld. Reverse-stress summary metrics are empty. Callers
with confidential view retain full metrics on rows admitted by aggregated view.
The per-module summary policy lives in
`regulatory_liquidity._REGULATORY_RUN_AUTHORIZATION`; this cutover applies it
only to Forecasting, leaving FTP, FX, and IRRBB output unchanged. Regression
coverage is in `tests/api/test_forecasting_authorization.py`.

Run-detail denials use 404 "Regulatory run not found."; the period-keyed latest
reverse-stress read denies missing authority with 403.
A denied execution cannot start an engine, create a run row,
derive facts, enqueue work, or write an audit event: the route dependency
decides before the handler, and the service re-checks confidential run before
the period lookup or computation, after resolving the bank, so the official-run
path is held to the same rule.

Shared live projections filter the `forecast` module before serialization,
counts, limits, and aggregation. Mixed execution retains its existing gates and
checks Forecasting only when planned. Scheduled official-run actor selection and
queue attribution follow the
[FX queued-run contract](fx_enforcement_rollout.md#queued-and-scheduled-official-runs);
Forecasting joins FX and FTP as a module the selected principal must hold
confidential run authority for when it is in the institution's plan.

The capital plan stays readable without Forecasting authority. Only its
projection section is withheld, reported as
`projection_unavailable.error_code = "forecasting_view_required"` with the
grant sentence a user needs, so a capital reader is told what is missing rather
than shown an empty chart.

Two consumers deliberately keep their own authority: the ICAAP workspace links
a reverse-stress figure under the Capital authority that governs the whole
document, and return generation (STRESS-PACK, ICAAP) reads forecast and
reverse-stress runs under the Regulatory authority of the package. Neither
surface is a Forecasting route, and neither exposes a run outside the document
it is bound into.

No scalar role, token role, tenant membership, or binding for another module or
sensitivity grants Forecasting authority. Forecast assumption presets are
read-only reference data on this surface; assumption governance stays with the
regulatory-parameter control plane and its own cutover.

## Dashboard controls

`/forecasting` (Balance Sheet) and `/forecasting/assumptions` require
aggregated view. Scenarios and Reverse Stress require confidential view; NII,
Optimizer, and What-if require both aggregated and confidential view because
they read a run index before opening full results.

A missing view grant leaves the workspace and its navigation visible but
disabled. Direct workspace links preserve the console shell and explain the
missing Forecasting view permission in a hover/focus tooltip, directing the
member to an Org Owner via Settings → Members. This includes unbound members
and account-only administrators with no institution authority. The disabled
workspace does not mount data consumers or issue Forecasting API requests;
missing aggregated view is never presented as an empty run history.
Structural exclusions, unknown routes, and unauthorized object details retain
404 responses. An aggregated-only reader on the Balance Sheet sees the live
baseline and run index but never requests a run's detail.

Every execution control — **Run forecast**, the scenario designer's run action,
**Run optimizer**, the what-if **Run** control, and **Run reverse stress** —
consumes the exact confidential run capability from effective authority.
Without it the control remains visible and disabled with "Requires Forecasting
· Confidential · Run. Ask your organization owner or admin to grant it." The
shared permission-only disabled-control policy is defined in
[the RBAC guide](../../docs/rbac.md); native disabled controls expose their
explanation through a keyboard-focusable wrapper.

If a confidential-view reader lacks aggregated view, the scenario designer
cannot load presets. Its run control remains visible and disabled with
"Requires Forecasting · Aggregated · View. Ask your organization owner or admin
to grant it."

Basel → Planning requests the forecast run index only with aggregated view and
a run's path only with confidential view; the Command Center pulse wall and its
prior-close ladder request Forecasting data only when `/forecasting` is
enabled for the user.

## Deny-impact inventory

Run the inventory read-only with a role that can see every tenant. Store the
dated result with the release evidence. Do not copy names or identifiers from
another environment into a grant request.

```sql
SELECT
    u.organization_id,
    u.id AS principal_user_id,
    u.email,
    u.auth_provider,
    u.is_active,
    b.id AS institution_id,
    ab.id AS binding_id,
    ab.principal_type,
    ab.role_bundle,
    ab.institution_scope,
    ab.institution_id AS binding_institution_id,
    ab.module_scope,
    ab.sensitivity_scope,
    ab.status,
    ab.valid_from,
    ab.valid_until,
    ab.revoked_at
FROM users AS u
CROSS JOIN banks AS b
LEFT JOIN authorization_bindings AS ab
  ON ab.organization_id = u.organization_id
 AND ab.principal_user_id = u.id
 AND ab.principal_type = 'human'
 AND ab.status = 'active'
 AND ab.revoked_at IS NULL
 AND ab.valid_from <= now()
 AND (ab.valid_until IS NULL OR ab.valid_until > now())
 AND ab.module_scope IN ('fcst', 'all')
 AND (
      (ab.institution_scope = 'institution' AND ab.institution_id = b.id)
      OR
      (ab.institution_scope = 'organization' AND ab.institution_id IS NULL)
 )
WHERE b.organization_id = u.organization_id
ORDER BY u.organization_id, b.id, u.email, ab.role_bundle, ab.sensitivity_scope;
```

Institutions whose plan includes Forecasting (`module_scope.runs_module`
returns true for `forecast`: universal banks, not SDIs) also need a scheduled
official-run actor with confidential run authority, or the scheduler records
`no_authorized_scheduled_principal` and mints nothing. Read the queued-run
inventory from the
[FX contract](fx_enforcement_rollout.md#queued-and-scheduled-official-runs)
with `module_scope IN ('fcst', 'all')` alongside the FX and FTP rows.

For every active human principal who uses an affected surface, record:

1. the organization and exact institution;
2. the user and confirmed Forecasting duty;
3. the affected surface;
4. whether one complete active row already grants the required tuple; and
5. whether deployment will deny the user until a row is approved.

Record machine principals separately. They receive no interactive Forecasting
grant in this cutover. The live plane's own five-year baseline
(`regulatory_forecasting.compute_live`) is computed by the worker without a
tenant actor and is unaffected; only who may read it changes.

## Exact binding rows

Create only rows approved from the inventory through the authorization service.
Follow the foundation's
[authorization-version and session transition contract](authorization_foundation.md#authorization-version-and-deployment-transition).

| Duty                                                                           | `principal_type` | `role_bundle`                                 | `institution_scope` | `institution_id` | `module_scope` | `sensitivity_scope` |
| ------------------------------------------------------------------------------ | ---------------- | --------------------------------------------- | ------------------- | ---------------- | -------------- | ------------------- |
| Read presets, the run index, live baseline, capital-plan projection            | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `fcst`         | `aggregated`        |
| Open full runs, the what-if and optimizer results, the reverse-stress frontier | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `fcst`         | `confidential`      |
| Run forecasts, the optimizer, what-if shocks, and reverse stress               | `human`          | `analyst`                                     | `institution`       | exact `BK-*`     | `fcst`         | `confidential`      |

If the institution explicitly approves coverage across all its banks, replace
only `institution_scope` with `organization` and `institution_id` with `NULL`.
Do not widen module or sensitivity scope to compensate for a missing row.
Aggregated and confidential access are separate rows unless the institution
explicitly approves the broad `all` sensitivity.

Example least-privilege request for a Forecasting run maker:

```json
{
  "principal_user_id": "<confirmed human user UUID>",
  "role_bundle": "analyst",
  "institution_scope": "institution",
  "institution_id": "<exact BK-* ID>",
  "module_scope": "fcst",
  "sensitivity_scope": "confidential",
  "reason": "<institution-approved reason>",
  "expected_authority_sentence": "<server preview response>"
}
```

Do not infer rows from scalar roles, combine partial rows, grant machine
principals, or reuse authority approved for another module.

## Release record

Attach all of the following before deployment:

1. the dated inventory output for every organization and institution;
2. the exact human and machine principals affected, including everyone denied;
3. the institution-approved duty for every new binding;
4. the exact rows created, including grantor, reason, validity, and scope;
5. confirmation that users whose `authv` changed signed in again; and
6. browser evidence for an unbound user, an aggregated-view reader, a
   confidential-view reader with every run control disabled, and an exactly
   Forecasting-bound analyst with the run actions enabled.
