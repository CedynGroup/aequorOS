# Authorization foundation (as built through 2026-09-16)

This document records the first bounded server-side slice of `docs/rbac.md`.
The policy kernel remains additive. Product enforcement is tracked in the
[rollout contracts](#product-rollout-boundary); the authorization-version check, effective-authority
projection, institution-discovery boundary, and Org Owner grant-administration
boundary are also enforcing. Tenant grant create/list/revoke and the Members
aggregation are live. Subsequent product cutovers are tracked in the
[product rollout boundary](#product-rollout-boundary).

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
- static bundles and their exact granted actions: the executable
  [`RoleBundle` and `ROLE_PERMISSIONS`](../app/core/authorization.py) definitions.

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

## Baseline membership

Every active non-service human has exactly one active, system-managed `member`
binding at organization-wide Account/restricted scope, with no expiry. Its empty
evaluator permission set grants no institution, directory, or product authority;
shell and own-profile access remain authenticated self-service. The dashboard
behavior is owned by [docs/rbac.md §8.2](../../docs/rbac.md#82-frontend-dashboard).

`app/services/membership.py::ensure_baseline_membership` creates the binding and
its grant audit atomically with activation in SSO request approval and operator
tenant provisioning. The partial unique index enforces one active row per user
and organization. Settings → Members displays the row but cannot grant or revoke
it. `authentication.deactivate_user` revokes and audits the baseline, deactivates
the user, advances `authv`, and revokes refresh families with `user_deactivated`
in one transaction. Future activation paths, including invite acceptance and
reactivation, must call the same creation service; reactivation creates a fresh
active row and retains the revoked history.

Migration `202609160053` backfills active non-service users, audits each grant,
and invalidates affected authorization versions and refresh families. It enumerates
organizations, sets `app.organization_id` per organization, and explicitly filters
each tenant mutation. Its `force_rls_suspended` contexts temporarily suspend FORCE
for the migration owner and restore it afterward. Downgrade invalidates sessions
for users with membership rows before removing those rows and the added index.
`tests/db/test_baseline_membership_migration.py` pins the multi-tenant backfill;
`tests/api/test_baseline_membership.py` and
`tests/core/test_authorization_properties.py` pin lifecycle and non-authority.

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
4. applies every applicable runtime condition as a global veto.

The returned `AuthorizationDecision` includes a per-binding trace, matching
binding IDs, typed condition results, and `to_audit_dict()` for a future
immutable `audit_events` envelope. The shared condition authority contributes
request-wide state available at the projection boundary (currently demo mode)
to every evaluated tuple. Maker/checker, step-up, digest, routed-recipient, and
approval-limit checks remain owned by their workflows and cannot be bypassed by
adding another allow binding. The filing workflow is not changed by this slice.

The persistence boundary performs two fail-closed checks before evaluating any
binding: the principal must still be an active tenant member of the declared
type, and an institution-targeted resource must belong to that same tenant.
These return explained denials (`principal_not_active`,
`resource_tenant_mismatch`, or `resource_institution_not_in_tenant`) with no
misleading matching-binding trace rather than allowing a matching row to
outlive its identity or resource.

## Effective-authority projection and institution coverage

`GET /auth/me` includes `effective_authority`; the same projection is available
alone from `GET /auth/effective-authority`. It contains the current `authv`,
organization capabilities, and capabilities grouped by exact institution. Each
capability is produced by evaluating one complete active binding against one
exact module, sensitivity, permission, and resource target. Scalar roles, token
roles, tenant membership, and partial matches across rows do not contribute.

Request-wide vetoes available at projection time are evaluated before a
capability is returned. A capability whose final decision needs unavailable
object or transaction context is structural eligibility only and carries
`requires_contextual_authorization=true`; it is not execution authority. The
concrete operation must evaluate its maker/checker, step-up, digest, recipient,
limit, and other workflow context before any side effect.

`GET /banks` returns only institutions with at least one projected capability.
Bank detail, reporting-period, and fact routes apply the same coverage decision
and return `404` for an uncovered institution. This is an addressability and
discovery boundary only: each product route must still enforce its own data
authorization as described in the [rollout boundary](#product-rollout-boundary).
Projection failures return `503` and emit denial/error telemetry rather than
falling back to legacy authority.

The dashboard shell, command palette, module tabs, route guard, and query policy
consume this server projection. Capability and product caches are partitioned by
tenant, actor, `authv`, and institution. Baseline-only shell navigation and
personal settings follow [docs/rbac.md §8.2](../../docs/rbac.md#82-frontend-dashboard);
organization settings require organization-wide Account administration.
Institution Profile navigation (`/institution` and its tabs) requires a final,
non-contextual organization-scoped ACCOUNT/restricted `view` capability;
institution-scoped capabilities and Account administration alone do not expose it.
Context-dependent capabilities may support structural navigation, but actions and deep links never treat them as final authorization.
This dashboard slice gates action controls only where the projected capability is
already final and non-contextual. Module-specific mutations such as run,
configure, approve, sign-off, and submit remain with their dependency-ordered
endpoint cutovers; each cutover must drive its UI control from the same final
authority that its backend route enforces before side effects.

Verified operator examiner impersonation remains explicit staff-plane read
authority rather than a tenant binding. Its server projection grants only
institution `view` capabilities, applies the same request-wide vetoes, and is
shared by profile bootstrap and bank list/detail/period/fact coverage. It grants
no tenant mutation capability and never advertises Liquidity Monitoring access.

## Liquidity enforcement

The [Liquidity rollout contract](liquidity_enforcement_rollout.md) owns the
surface/permission matrix, immediate-deny behavior, credential exclusions,
dashboard access rules, and deployment inventory. Organization membership,
token `org`, the session's `app.organization_id`, organization-scoped foreign
keys, and FORCE RLS remain the outer boundary.

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
refresh families with `authorization_changed`. The scalar `account_admin` value
does not authorize SSO connection or access-request administration,
integration-key lifecycle operations, or organization-directory reads. Those
routes evaluate explicit organization-wide ACCOUNT/restricted bindings. The
generic `require_role("admin")` dependency remains operational and
excludes `account_admin`. It sits outside the analyst/approver ladder and cannot reach
attestation policy, placement-template mutation, or regulatory submission. This
avoids grandfathering operational superuser authority while binding enforcement
rolls out route by route. Liquidity Monitoring likewise requires its own
explicit scoped binding.

Migration `202609090051` subsequently restores only Account administration for
eligible administrators in unowned multi-candidate organizations; it does not
assign ownership or directory view. See
`account_administration_enforcement_rollout.md` for the authoritative migration
and rollout contract.

Migration `202608280046` records every demoted legacy administrator in the
FORCE-RLS `initial_admin_role_demotions` table. Downgrade restores and invalidates
sessions only for those recorded identities. If a post-migration
`account_admin` exists, downgrade refuses before changing any role or refresh
family because the historical schema cannot represent that account safely.

## Org Owner read access (migration 202609160052)

The Owner bundle carries `administer` only and the owner binding names the
Account module alone, so once the dashboard derived visibility from bindings
an Owner holding nothing else saw no institution and no module (the founder's
own account, 2026-09-16). docs/rbac.md §7 gives Owner `view` on every module
"for administration context but no operational write". That is now a second,
explicit sentence written with ownership: `viewer` / organization-wide / `all`
modules / `all` sensitivities. `organization_ownership.ensure_owner_read_access`
writes it in the provisioning saga and any owner assignment unless an equivalent
organization-wide all/all row of a view-carrying bundle already exists;
migration `202609160052` backfills every effective human owner of an active
user the same way, invalidating only the sessions of owners who received a row.
Nothing is inferred from the owner bundle, the row appears in Members like any
grant, and it can be revoked on its own. Maker/checker authority stays out (C9).

Grant administration also has its own institution catalogue:
`GET /api/v1/organization/institutions` (Org Owner gate) lists every
institution in the organization for scoping a grant. `/banks` filters to the
institutions the caller can view, which is empty for an Owner without the read
sentence, and the Members composer used to read it — so such an Owner could
only write organization-wide grants.

The cutover gate is `scripts/authorization_access_impact.py`: it projects every
active human user through `project_effective_authority` and flags
`no_bindings`, `no_product_view` and `account_plane_only`. Run it against the
target deployment before any change to binding enforcement and keep the dated
output with the deployment record.

The staff provisioning saga creates a new organization and exactly one active
human account administrator, then creates its owner binding, the owner's read
sentence, and assignment row in the same transaction. Existing zero/multiple-candidate tenants still require
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
Owner, Member, and Integration Writer are not tenant-grantable; Account Admin is valid
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
active. Baseline membership ends only through [deactivation](#baseline-membership).

The Members response is tenant-filtered and aggregates identity, lifecycle,
SSO-access-request state, last activity, authentication method, active grant
count, and complete binding summaries. Settings renders this as a grant count
plus at most two compact fragments, with lifecycle kept separate from access.
Its Define → Review → Done sentence composer fixes the principal and makes all
four binding dimensions single-valued. The same sentence is reused in review,
member detail, revoke confirmation, completion, and audit evidence. SSO request
approval uses this same service transaction: verified identity alone has no
binding-derived authority; approval atomically activates the identity, ensures
[baseline membership](#baseline-membership), and adds the selected complete
scoped grant.

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
`authorization_changed` by default in one transaction; deactivation supplies
`user_deactivated` instead. The binding creation primitive
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

## Account administration enforcement (built 2026-09-08)

SSO connection read/write, SSO access-request list/reject, and integration-key
issue/list/revoke require one complete active human binding with organization-wide
institution coverage, ACCOUNT/restricted scope, and `administer` permission.
Either the `account_admin` or `org_owner` bundle can supply that authority.
`GET /api/v1/organization/users` separately requires ACCOUNT/restricted `view`;
administration does not imply directory access. Scalar roles and operational
Analyst/Approver grants satisfy none of these checks.

SSO approval and binding administration continue to require the `org_owner`
bundle itself. Integration-key issuance additionally requires one exact bank
target and atomically creates a machine-only Integration Writer binding for
DATA/restricted `ingest`. All four push-batch routes require that complete
binding; human Analyst grants cannot satisfy it, and a machine key targeting a
sibling bank receives 404. Legacy null-bank keys receive no inferred target or
compatibility grant. Their deployment inventory and mandatory rotation are
owned by `integration_key_machine_principal_rollout.md`.

## Executable verification

The fixed evaluator, service, refresh-token, and Postgres migration suites pin
the binding semantics, database constraints, FORCE RLS, cross-tenant refusal,
and atomic version-bump/session-revocation contract. The fixed tests also prove
that one institution binding does not reach a sibling, an explicitly
organization-wide binding does, cross-organization and invalid targets fail
closed with actionable reasons, and suspended or absent bindings default to
denial. The Liquidity Monitoring API tests pin both allowed and denied binding
decisions, all scope and lifecycle mismatches, anti-composition, stale sessions,
post-revocation denial, and fail-closed evaluator errors. The architecture test
pins the route's named binding dependency and forbids legacy role dependencies.
Generative suites add coverage beyond the fixed examples:

- `tests/core/test_authorization_properties.py` compares the evaluator with an
  independent per-binding oracle across binding order, partial cross-row
  matches, runtime conditions, and exact lifecycle boundaries;
- `tests/api/test_authorization_state_machine.py` exercises arbitrary sequences
  of token-family issue, refresh rotation, authorization invalidation, scoped
  grant creation, and exact single-row revocation. It checks the effective union
  against an independent finite oracle, including sensitivity, after every
  transition and proves no unrequested cross-product appears. Its ownership
  machine checks initial assignment, idempotent owner read access, public grants,
  JIT approval, revocation, and refusal of public owner creation or revocation,
  preserving assignment and audit evidence after each transition; and
- `tests/db/test_authorization_tenant_isolation_properties.py` generates tenant
  bindings against a migrated Postgres schema with FORCE RLS, checks direct
  current-fact isolation, and discovers bank GET and organization listing routes
  from FastAPI. A persisted tenant-B reporting period and fact have an HTTP 200
  positive control before tenant-A requests. Other UUID resource kinds still use
  unknown IDs, so those requests do not prove isolation of existing child rows.

Two paired tests extend that route enumeration to the classic IDOR shape: a
route that carries a second object identifier beside `{bank_id}` — a
`package_id`, `signoff_id`, `analysis_id`, `declaration_id`, `connection_id` and
the rest — in its path, or a UUID `*_id` in its request body or query. Both draw
the same census and object catalogue from `tests/fixtures/object_references.py`
and `tests/fixtures/object_reference_routes.py`, seed one real object of every
such kind for three tenants (organization A bank A, organization A sibling bank
A2, organization B bank B), and reference a foreign tenant's object under bank
A. Across the cross-organization and same-organization sibling-bank layouts each
checks responses for foreign identifiers other than those sent in the request
(a 200 that hides the row is a valid read refusal), rejects successful mutations,
and checks for persisted side effects.
Actor-label body fields (`assigned_to_user_id`, `approved_by_user_id`) are
excluded because they record who acted, not an object whose data is read.
`KNOWN_UNCOVERED` in `tests/fixtures/object_reference_routes.py` owns the
excluded-route list, including routes that need multi-step fixtures. A third
`single_foreign_child` layout enumerates each eligible child
on multi-reference routes, holding every other reference at home and substituting
only that child from A2. This covers same-organization cross-parent nesting,
including package/resubmission, party/shareholding and scenario/assumption guards.

`tests/api/test_authorization_object_reference_coverage.py` is the deterministic
layer: it `pytest.mark.parametrize`s one case per route × HTTP method × layout,
fixes a fully entitled bank-A caller, and checks the refusal shape and that no
table content changes, including on reads, using portable per-table content
hashes. It runs on SQLite because its refusals come from the explicit
organization/bank `WHERE` clauses in the guards and services, which hold without row-level security; its data-layer
positive control confirms every seeded object exists for the tenant that owns
it, so a refusal is authorization, not a missing fixture. It pins the two
confirmed same-organization cross-bank defects in `KNOWN_DEFECTS`, skips them in
the strict parametrization, and asserts they are still reproduced so a product
fix forces their promotion.

`tests/db/test_authorization_object_reference_properties.py` is the generative
layer, Postgres-only against a migrated schema with FORCE RLS so the RLS
backstop is exercised too. Hypothesis varies legacy token roles alongside
role/permission bundles, module scope, sensitivity scope, institution scope and
binding lifecycle state, together with object placement (cross-organization,
sibling bank or a single foreign child). Each example sweeps the applicable
census and checks that the content digest of every table in both organizations
is unchanged. The bounded sample (`max_examples=15`) checks the isolation
invariant across generated authority combinations. Its committed negative
control weakens the package bank guard under a rolled-back `monkeypatch` and
confirms the sweep then reports the sibling-bank leak.

Negative controls reuse the ownership and current-fact invariants: a test-scoped
patch admitting public `org_owner` grants must violate the unassigned-organization
invariant; transactional `NO FORCE ROW LEVEL SECURITY` must break table-owner
isolation. Restoration and rollback must restore the respective protections.
Execution results are recorded in
[the mutation evidence](../tests/authorization_mutation_evidence.md).

Known coverage limits remain: ownership transfer and owner deactivation have no
product API to exercise. The generated route property checks identifier leakage in
successful responses; the separate sibling-bank read regression now enforces the
[bank-route existence rule](../../docs/rbac.md#4-tenancy--the-two-planes), implemented
by `app/api/deps.py::resolve_tenant_bank`, without an expected failure.
`tests/api/test_cross_tenant_bank_routes.py` also enumerates
OpenAPI bank-path and bank-query operations, generates request inputs for reads and
mutations, and checks the shared dependency and standard error envelope for an
authenticated sibling-organization member with no bindings on Postgres.

## Product rollout boundary

Liquidity enforcement scope and held configuration routes are owned by the
[Liquidity rollout contract](liquidity_enforcement_rollout.md). Capital's route matrix,
access requirements, and deployment inventory are owned by the
[Capital enforcement rollout](capital_enforcement_rollout.md). IRRBB dashboard,
engine, shared workbench, and run-registry requirements are owned by the
[IRRBB enforcement rollout](irrbb_enforcement_rollout.md). FX direct and shared
surfaces, result projections, and required grants are owned by the
[FX rollout contract](fx_enforcement_rollout.md). FTP dashboards, runs, shared
workbench entries, and deployment grants are owned by the
[FTP rollout contract](ftp_enforcement_rollout.md). Existing
operational routes outside these cutovers keep their current checks, while
grant administration itself requires the owner binding. Explanation endpoints,
further product-route cutovers,
invite/lifecycle actions, scheduled grants, and owner designation/transfer
remain separate work.
