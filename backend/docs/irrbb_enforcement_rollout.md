# IRRBB scoped-binding enforcement rollout

This is the deployment gate for the IRRBB cutover. It does not authorize a
backfill. Operators must inventory real principals, confirm duties with the
institution, and create each approved binding explicitly before deployment.

## Affected surfaces

| Surface                                                                                                  | Required complete binding         |
| -------------------------------------------------------------------------------------------------------- | --------------------------------- |
| IRRBB projections in live summary, snapshots, alerts, and window analytics                               | IRRBB / `aggregated` / `view`     |
| Activation with calculations and on-demand official runs, when `module_scope.runs_module` includes IRRBB | IRRBB / `confidential` / `run`    |
| IRRBB dashboards and regulatory-run index                                                                | IRRBB / `aggregated` / `view`     |
| Full IRRBB run detail, scenario catalogue, and saved-analysis index/detail                               | IRRBB / `confidential` / `view`   |
| Run all regulatory scenarios, EaR compute-only analysis, and scenario analysis execution                 | IRRBB / `confidential` / `run`    |
| Create a custom scenario or saved analysis                                                               | IRRBB / `confidential` / `create` |
| Edit or archive a custom scenario; delete a saved analysis                                               | IRRBB / `confidential` / `edit`   |

The shared regulatory-run registry filters unauthorized IRRBB rows before total
counts, offsets, and limits. The IRRBB saved-analysis index denies unauthorized
requests before querying or counting rows. Unauthorized run and saved-analysis
detail IDs return 404. A denied request cannot
start an engine, create a run, save an analysis, mutate a scenario, or write an
audit event.

Shared live projections filter IRRBB before serialization, counts, limits, and
aggregation. Mixed execution retains its existing gates and checks IRRBB only
when planned; internal scheduled execution is unchanged.

No scalar role, token role, tenant membership, or binding for another module or
sensitivity grants IRRBB authority.

## Dashboard controls

The `/irr` dashboards require aggregated view; `/irr/scenarios` requires
confidential view. Run buttons and the sensitivity analysis horizon
control consume the exact confidential run capability from effective authority.
The shared permission-only disabled-control policy is defined in
[the RBAC guide](../../docs/rbac.md); native disabled controls expose their
explanation through a keyboard-focusable wrapper.

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
 AND ab.module_scope IN ('irrbb', 'all')
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
2. the user and confirmed IRRBB duty;
3. the affected surface;
4. whether one complete active row already grants the required tuple; and
5. whether deployment will deny the user until a row is approved.

Machine principals receive no IRRBB grant in this cutover. Scheduled internal
live computation does not impersonate a tenant user.

## Exact binding rows

Create only rows approved from the inventory.

| Duty                                           | `principal_type` | `role_bundle`                                 | `institution_scope` | `institution_id` | `module_scope` | `sensitivity_scope` |
| ---------------------------------------------- | ---------------- | --------------------------------------------- | ------------------- | ---------------- | -------------- | ------------------- |
| Read IRRBB dashboards                          | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `irrbb`        | `aggregated`        |
| Read run and workbench detail                  | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `irrbb`        | `confidential`      |
| Run engines and create/edit scenario artifacts | `human`          | `analyst`                                     | `institution`       | exact `BK-*`     | `irrbb`        | `confidential`      |

If the institution explicitly approves coverage across all its banks, replace
only `institution_scope` with `organization` and `institution_id` with `NULL`.
Do not widen module or sensitivity scope to compensate for a missing row.
Aggregated and confidential access are separate rows unless the institution
explicitly approves the broad `all` sensitivity.

Example least-privilege request for an IRRBB run maker:

```json
{
  "principal_user_id": "<confirmed human user UUID>",
  "role_bundle": "analyst",
  "institution_scope": "institution",
  "institution_id": "<exact BK-* ID>",
  "module_scope": "irrbb",
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
6. browser evidence for an unbound user, an aggregated-view reader, and an
   exactly IRRBB-bound analyst with the run action enabled.
