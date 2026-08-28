# Authorization foundation (as built through 2026-08-28)

This document records the first bounded server-side slice of `docs/rbac.md`.
The new policy kernel is additive and shadow-only; the authorization-version
check is enforcing. It does not expose role/grant administration and does not
migrate tenant endpoints off the existing role hierarchy.

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
vocabulary currently overlaps Account Admin. The later grant/transfer service
must distinguish the owner binding; it must not infer ownership from
`administer` or promote every Account Admin.

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

The creation service also verifies that the principal type matches the identity
record, machine and human bundles are not mixed, and a tenant-user or operator
grantor is active. It requires non-empty grant provenance and creates only an
active binding; lifecycle mutation APIs do not exist in this slice.

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

## Institution-target slice and migration posture (as built 2026-08-27)

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
ownership is the separate migration `202608280045`, described below. Organization
membership, token `org`, the session's `app.organization_id`, organization-scoped
foreign keys, and FORCE RLS remain the outer boundary. No tenant grant endpoint
or binding enforcement is introduced.

## Initial Org Owner assignment (migration 202608280045)

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
legacy account-plane `require_role("admin")` compatibility gate; it sits outside
the analyst/approver ladder and cannot reach regulatory submission. This avoids
grandfathering operational superuser authority while binding enforcement remains
shadow-only.

The staff provisioning saga creates a new organization and exactly one active
human account administrator, then creates its owner binding and assignment row
in the same transaction. Existing zero/multiple-candidate tenants still require
a later audited operator designation mutation. That action belongs in the staff
operator plane because a zero-owner tenant has no tenant authority that could
authorize it; this slice provides no grant UI or mutation API.

## Authorization version and deployment transition

Migration `202608250044` adds `users.authorization_version`, initially `1`, and
does **not** backfill any binding. In particular, an existing scalar `admin` is
not silently made an operational principal, Account Admin, or Org Owner in the
new model.

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
safe deployment consequence is a deliberate one-time re-authentication for
sessions outstanding at deploy time. Existing active users then receive tokens
at version 1 and retain current product behavior through the legacy hierarchy.
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
  of token-family issue, refresh rotation, and authorization invalidation,
  checking after every transition that stale access and refresh credentials
  remain unusable and their persisted families are revoked.

## Shadow rollout and next vertical slice

The new evaluator is not an endpoint gate yet. Existing operational routes keep
their current rank checks, grant CRUD is absent, and an owner binding does not
itself switch any tenant endpoint to enforcement. Liquidity Monitoring records
legacy-versus-binding decisions for one exact institution target, which is
evidence for a later enforcement decision rather than enforcement itself.

The next bounded slice may provision governed pilot bindings and review
parity/denial telemetry before switching any endpoint gate. Role/grant
administration remains later work and must add delegation, SoD, reason/audit,
and last-admin/owner safeguards before exposure.
