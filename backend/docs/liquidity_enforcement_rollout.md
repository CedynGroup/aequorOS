# Liquidity scoped-binding enforcement rollout

Issue #165 cuts the remaining Liquidity product surfaces over from scalar roles
to exact authorization bindings. Run this inventory against each target
deployment immediately before release. Store the dated output with the release
record. Do not copy production identities into this repository.

No migration or application startup creates compatibility grants. A principal
without one complete active row for the consumed institution, LIQ module,
sensitivity, and permission is denied immediately (403 unless the object-hiding
404 contract below applies). This cutover requires no new migration.

## Affected authority

| Surface | Required authority |
| --- | --- |
| Liquidity dashboard, live summaries/history, alerts, saved-analysis summaries, and regulatory-run list rows | LIQ / `aggregated` / `view` |
| Full regulatory-run and saved-analysis detail, and BSD-3 preview | LIQ / `confidential` / `view` |
| Monitoring Tools, EWI dashboard, CFP reads/events, forecasts/history, cash-flow window, thresholds, haircuts, SDI liquidity position, and scenario details | LIQ / `confidential` / `view` |
| Create one or all Liquidity regulatory runs; execute scenario analysis | LIQ / `confidential` / `run` |
| Create a CFP draft, custom scenario, or saved analysis | LIQ / `confidential` / `create` |
| Update a CFP draft or custom scenario; archive a scenario; delete a saved analysis | LIQ / `confidential` / `edit` |
| Approve, activate, or de-escalate a CFP | LIQ / `confidential` / `approve`, with maker-checker still a global veto for approval |

Regulatory-run lists remove unauthorized LIQ rows before total counts, offsets,
and limits. Live summaries omit unauthorized Liquidity metrics; alerts omit
Liquidity findings before counts and limits. Liquidity live-history requests
require aggregated view. Full LIQ run and saved-analysis IDs return 404 without
confidential authority.
Denied run and CFP lifecycle requests create no run, event, audit mutation, job,
or notification.

Tenant-initiated `POST /banks/{bank_id}/official-runs` ("Mint for filing")
and `POST /banks/{bank_id}/data-activations` with `run_calculations=true`
also require LIQ/confidential/run before enqueueing or deriving facts when
`module_scope.runs_module` includes Liquidity. These mixed-module operations
retain their existing mutation gate as well. SDI plans that exclude the
Liquidity engine do not acquire this additional run requirement.

Tenant-owned bank resolution precedes the binding decision: unknown or
cross-tenant institutions return 404. The evaluator emits
`authz.binding_decision` with target, outcome, matching binding IDs, and
per-binding reasons; evaluation failure records `binding_evaluation_failed` and
denies closed. Operator impersonation
and integration keys do not satisfy the interactive human-binding gate.

The EWI, Liquidity threshold, and Liquidity haircut PUT routes remain on their
existing configuration gate until issue #184. Scheduled internal live
computation does not impersonate a user and receives no binding; this cutover
checks tenant-facing entry points while scheduled computation retains its
existing behavior. Capital, Credit, and RISK-owned enterprise-stress backend
enforcement remain outside this cutover.

## Dashboard access

Navigation, deep links, action controls, and background requests consume the
server's effective-authority projection. Sensitive query keys and invalidations
use the tenant, actor, `authv`, and institution partition defined in
[`authorization_foundation.md`](authorization_foundation.md). Grant changes
invalidate sessions; users must sign in again before using the new authority.

An aggregated-only viewer can read summaries, but the cockpit labels EWI and
CFP sections restricted and omits threshold-dependent headroom assessments
when confidential inputs are unavailable. Saved-analysis workbench links use
the same visibility decision as navigation. The shared `/liquidity/stress`
workbench requires both LIQ/confidential/view and RISK/confidential/view for
navigation; this does not change RISK backend enforcement. The SDI Liquidity
landing requires confidential view because it reads the SDI position.

## Deny-impact inventory

Run this read-only query with a role that can see all organizations, banks,
users, and bindings. It evaluates active human and machine principals without
inferring authority from `users.role`.

```sql
WITH active_bindings AS (
    SELECT
        b.organization_id,
        b.principal_user_id,
        b.principal_type,
        b.role_bundle,
        b.institution_scope,
        b.institution_id,
        b.module_scope,
        b.sensitivity_scope
    FROM authorization_bindings AS b
    WHERE b.status = 'active'
      AND b.revoked_at IS NULL
      AND b.valid_from <= CURRENT_TIMESTAMP
      AND (b.valid_until IS NULL OR b.valid_until > CURRENT_TIMESTAMP)
),
principal_institutions AS (
    SELECT
        u.organization_id,
        u.id AS principal_user_id,
        CASE
            WHEN u.auth_provider = 'service' THEN 'machine'
            ELSE 'human'
        END AS principal_type,
        u.email,
        u.auth_provider,
        u.role AS scalar_role,
        bank.id AS institution_id,
        bank.name AS institution_name
    FROM users AS u
    JOIN banks AS bank
      ON bank.organization_id = u.organization_id
    WHERE u.is_active IS TRUE
),
authority AS (
    SELECT
        p.*,
        COALESCE(
            bool_or(
                b.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
                AND b.sensitivity_scope IN ('aggregated', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_view_aggregated,
        COALESCE(
            bool_or(
                b.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
                AND b.sensitivity_scope IN ('confidential', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_view_confidential,
        COALESCE(
            bool_or(
                b.role_bundle = 'analyst'
                AND b.sensitivity_scope IN ('confidential', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_run_create_edit,
        COALESCE(
            bool_or(
                b.role_bundle = 'approver'
                AND b.sensitivity_scope IN ('confidential', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_approve
    FROM principal_institutions AS p
    LEFT JOIN active_bindings AS b
      ON b.organization_id = p.organization_id
     AND b.principal_user_id = p.principal_user_id
     AND b.module_scope IN ('liq', 'all')
     AND (
          (b.institution_scope = 'organization' AND b.institution_id IS NULL)
          OR
          (b.institution_scope = 'institution' AND b.institution_id = p.institution_id)
     )
    GROUP BY
        p.organization_id,
        p.principal_user_id,
        p.principal_type,
        p.email,
        p.auth_provider,
        p.scalar_role,
        p.institution_id,
        p.institution_name
)
SELECT
    organization_id,
    institution_id,
    institution_name,
    principal_user_id,
    principal_type,
    email,
    auth_provider,
    scalar_role,
    may_view_aggregated,
    may_view_confidential,
    may_run_create_edit,
    may_approve,
    CASE
        WHEN principal_type = 'machine' THEN
            'denied: interactive human binding required'
        WHEN may_view_aggregated OR may_view_confidential
             OR may_run_create_edit OR may_approve THEN
            'partially or fully allowed: compare flags with actual duties'
        ELSE
            'denied: no active exact LIQ binding'
    END AS cutover_result
FROM authority
ORDER BY organization_id, institution_id, principal_type, email;
```

Review each row with the institution. Record every person whose actual duty
uses an affected surface but whose matching flag is false. Record machine
principals separately; do not create a human bundle for a service account.

## Exact binding rows

Create a row only after the institution confirms the person, institution, duty,
and sensitivity. Use the authorization service so `authv` advances and refresh
families are revoked in the same transaction.

| Confirmed need | `principal_type` | `role_bundle` | `institution_scope` | `institution_id` | `module_scope` | `sensitivity_scope` |
| --- | --- | --- | --- | --- | --- | --- |
| Read dashboard summaries and LIQ rows in the shared run registry | `human` | `viewer` or institution-approved `auditor`/`analyst`/`approver` | `institution` | exact `BK-*` | `liq` | `aggregated` |
| Read forecasts, CFP, thresholds, haircuts, SDI positions, scenario details, and full run snapshots | `human` | `viewer` or institution-approved `auditor`/`analyst`/`approver` | `institution` | exact `BK-*` | `liq` | `confidential` |
| Create runs and analyses; create/edit CFP drafts and scenario artifacts | `human` | `analyst` | `institution` | exact `BK-*` | `liq` | `confidential` |
| Approve, activate, and de-escalate CFPs | `human` | `approver` | `institution` | exact `BK-*` | `liq` | `confidential` |

If an institution explicitly approves coverage across all its banks, replace
only `institution_scope` with `organization` and `institution_id` with `NULL`.
Do not widen module or sensitivity scope to compensate for a missing row.
Create separate aggregated and confidential rows when the person's duties
consume both. The explicit `all` value remains a broad grant, not a shortcut
the rollout may infer.

Example least-privilege request for a run maker:

```json
{
  "principal_user_id": "<confirmed human user UUID>",
  "role_bundle": "analyst",
  "institution_scope": "institution",
  "institution_id": "<exact BK-* ID>",
  "module_scope": "liq",
  "sensitivity_scope": "confidential",
  "reason": "<institution-approved reason>",
  "expected_authority_sentence": "<server preview response>"
}
```

Do not backfill from scalar roles, create bindings for scheduled internal
workers, combine partial rows into a synthetic permission, or reuse a grant
approved for another module.

## Release record

Attach all of the following before deployment:

1. The dated inventory output for every organization and institution.
2. The exact human and machine principals that the cutover will deny.
3. The institution-approved duty for every new binding.
4. The exact binding rows created, including grantor, reason, validity, and scope.
5. Confirmation that users whose `authv` changed signed in again.
6. Reviewer-visible browser evidence for an unbound user and an exactly
   LIQ-bound user.
