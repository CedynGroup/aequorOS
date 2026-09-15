# Account administration enforcement rollout

Issue #174 replaces scalar account-administrator role checks with scoped
authorization bindings. Run this inventory against each target deployment
immediately before release. Store the dated query output with the deployment
record. Do not copy production identities into this repository.

## Affected surfaces

The following routes require one active organization-scoped binding for module
`account`, sensitivity `restricted`, and permission `administer`:

- `GET|PUT /api/v1/auth/sso/connection`
- `GET /api/v1/auth/sso/access-requests`
- `POST /api/v1/auth/sso/access-requests/{user_id}/reject`
- `GET /api/v1/integration-keys`
- `POST /api/v1/integration-keys/{key_id}/revoke`

`GET /api/v1/organization/users` instead requires permission `view` over that
same resource. Account administration does not imply directory view. SSO
access-request approval and grant administration still require an `org_owner`
binding. Integration-key issuance remains on its existing compatibility gate
until the bank-scoped machine-principal cutover in issue #175. The dashboard
nevertheless hides the Generate control unless the user has this explicit
organization-scoped Account administration authority. Consequently, a legacy
`admin` or `account_admin` scalar-role holder without an explicit binding loses
the Generate control before the issuance endpoint changes.

## Legacy-administrator lockout closure

Migration `202609090051` restores the account-plane authority that eligible
legacy administrators exercised before scalar-role enforcement ended. It uses
the candidate snapshots recorded by migration `202608280046` and grants each
still-active human candidate in an unresolved multi-candidate organization the
exact organization-scoped `account_admin` / `account` / `restricted` binding.
This is a compatibility restoration, not an escalation: it does not grant
billing, organization transfer, organization deletion, or ownership.

The migration does not guess an owner. Organizations that already have an
owner are untouched, no `org_owner` binding is created, and every unresolved
`organization_owner_assignments.status = 'designation_required'` record remains
the operator's queue for explicit designation. Until staff complete that
designation, a multi-candidate organization can administer SSO and existing
integration keys but cannot create new grants.

Only a newly inserted binding invalidates sessions. Its user row is locked,
`authorization_version` advances, and every live refresh-token family is
revoked with `authorization_changed` in the same transaction, so that user must
sign in again. A candidate who already holds suitable Account administration
authority is left untouched. The `account_admin` bundle grants `administer`,
not `view`; organization-directory access still requires a separate,
institution-approved view-capable Account binding.

## Inventory

Run this read-only query as a role that can see all tenant users and bindings.
It enumerates every active human and machine principal and states the result of
this cutover without inferring a grant from the scalar role.

```sql
WITH effective_bindings AS (
    SELECT
        b.organization_id,
        b.principal_user_id,
        b.principal_type,
        b.role_bundle,
        b.module_scope,
        b.sensitivity_scope
    FROM authorization_bindings AS b
    WHERE b.status = 'active'
      AND b.revoked_at IS NULL
      AND b.valid_from <= CURRENT_TIMESTAMP
      AND (b.valid_until IS NULL OR b.valid_until > CURRENT_TIMESTAMP)
      AND b.institution_scope = 'organization'
      AND b.institution_id IS NULL
),
authority AS (
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
        COALESCE(
            bool_or(
                b.role_bundle IN ('account_admin', 'org_owner')
                AND b.module_scope IN ('account', 'all')
                AND b.sensitivity_scope IN ('restricted', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_administer_accounts,
        COALESCE(
            bool_or(
                b.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
                AND b.module_scope IN ('account', 'all')
                AND b.sensitivity_scope IN ('restricted', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_view_organization_directory
    FROM users AS u
    LEFT JOIN effective_bindings AS b
      ON b.organization_id = u.organization_id
     AND b.principal_user_id = u.id
    WHERE u.is_active IS TRUE
    GROUP BY
        u.organization_id,
        u.id,
        u.email,
        u.auth_provider,
        u.role
)
SELECT
    organization_id,
    principal_user_id,
    principal_type,
    email,
    auth_provider,
    scalar_role,
    may_administer_accounts,
    may_view_organization_directory,
    CASE
        WHEN principal_type = 'machine' THEN
            'denied: human Account binding required'
        WHEN scalar_role IN ('admin', 'account_admin')
             AND may_administer_accounts IS FALSE THEN
            'denied: legacy scalar administrator has no exact binding'
        WHEN may_administer_accounts IS TRUE THEN
            'allowed: Account administration'
        ELSE
            'denied: no Account administration permission'
    END AS administration_result,
    CASE
        WHEN principal_type = 'machine' THEN
            'denied: human Account binding required'
        WHEN may_view_organization_directory IS TRUE THEN
            'allowed: organization directory'
        ELSE
            'denied: no Account view permission'
    END AS directory_result
FROM authority
ORDER BY organization_id, principal_type, email;
```

Review the output with each institution. Explicitly identify reporting
preparers, approvers, auditors, or other staff who use actor-name attribution
and therefore need the organization directory. A product grant does not imply
that need, and the platform must not create one automatically.

## Exact binding rows

Migration `202609090051` creates only the compatibility bindings described
above. Create any other rows only after the institution confirms them. Each
interactive grant must go through the authorization service so the user's
`authv` advances and refresh-token families are revoked in the same
transaction.

| Need                                                | `principal_type` | `role_bundle`                                                                      | `institution_scope` | `institution_id` | `module_scope` | `sensitivity_scope` |
| --------------------------------------------------- | ---------------- | ---------------------------------------------------------------------------------- | ------------------- | ---------------- | -------------- | ------------------- |
| Administer SSO and existing integration keys        | `human`          | `account_admin`                                                                    | `organization`      | `NULL`           | `account`      | `restricted`        |
| Preserve initial ownership and grant administration | `human`          | `org_owner`                                                                        | `organization`      | `NULL`           | `account`      | `restricted`        |
| Read the organization directory                     | `human`          | one institution-approved bundle from `viewer`, `auditor`, `analyst`, or `approver` | `organization`      | `NULL`           | `account`      | `restricted`        |

To preserve the Generate control during this transition, operators must create
exactly one of the first two rows for the affected human: either the
organization-scoped `account_admin` / `account` / `restricted` row or the
organization-scoped `org_owner` / `account` / `restricted` row. A scalar role
alone is insufficient.

The least-privilege account-administrator grant request is:

```json
{
  "principal_user_id": "<confirmed human user UUID>",
  "role_bundle": "account_admin",
  "institution_scope": "organization",
  "institution_id": null,
  "module_scope": "account",
  "sensitivity_scope": "restricted",
  "reason": "<institution-approved reason>",
  "expected_authority_sentence": "<server preview response>"
}
```

For directory access, change only `role_bundle` to the institution-approved
view-capable bundle. Do not create a machine Account grant, infer authority from
`users.role`, or widen module/sensitivity scope to compensate for a missing
exact row.

## Release record

Before deployment, attach all of the following to the release record:

1. The dated inventory output for every organization.
2. The exact list of human and machine principals that will be denied.
3. The exact migration-created compatibility bindings and any separately
   approved binding rows.
4. The remaining `designation_required` ownership records, with an assigned
   operator for each explicit designation.
5. Confirmation that every user whose `authv` changed signed in again.
