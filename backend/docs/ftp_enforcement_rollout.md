# FTP scoped-binding enforcement rollout

This is the deployment gate for the Funds Transfer Pricing cutover. It does not
authorize a backfill. Operators must inventory real principals, confirm duties
with the institution, and create each approved binding explicitly before
deployment.

## Affected surfaces

| Surface                                                                                                | Required complete binding       |
| ------------------------------------------------------------------------------------------------------ | ------------------------------- |
| FTP projections in live summary, snapshots, alerts, and window analytics                               | FTP / `aggregated` / `view`     |
| Activation with calculations and on-demand official runs, when `module_scope.runs_module` includes FTP | FTP / `confidential` / `run`    |
| FTP dashboards, regulatory-run index, and saved-analysis index                                         | FTP / `aggregated` / `view`     |
| Full FTP run detail, scenario catalogue, and saved-analysis detail                                     | FTP / `confidential` / `view`   |
| Run all regulatory scenarios and execute scenario analysis                                             | FTP / `confidential` / `run`    |
| Create a custom scenario or saved analysis                                                             | FTP / `confidential` / `create` |
| Edit or archive a custom scenario; delete a saved analysis                                             | FTP / `confidential` / `edit`   |

The shared regulatory-run registry filters unauthorized FTP rows before total
counts, offsets, and limits. The FTP saved-analysis index denies unauthorized
requests before querying or counting rows. Unauthorized run and saved-analysis
detail IDs return 404. A denied request cannot start an engine, create a run,
save an analysis, mutate a scenario, enqueue work, derive facts, or write an
audit event.

Shared live projections filter FTP before serialization, counts, limits, and
aggregation. Mixed execution retains its existing gates and checks FTP only
when planned. Scheduled official-run actor selection and queue attribution
follow the
[FX queued-run contract](fx_enforcement_rollout.md#queued-and-scheduled-official-runs).

No scalar role, token role, tenant membership, or binding for another module or
sensitivity grants FTP authority. FTP rule and methodology configuration
remains outside this cutover unless a mutation surface is introduced.

## Dashboard controls

The `/ftp` dashboards require aggregated view; `/ftp/scenarios` requires
confidential view. Both the dashboard's run-all action and the enterprise stress
run control mounted at `/ftp/scenarios` consume the exact confidential run
capability from effective authority. Without it, they remain visible and disabled
with “Requires Funds Transfer Pricing · Confidential · Run. Ask your organization
owner or admin to grant it.” The shared permission-only
disabled-control policy is defined in [the RBAC guide](../../docs/rbac.md);
native disabled controls expose their explanation through a
keyboard-focusable wrapper.

Reports → Saved Analyses requests FTP summaries only with aggregated view.
Cross-module board-pack and limit-monitor queries use the same FTP route access
decision, so they do not issue FTP requests for an unauthorized user.

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
 AND ab.module_scope IN ('ftp', 'all')
 AND (
      (ab.institution_scope = 'institution' AND ab.institution_id = b.id)
      OR
      (ab.institution_scope = 'organization' AND ab.institution_id IS NULL)
 )
WHERE b.organization_id = u.organization_id
ORDER BY u.organization_id, b.id, u.email, ab.role_bundle, ab.sensitivity_scope;
```

For every active human principal who uses an affected surface, record:

1. the organization and exact institution;
2. the user and confirmed FTP duty;
3. the affected surface;
4. whether one complete active row already grants the required tuple; and
5. whether deployment will deny the user until a row is approved.

Record machine principals separately. They receive no interactive FTP grant in
this cutover. Scheduled internal live computation does not impersonate a tenant
user.

## Exact binding rows

Create only rows approved from the inventory through the authorization service.
Follow the foundation's
[authorization-version and session transition contract](authorization_foundation.md#authorization-version-and-deployment-transition).

| Duty                                           | `principal_type` | `role_bundle`                                 | `institution_scope` | `institution_id` | `module_scope` | `sensitivity_scope` |
| ---------------------------------------------- | ---------------- | --------------------------------------------- | ------------------- | ---------------- | -------------- | ------------------- |
| Read FTP dashboards and summary indexes        | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `ftp`          | `aggregated`        |
| Read run and workbench detail                  | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `ftp`          | `confidential`      |
| Run engines and create/edit scenario artifacts | `human`          | `analyst`                                     | `institution`       | exact `BK-*`     | `ftp`          | `confidential`      |

If the institution explicitly approves coverage across all its banks, replace
only `institution_scope` with `organization` and `institution_id` with `NULL`.
Do not widen module or sensitivity scope to compensate for a missing row.
Aggregated and confidential access are separate rows unless the institution
explicitly approves the broad `all` sensitivity.

For a run maker, use Analyst / FTP / confidential at the exact institution.
Use the [grant submission contract](authorization_foundation.md#structured-grant-reasons)
for the payload, reason fields, and preview/create sequence.

Do not infer rows from scalar roles, combine partial rows, grant machine
principals, or reuse authority approved for another module.

## Release record

Attach all of the following before deployment:

1. the dated inventory output for every organization and institution;
2. the exact human and machine principals affected, including everyone denied;
3. the institution-approved duty for every new binding;
4. the exact rows created, including grantor, reason, validity, and scope;
5. confirmation that users whose `authv` changed signed in again; and
6. browser evidence for an unbound user, an aggregated-view reader, and an
   exactly FTP-bound analyst with the run action enabled.
