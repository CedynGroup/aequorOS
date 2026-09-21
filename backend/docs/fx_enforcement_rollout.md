# FX scoped-binding enforcement rollout

FX enforcement replaces scalar role checks with independently complete
authorization bindings. Run this inventory against each target deployment
immediately before release. Store the dated output with the deployment record.
Do not copy production identities into this repository.

## Affected surfaces

The FX dashboard and aggregated `/fx` tabs require FX `view` with sensitivity
`aggregated`:

- `GET /api/v1/banks/{bank_id}/fx/dashboard`
- `/fx`
- `/fx/var`
- `/fx/hedges`
- `/fx/limits`
- `/fx/forwards`

FX regulatory-run and saved-analysis lists use FX `view` with sensitivity
`aggregated`. Their details and the `/fx/scenarios` catalogue use FX `view` with
sensitivity `confidential`. Regulatory-run lists remove unauthorized FX rows before
counts and pagination; saved-analysis lists and scenario catalogues deny with 403
without their required view authority. Unauthorized FX run and saved-analysis
detail IDs return 404. Scenario mutation permission denials return 403.

Shared live summaries and alerts omit FX metrics and findings without
FX/aggregated/view, including their counts. Analytics windows exclude FX daily
snapshots before computing statistics while retaining non-FX results. Explicit
`/live-snapshots?module=fx` requests require the same aggregated view authority.

Running the FX scenario batch and compute-only FX analysis requires FX `run`
with sensitivity `confidential`. Enterprise stress runs with `include_fx=true`
(default) require that same FX permission in the service before input reads or
persistence, in addition to the
[shared enterprise execution gates](irrbb_enforcement_rollout.md#affected-surfaces).
Runs explicitly excluding FX retain those shared gates. Creating a custom FX scenario or saved analysis
requires FX `create`; changing, archiving, or deleting one requires FX `edit`,
all at that same sensitivity. No route infers authority from `users.role`, token
`roles[]`, a different module, a different sensitivity, or separate partial
bindings.

Tenant-initiated `POST /banks/{bank_id}/official-runs` and data activation with
`run_calculations=true` also require FX/confidential/run when
`module_scope.runs_module` includes FX. These checks precede job enqueueing or
fact derivation and retain the existing mutation gate. Activation without
calculations does not acquire this FX requirement.

FX authority is institution/module scoped in v1. A binding cannot narrow access
to one treasury desk or one currency because those resource attributes do not
exist in the canonical authorization locator. Do not encode desk or currency
names in grant reasons as if they were enforced scope.

### Queued and scheduled official runs

User-requested jobs retain the initiating `actor_user_id`. For scheduled runs,
the scheduler selects the first active human principal (ordered by creation
time, then ID) with independently complete confidential/run authority covering
that institution for each of FX, FTP, and Forecasting included by
`module_scope.runs_module`. The same principal must satisfy every planned
module's requirement. If none exists,
it skips the bank's official-run enqueue and emits
`no_authorized_scheduled_principal` on the `scheduled_official_run` surface.
For banks excluding all three modules, it selects the first active human
without any module check.

The worker resolves the recorded actor in the job's organization, requires that
actor to remain active and human, and loads their current authorization version.
The FX, FTP, and Forecasting run services still check current binding
authority before execution; enqueueing does not grant permanent authority or
bypass the service gates. When FTP or Forecasting is planned, the worker also
preflights that module's confidential/run authority before period lookup, fact
derivation, or dispatching any module.
Scheduled jobs use `scheduled-official:{bank_id}:{tick_date}`, while user-requested
jobs use `official:{bank_id}:{as_of_date}`. Coalescing remains within each source so a
scheduler tick cannot replace an interactive job's actor or attribution.
Behavioral coverage lives in `tests/services/test_scheduler.py` and
`tests/api/test_fx_authorization.py`, `tests/api/test_ftp_authorization.py`, and
`tests/api/test_forecasting_authorization.py`.

## Dashboard access

FX navigation and deep links follow the server's effective-authority projection.
For an entitled institution, a missing FX view permission leaves the navigation
link visible but disabled, with a tooltip naming the required grant. Structural
exclusions and institutions without authority remain hidden.
A denied `/fx` deep link returns the dashboard's not-found view and sends no FX
dashboard query. Command Center, Risk, Alerts, and Board Pack request FX data
only when the same FX/aggregated view capability is present.

Reports requests FX saved analyses and exposes the FX official-run filter only
when FX/aggregated view is present. Saved-analysis workbench links still require
FX/confidential view. The enterprise stress workbench keeps an
unavailable FX action visible but disabled. Its tooltip names the exact
FX/confidential `run` permission and says that an organization owner can grant
it. After a grant change, sign in again to obtain the new authorization version.

## Inventory

Run this read-only query as a role that can see every tenant user, institution,
and binding. It enumerates human and machine principals without inferring any
grant from scalar roles.

```sql
WITH active_principals AS (
    SELECT
        u.organization_id,
        u.id AS principal_user_id,
        CASE
            WHEN u.auth_provider = 'service' THEN 'machine'
            ELSE 'human'
        END AS principal_type,
        u.email,
        u.auth_provider,
        u.role AS scalar_role
    FROM users AS u
    WHERE u.is_active IS TRUE
),
targets AS (
    SELECT
        p.*,
        b.id AS institution_id,
        b.name AS institution_name
    FROM active_principals AS p
    JOIN banks AS b ON b.organization_id = p.organization_id
),
active_bindings AS (
    SELECT *
    FROM authorization_bindings
    WHERE status = 'active'
      AND revoked_at IS NULL
      AND valid_from <= CURRENT_TIMESTAMP
      AND (valid_until IS NULL OR valid_until > CURRENT_TIMESTAMP)
),
authority AS (
    SELECT
        t.organization_id,
        t.institution_id,
        t.institution_name,
        t.principal_user_id,
        t.principal_type,
        t.email,
        t.auth_provider,
        t.scalar_role,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
            AND a.module_scope IN ('fx', 'all')
            AND a.sensitivity_scope IN ('aggregated', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS fx_aggregated_view,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
            AND a.module_scope IN ('fx', 'all')
            AND a.sensitivity_scope IN ('confidential', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS fx_confidential_view,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle = 'analyst'
            AND a.module_scope IN ('fx', 'all')
            AND a.sensitivity_scope IN ('confidential', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS fx_run_create_edit
    FROM targets AS t
    LEFT JOIN active_bindings AS a
      ON a.organization_id = t.organization_id
     AND a.principal_user_id = t.principal_user_id
    GROUP BY
        t.organization_id,
        t.institution_id,
        t.institution_name,
        t.principal_user_id,
        t.principal_type,
        t.email,
        t.auth_provider,
        t.scalar_role
)
SELECT
    *,
    CASE
        WHEN principal_type = 'machine' THEN
            'denied: FX routes require a human binding'
        WHEN fx_aggregated_view OR fx_confidential_view OR fx_run_create_edit THEN
            'partially or fully allowed: review columns for exact surfaces'
        ELSE
            'denied: no active exact FX binding'
    END AS fx_result
FROM authority
ORDER BY organization_id, institution_id, principal_type, email;
```

Review the output with each institution. Explicitly list every human and
machine principal that will be denied. Machine principals are included so the
release record proves no integration was silently assumed to have FX authority.

## Exact binding rows

Create no automatic backfill. Operators must create only institution-approved
rows through the authorization service so `authv` advances and refresh-token
families are revoked in the same transaction.

| Need                                                                        | `principal_type` | `role_bundle`                                 | `institution_scope`                      | `institution_id`                             | `module_scope` | `sensitivity_scope` |
| --------------------------------------------------------------------------- | ---------------- | --------------------------------------------- | ---------------------------------------- | -------------------------------------------- | -------------- | ------------------- |
| FX dashboards, navigation, run/analysis lists, and aggregated report blocks | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution` or explicit `organization` | exact `BK-*` or `NULL` for organization-wide | `fx`           | `aggregated`        |
| FX scenario catalogue and run/saved-analysis detail                         | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution` or explicit `organization` | exact `BK-*` or `NULL`                       | `fx`           | `confidential`      |
| Run FX, compute analyses, and create/edit FX workbench entries              | `human`          | `analyst`                                     | `institution` or explicit `organization` | exact `BK-*` or `NULL`                       | `fx`           | `confidential`      |

An Analyst FX/confidential row grants `view`, `create`, `edit`, and `run`, but
does not grant FX/aggregated because sensitivity is exact. Add the aggregated
row only when the institution confirms dashboard access.

The common least-privilege operator therefore needs two independently complete
rows:

```json
[
  {
    "principal_user_id": "<confirmed human user UUID>",
    "role_bundle": "viewer",
    "institution_scope": "institution",
    "institution_id": "<exact BK-*>",
    "module_scope": "fx",
    "sensitivity_scope": "aggregated",
    "reason": "<institution-approved reason>",
    "expected_authority_sentence": "<server preview response>"
  },
  {
    "principal_user_id": "<same confirmed human user UUID>",
    "role_bundle": "analyst",
    "institution_scope": "institution",
    "institution_id": "<same exact BK-*>",
    "module_scope": "fx",
    "sensitivity_scope": "confidential",
    "reason": "<institution-approved reason>",
    "expected_authority_sentence": "<server preview response>"
  }
]
```

Do not replace these with incomplete rows, combine fields across rows, copy
production users into code, or widen scope to `all` to compensate for a missing
decision.

## Release record

Before deployment, attach:

1. The dated inventory output for every organization and institution.
2. The exact list of affected human and machine principals.
3. The exact list of principals who will be denied on each FX surface.
4. Every institution-approved binding row created before release.
5. Confirmation that no desk or currency scope was promised in v1.
6. Confirmation that every user whose `authv` changed signed in again.

## Enterprise result visibility

Generic regulatory-run summaries project enterprise FX results using aggregated
FX view; generic details and enterprise latest/detail use confidential FX view.
Without the matching permission, responses omit the FX outcome, FX plan and
scenario inputs, Appendix II Table 6 FX risk-driver rows, and the country/FX
charge. Dependent totals are omitted only for rows with a non-null FX charge;
rows with null charges retain their totals, including runs with FX excluded.
Non-FX risk-driver rows and results remain visible, including capital and liquidity
outcomes, projections, and other Appendix II fields. Every valid enterprise run
contains these non-FX results, so its registry entry and count remain visible.
Projection changes response copies only; immutable stored inputs, metrics, and
hashes are unchanged.
