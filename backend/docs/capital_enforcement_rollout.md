# Capital scoped-binding enforcement rollout

Capital enforcement replaces scalar role checks with independently complete
authorization bindings. Run this inventory against each target deployment
immediately before release. Store the dated output with the deployment record.
Do not copy production identities into this repository.

## Affected surfaces

The following institution-scoped reads require CAP `view` with sensitivity
`aggregated`:

- `GET /api/v1/banks/{bank_id}/capital/dashboard`
- `GET /api/v1/banks/{bank_id}/capital/rwa`
- `GET /api/v1/banks/{bank_id}/capital/structure`
- `GET /api/v1/banks/{bank_id}/sdi/capital-checks`
- `GET /api/v1/banks/{bank_id}/sdi/capital-summary`

The capital plan, ILAAP snapshot reads, BSD capital preview, and Capital
regulatory-run details require CAP `view` with sensitivity `confidential`.
SDI capital assurance requires CAP `view` with sensitivity `restricted`.
Unauthorized Capital run IDs return 404, and Capital rows are removed before
regulatory-run counts and pagination are calculated.

Capital execution requires CAP `run` with sensitivity `confidential`.
Capital-plan draft creation and revision require CAP `create` and `edit`
respectively at that same sensitivity. Approval requires CAP `approve` and an
independent checker. ILAAP refresh additionally requires a second, independent
LIQ `view` decision with sensitivity `confidential`; neither decision may use
scope fields from the other binding.

Capital entries in `/scenario-workbench/capital` apply the same CAP
`run`/`create`/`view`/`edit` split. No route infers authority from `users.role`,
token `roles[]`, a different module, a different sensitivity, or separate
partial bindings.

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
            AND a.module_scope IN ('cap', 'all')
            AND a.sensitivity_scope IN ('aggregated', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS cap_aggregated_view,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
            AND a.module_scope IN ('cap', 'all')
            AND a.sensitivity_scope IN ('confidential', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS cap_confidential_view,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
            AND a.module_scope IN ('cap', 'all')
            AND a.sensitivity_scope IN ('restricted', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS cap_restricted_view,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle = 'analyst'
            AND a.module_scope IN ('cap', 'all')
            AND a.sensitivity_scope IN ('confidential', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS cap_maker,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle = 'approver'
            AND a.module_scope IN ('cap', 'all')
            AND a.sensitivity_scope IN ('confidential', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS cap_approver,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
            AND a.module_scope IN ('liq', 'all')
            AND a.sensitivity_scope IN ('confidential', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS liq_confidential_view
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
            'denied: Capital routes require a human binding'
        WHEN cap_aggregated_view OR cap_confidential_view
             OR cap_restricted_view OR cap_maker OR cap_approver THEN
            'partially or fully allowed: review columns for exact surfaces'
        ELSE
            'denied: no active exact CAP binding'
    END AS capital_result,
    CASE
        WHEN cap_maker AND liq_confidential_view THEN
            'allowed: ILAAP refresh'
        ELSE
            'denied: ILAAP needs CAP maker and LIQ confidential view'
    END AS ilaap_refresh_result
FROM authority
ORDER BY organization_id, institution_id, principal_type, email;
```

Review the output with each institution. Explicitly list every human and
machine principal that will be denied. Machine principals are included so the
release record proves no integration was silently assumed to have Capital
authority.

## Exact binding rows

Create no automatic backfill. Operators must create only institution-approved
rows through the authorization service so `authv` advances and refresh-token
families are revoked in the same transaction.

| Need                                                                          | `principal_type` | `role_bundle`                                 | `institution_scope`                      | `institution_id`                             | `module_scope` | `sensitivity_scope` |
| ----------------------------------------------------------------------------- | ---------------- | --------------------------------------------- | ---------------------------------------- | -------------------------------------------- | -------------- | ------------------- |
| Aggregated Capital dashboards and SDI checks                                  | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution` or explicit `organization` | exact `BK-*` or `NULL` for organization-wide | `cap`          | `aggregated`        |
| Capital plans, BSD preview, run details, and ILAAP reads                      | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution` or explicit `organization` | exact `BK-*` or `NULL`                       | `cap`          | `confidential`      |
| SDI capital assurance evidence                                                | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution` or explicit `organization` | exact `BK-*` or `NULL`                       | `cap`          | `restricted`        |
| Run Capital, create/edit plans, and run/create/edit Capital workbench entries | `human`          | `analyst`                                     | `institution` or explicit `organization` | exact `BK-*` or `NULL`                       | `cap`          | `confidential`      |
| Approve a Capital plan as an independent checker                              | `human`          | `approver`                                    | `institution` or explicit `organization` | exact `BK-*` or `NULL`                       | `cap`          | `confidential`      |
| Read Liquidity evidence consumed by ILAAP refresh                             | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | same target form as the CAP row          | same exact `BK-*` or `NULL`                  | `liq`          | `confidential`      |

An Analyst CAP/confidential row grants `view`, `create`, `edit`, and `run`, but
does not grant CAP/aggregated or CAP/restricted because sensitivity is exact.
Add those read rows only when the institution confirms the corresponding
surface. An Approver CAP/confidential row grants `view`, `review`, and
contextual `approve`; maker-checker is still evaluated against the specific
plan.

The least-privilege ILAAP operator therefore needs two independently complete
rows:

```json
[
  {
    "principal_user_id": "<confirmed human user UUID>",
    "role_bundle": "analyst",
    "institution_scope": "institution",
    "institution_id": "<exact BK-*>",
    "module_scope": "cap",
    "sensitivity_scope": "confidential",
    "reason": "<institution-approved reason>",
    "expected_authority_sentence": "<server preview response>"
  },
  {
    "principal_user_id": "<same confirmed human user UUID>",
    "role_bundle": "viewer",
    "institution_scope": "institution",
    "institution_id": "<same exact BK-*>",
    "module_scope": "liq",
    "sensitivity_scope": "confidential",
    "reason": "<institution-approved reason>",
    "expected_authority_sentence": "<server preview response>"
  }
]
```

Do not replace these with one incomplete row, combine fields across rows, copy
production users into code, or widen scope to `all` to compensate for a missing
decision.

## Release record

Before deployment, attach:

1. The dated inventory output for every organization and institution.
2. The exact list of affected human and machine principals.
3. The exact list of principals who will be denied on each Capital surface.
4. Every institution-approved binding row created before release.
5. The paired CAP and LIQ rows for each ILAAP operator.
6. Confirmation that every user whose `authv` changed signed in again.
