# Authorization foundation (as built through 2026-08-29)

This document records the first bounded server-side slice of `docs/rbac.md`.
The policy kernel remains additive and product-route enforcement is shadow-only;
the authorization-version check and Org Owner grant-administration boundary are
enforcing. Tenant grant create/list/revoke and the Members aggregation are live,
but ordinary product routes remain on the existing hierarchy until their
separate rollout.

## Authority model

`Organization` (`OR-*`) remains the account and security tenant. `Bank`
(`BK-*`) is an institution/legal entity beneath it. A principal is a tenant
`User` classified as either `human` or `machine`; a service user is not treated
as a human preset or seat.

The policy vocabulary lives only in `backend/app/core/authorization.py`:

- permissions: `view`, `create`, `edit`, `run`, `review`, `approve`,
  `configure`, `export`, `validate`, `sign_off`, `submit`, `administer`, and
  `ingest`;
- concrete resource modules: LIQ, CAP, IRRBB, FX, FTP, FCST, BEH, DATA, REG,
  Risk, Markets, Account, and Audit;
- sensitivities: `published`, `aggregated`, `confidential`, and `restricted`;
- static bundles: Viewer, Auditor, Analyst, Approver, Account Admin, Org Owner,
  and the machine-only Integration Writer.

The v1 bundle contents are deliberately narrow:

| Bundle             | Granted actions                                       |
| ------------------ | ----------------------------------------------------- |
| Viewer             | `view`                                                |
| Auditor            | `view`                                                |
| Analyst            | `view`, `create`, `edit`, `run`, `validate`, `export` |
| Approver           | `view`, `review`, `approve`                           |
| Account Admin      | `administer`                                          |
| Org Owner          | `administer`                                          |
| Integration Writer | `ingest`                                              |

`configure`, `sign_off`, and `submit` are reserved action names but are not in
any v1 bundle. Workflow-specific authority for them must be designed explicitly
rather than inferred from a nearby role.

Org Owner is a distinct binding authority even though the bounded v1 action
vocabulary currently overlaps Account Admin. The grant-administration boundary
requires the owner binding itself; it does not infer ownership from
`administer` or promote every Account Admin. Owner designation and transfer
remain later staff-plane work.

No route name, HTTP verb, UI navigation item, or token permission claim creates
authority. Static bundles are code; v1 has no mutable permission catalog and no
configurable deny grant.

## Indivisible scoped bindings

`authorization_bindings` is the only grant authority understood by the new
evaluator. One row keeps these facts together:

```text
tenant + principal/type + role bundle
       + institution scope + module scope + sensitivity scope
       + grantor/reason + validity/lifecycle
```

Rows combine with OR, but every dimension within one row combines with AND.
Thus Analyst on LIQ plus Approver on REG grants exactly those two combinations;
it cannot produce Analyst on REG or Approver on LIQ.

Broad scope is named, never inferred:

- `institution_scope=organization` explicitly covers every institution in the
  organization and requires `institution_id IS NULL`;
- `institution_scope=institution` requires one exact `institution_id`;
- module and sensitivity broad grants store the explicit value `all`.

The database uses composite foreign keys from `(principal_user_id,
organization_id)` to `users` and from `(institution_id, organization_id)` to
`banks`. The service repeats both ownership checks before insert. The table is
tenant-owned and has ENABLE + FORCE RLS with the standard
`app.organization_id` policy. Status/validity constraints make active,
suspended, and revoked stored states, plus not-yet-valid and expired effective
states, unambiguous. `valid_from` is inclusive and `valid_until` is exclusive.

The low-level creation service also verifies that the principal type matches the identity
record, machine and human bundles are not mixed, and a tenant-user or operator
grantor is active. It requires non-empty grant provenance and creates only an
active binding. The tenant surface fixes validity to immediate with no expiry
and exposes only explicit single-binding revocation; scheduled and expiry
lifecycle remain later work.

## Decision semantics and conditions

`ResourceLocator` carries exactly the rollout-v1 resource attributes:
organization, an explicit organization-or-institution target, concrete module,
and concrete sensitivity. An institution target requires a non-empty
`institution_id`; an organization target forbids one. A missing institution is
therefore never interpreted as broad access. The same `InstitutionScope`
vocabulary is used by bindings and resources so their matching semantics cannot
drift.
The shared evaluator:

1. starts denied;
2. evaluates each binding independently for principal, tenant, lifecycle,
   permission bundle, institution, module, and sensitivity;
3. unions only bindings whose complete tuple matches; and
4. applies every supplied runtime condition as a global veto.

The returned `AuthorizationDecision` includes a per-binding trace, matching
binding IDs, typed condition results, and `to_audit_dict()` for a future
immutable `audit_events` envelope. Condition hooks are reserved for demo-mode,
maker/checker, step-up, and approval-limit policies. Those conditions remain
owned by their workflows and cannot be bypassed by adding another allow
binding. The filing workflow is not changed by this slice.

The persistence boundary performs two fail-closed checks before evaluating any
binding: the principal must still be an active tenant member of the declared
type, and an institution-targeted resource must belong to that same tenant.
These return explained denials (`principal_not_active`,
`resource_tenant_mismatch`, or `resource_institution_not_in_tenant`) with no
misleading matching-binding trace rather than allowing a matching row to
outlive its identity or resource.

## Institution-target slice and migration posture (as built 2026-08-28)

`GET /banks/{bank_id}/liquidity-monitoring` is the first real resource path to
construct an exact institution-scoped locator. For normal tenant app sessions
it evaluates `view` on LIQ/confidential and emits `authz.shadow_decision` with
the legacy and binding outcomes, reason, target, matching binding IDs, and
per-binding reasons. The route still follows its existing authenticated read
contract regardless of the shadow outcome. Operator impersonation and
integration-key credentials retain their separate lifecycles and are outside
this human-binding pilot. A shadow-evaluation failure emits
`shadow_evaluation_failed` at error severity and does not become a route gate.

The institution-target shadow slice itself requires no new migration. Initial
ownership is the separate migration `202608280046`, described below. Organization
membership, token `org`, the session's `app.organization_id`, organization-scoped
foreign keys, and FORCE RLS remain the outer boundary. Grant administration is
described below; Liquidity product enforcement is not introduced.

## Initial Org Owner assignment (migration 202608280046)

Ownership is an organization-wide `org_owner` binding on the Account module,
with explicit system provenance. The migration considers only users whose
legacy role is `admin`, who are active, and whose authentication provider is not
`service`:

- exactly one eligible administrator is assigned automatically;
- zero eligible administrators receive no binding; and
- multiple eligible administrators receive no binding.

Every organization gets one FORCE-RLS `organization_owner_assignments` control
row. It persists the outcome, basis, candidate count, and an ordered candidate
snapshot (`user_id`, email, display name), plus the owner user/binding IDs only
when assigned. This is the ordinary staff-side query for unresolved tenants:

```sql
SELECT organization_id, basis, eligible_candidates
FROM organization_owner_assignments
WHERE status = 'designation_required'
ORDER BY organization_id;
```

The operator database role can run that fleet query because it has the same
cross-tenant BYPASSRLS posture as the rest of the staff console. Tenant sessions
see only their own row. A partial unique index permits at most one active Org
Owner binding per organization.

The same migration converts every persisted scalar `admin` to
`account_admin`, increments `authorization_version`, and revokes outstanding
refresh families with `authorization_changed`. `account_admin` passes only the
explicit account-administration gate used by SSO membership and integration-key
management; the generic `require_role("admin")` dependency remains operational
and excludes it. It sits outside the analyst/approver ladder and cannot reach
attestation policy, placement-template mutation, or regulatory submission. This
avoids grandfathering operational superuser authority while binding enforcement
remains shadow-only.

Migration `202608280046` records every demoted legacy administrator in the
FORCE-RLS `initial_admin_role_demotions` table. Downgrade restores and invalidates
sessions only for those recorded identities. If a post-migration
`account_admin` exists, downgrade refuses before changing any role or refresh
family because the historical schema cannot represent that account safely.

The staff provisioning saga creates a new organization and exactly one active
human account administrator, then creates its owner binding and assignment row
in the same transaction. Existing zero/multiple-candidate tenants still require
a later audited operator designation mutation. That action belongs in the staff
operator plane because a zero-owner tenant has no tenant authority that could
authorize it. The Members surface depends on this owner and does not add a
tenant-side owner designation or transfer action.

## Scoped grant administration and Members (built 2026-08-29)

`GET/POST /api/v1/authorization/bindings`,
`POST /api/v1/authorization/bindings/preview`, the single-binding revoke route,
and `GET /api/v1/organization/members` require a persisted Org Owner binding
through the evaluator; scalar account-admin or token claims are insufficient.
Create has one scalar role bundle, one institution coverage, one module, one
sensitivity, and one required reason. Arrays are rejected by the closed request
schema, so two authority combinations require two requests and two binding rows.
Preview returns the canonical authority sentence; create requires that exact
sentence and refuses if names or scope presentation changed before commit.
Members may grant Viewer, Auditor, Analyst, Approver, or Account Admin. Org
Owner and Integration Writer are not tenant-grantable; Account Admin is valid
only as organization-wide Account Administration at all sensitivity levels.

The server runs assignment-time separation-of-duties policy and returns the
authoritative `allow`, `warn`, or `block` decision. C9 account-administration
versus operational maker/checker authority is blocked. An overlapping Analyst
and Approver pair is warned because per-object maker-checker remains a runtime
condition that no additional binding may bypass.

Revoke changes only the targeted row and records revoker, time, and reason.
Create and revoke both write an `audit_events` record containing actor, grantee,
role, complete scope (including sensitivity), time, reason, and the canonical
authority sentence. Both advance the grantee's authorization version and revoke
their refresh families in the same transaction; unrelated binding rows remain
active.

The Members response is tenant-filtered and aggregates identity, lifecycle,
SSO-access-request state, last activity, authentication method, active grant
count, and complete binding summaries. Settings renders this as a grant count
plus at most two compact fragments, with lifecycle kept separate from access.
Its Define → Review → Done sentence composer fixes the principal and makes all
four binding dimensions single-valued. The same sentence is reused in review,
member detail, revoke confirmation, completion, and audit evidence. SSO request
approval uses this same service transaction: verified identity alone has no
binding-derived authority; approval atomically activates the identity and adds
exactly one complete binding.

The SSO routes retain their split administration boundary: account
administrators may list or reject never-activated request stubs, but only an Org
Owner may call `POST /api/v1/auth/sso/access-requests/{user_id}/approve` because
approval creates authority.

## Authorization version and deployment transition

Migration `202608250044` adds `users.authorization_version`, initially `1`, and
does **not** itself backfill any binding. Follow-on migration `202608280046`
performs the explicit initial-owner assignment and account-role split described
above; it never infers owner authority merely from `account_admin`.

Every newly issued access and refresh token carries the authoritative, positive
integer `authv`. Every normal app-JWT request compares it with the active user
row; refresh also requires the current value in addition to the server-side
token record. A stale version returns 401. `invalidate_user_authorization()`
locks the user, advances the version, and revokes every refresh family with
`authorization_changed` in one transaction. The binding creation primitive
uses this operation before commit. Future role, scope, status, and security
mutations must do the same.

Tokens issued before this migration have no `authv`. They fail closed for both
access and refresh, even if their signature and old role claims are valid. The
safe deployment consequence of the foundation migration is a deliberate
one-time re-authentication for sessions outstanding at deploy time. The initial
ownership migration advances every legacy admin's version again while moving
them outside the operational hierarchy, so those sessions also fail closed.
Integration keys and operator impersonation tokens retain their separate
credential lifecycles and do not carry `authv`.

## Executable verification

The fixed evaluator, service, refresh-token, and Postgres migration suites pin
the binding semantics, database constraints, FORCE RLS, cross-tenant refusal,
and atomic version-bump/session-revocation contract. The fixed tests also prove
that one institution binding does not reach a sibling, an explicitly
organization-wide binding does, cross-organization and invalid targets fail
closed with actionable reasons, and suspended or absent bindings default to
denial. The Liquidity Monitoring API tests pin both allowed and denied shadow
telemetry and prove that a shadow-evaluation failure cannot change the legacy
response. Two generative suites add coverage beyond the fixed examples:

- `tests/core/test_authorization_properties.py` compares the evaluator with an
  independent per-binding oracle across binding order, partial cross-row
  matches, runtime conditions, and exact lifecycle boundaries; and
- `tests/api/test_authorization_state_machine.py` exercises arbitrary sequences
  of token-family issue, refresh rotation, authorization invalidation, scoped
  grant creation, and exact single-row revocation. It checks the effective union
  against an independent finite oracle, including sensitivity, after every
  transition and proves no unrequested cross-product appears.

## Shadow rollout and next vertical slice

The new evaluator is not a product endpoint gate yet. Existing operational
routes keep their current rank checks, while grant administration itself
requires the owner binding. Liquidity Monitoring records
legacy-versus-binding decisions for one exact institution target, which is
evidence for a later enforcement decision rather than enforcement itself.

The next bounded slice may review parity/denial telemetry before switching its
endpoint gate. Explanation endpoints, Liquidity enforcement, invite/lifecycle
actions, scheduled grants, and owner designation/transfer remain separate work.
