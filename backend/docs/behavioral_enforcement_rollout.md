# Behavioral scoped-binding enforcement rollout

This is the deployment gate for the Behavioral Models cutover. It does not
authorize a backfill. Operators must inventory real principals, confirm duties
with the institution, and create each approved binding explicitly before
deployment.

## Affected surfaces

| Surface                                                                             | Required complete binding    |
| ----------------------------------------------------------------------------------- | ---------------------------- |
| Model estimates (`GET /banks/{id}/behavioral/{model}`) and the `/behavioral` pages  | BEH / `aggregated` / `view`  |
| Observed deposit behavior and CFP overlays (`GET /banks/{id}/behavioral/liquidity`) | BEH / `aggregated` / `view`  |
| Retrain a model (`POST /banks/{id}/behavioral/{model}/train`)                       | BEH / `confidential` / `run` |

Applying estimates as reviewed assumptions
(`POST /banks/{id}/behavioral/{model}/apply`) is outside this cutover. It
writes an accepted `behavioral_assumptions` ingestion batch, so it moves with
the governed model-application work; until then it keeps its existing
mutation check.

A model estimate is read lazily: the first authorized read of a model fits it
on the bank's canonical history and persists the result for reuse. That fill
happens only after the `view` decision allows the read, and a denied read never
trains. Retraining is the governed run: the route requires `run`, and the
service re-decides the same binding before it trains or rewrites the artifact,
so a call that reaches the service by another path still cannot train.

A denied request cannot train a model, write or replace an artifact, warm the
cache, write an ingestion batch, or write an audit event. Unknown model names
are rejected by the route's closed schema for every caller, authorized or not,
before any binding is evaluated. A bank the caller's organization does not own
returns 404 in the same shape as every other bank route. A stale session
(`authv` behind the user's current version) is refused with 401 before any
Behavioral evaluation.

No scalar role, token role, tenant membership, or binding for another module or
sensitivity grants Behavioral authority. Aggregated view does not imply
confidential run, and confidential run does not imply aggregated view: an
analyst who retrains also needs the view row to see the result.

Integration keys are refused at the credential boundary on every Behavioral
route; machine principals receive no interactive Behavioral grant in this
cutover.

## Dashboard controls

Every `/behavioral` page requires aggregated view. Without it the Behavioral
navigation entry stays visible but disabled with “Requires Behavioral Models ·
Aggregated · View. Ask your organization owner or admin to grant it.” Deep links
answer 404, and no Behavioral request is issued. The Retrain control on each model page consumes the exact confidential
run capability from effective authority. Without it the control remains visible
and disabled with “Requires Behavioral Models · Confidential · Run. Ask your
organization owner or admin to grant it.” The shared permission-only
disabled-control policy is defined in [the RBAC guide](../../docs/rbac.md);
native disabled controls expose their explanation through a keyboard-focusable
wrapper.

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
 AND ab.module_scope IN ('beh', 'all')
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
2. the user and confirmed Behavioral duty;
3. the affected surface;
4. whether one complete active row already grants the required tuple; and
5. whether deployment will deny the user until a row is approved.

Record machine principals separately. They receive no interactive Behavioral
grant in this cutover.

## Exact binding rows

Create only rows approved from the inventory through the authorization service.
Follow the foundation's
[authorization-version and session transition contract](authorization_foundation.md#authorization-version-and-deployment-transition).

| Duty                                        | `principal_type` | `role_bundle`                                 | `institution_scope` | `institution_id` | `module_scope` | `sensitivity_scope` |
| ------------------------------------------- | ---------------- | --------------------------------------------- | ------------------- | ---------------- | -------------- | ------------------- |
| Read model estimates and liquidity behavior | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `beh`          | `aggregated`        |
| Retrain models                              | `human`          | `analyst`                                     | `institution`       | exact `BK-*`     | `beh`          | `confidential`      |

If the institution explicitly approves coverage across all its banks, replace
only `institution_scope` with `organization` and `institution_id` with `NULL`.
Do not widen module or sensitivity scope to compensate for a missing row.
Aggregated and confidential access are separate rows unless the institution
explicitly approves the broad `all` sensitivity; an analyst who retrains needs
both.

Example least-privilege request for a model trainer:

```json
{
  "principal_user_id": "<confirmed human user UUID>",
  "role_bundle": "analyst",
  "institution_scope": "institution",
  "institution_id": "<exact BK-* ID>",
  "module_scope": "beh",
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
6. browser evidence for an unbound user, an aggregated-view reader with the
   Retrain control disabled, and an exactly Behavioral-bound analyst with the
   Retrain control enabled.
