# RBAC proposal review

**Reviewed:** 2026-08-23 | **Status:** accepted design corrections; implementation pending

This companion records the completed security and architecture review of
[`rbac.md`](rbac.md). Code wins over proposal text where they differ. The review
and accepted defaults below constrain future implementation; this documentation
does not implement RBAC, change runtime behavior, or by itself close any
vulnerability.

## 1. Stale as-built claims

The proposal must be rebased on these facts before its roadmap is treated as an
implementation plan:

| Proposal claim | Current evidence and correction |
|---|---|
| Four roles; only the viewer/analyst boundary is enforced; approver/admin have no distinct gate (`rbac.md:51-60`). | There are five scalar roles, including `examiner`, in a linear hierarchy (`backend/app/models/user.py:20-25,33-70`; `backend/app/core/security.py:34-40,81-87`). Mutations require analyst-or-higher and control actions can require approver-or-higher (`backend/app/api/deps.py:245-288,364-390`). |
| There is no audit log, impersonation, or vendor console (`rbac.md:61-67`), and the target should add `admin.aequoros.com`, `/platform/*`, `platform_staff`, and a new impersonation model (`rbac.md:585-599,775-784,834-842`). | Immutable tenant `audit_events` and append-only `operator_audit_log` exist (`backend/app/models/audit_event.py:13-41`; `backend/app/models/operator.py:53-81`; `backend/alembic/versions/202607250027_attestation_foundation.py:67-107`). The separate `console/` and `/operator/v1/*` entrypoint already use `operator_users` and fixed read-only examiner inspection (`backend/app/operator/main.py:1-10,108-130`; `backend/app/operator/features/inspector.py:132-208`; `backend/tests/operator/test_route_isolation.py:22-33`). Extend these authorities; do not fork them. |
| Tenant, organization, and one bank are equivalent (`rbac.md:34-35`). | `Organization` is the security tenant; `Bank.organization_id` is indexed, not unique, so an organization can contain multiple legal entities (`backend/app/models/organization.py:10-19`; `backend/app/models/regulatory.py:31-64`). |
| Regulatory maker-checker should be generalized as though only the proposed primitive exists (`rbac.md:395-409,763-765`). | Package generation and approval already require different users, while transmission currently permits a maker to send an independently approved package (`backend/app/services/regulatory_reporting/workflow.py:266-293,896-929`). Existing attestation and immutable-digest controls must not be weakened. |

Tenant user administration remains unimplemented. The existing
`GET /organization/users` is a read-only directory available to every
authenticated tenant user (`backend/app/features/list_organization_users.py:22-52`).
Permission-aware tenant UX is also unimplemented: the sidebar is static apart
from institution-type filtering, middleware checks authentication only, and
`/auth/me` returns one role rather than effective bindings
(`backend/dashboard/components/shell/Sidebar.tsx:44-102`;
`backend/dashboard/middleware.ts:16-25`; `backend/app/schemas/auth.py:44-56`).

## 2. Required design corrections

These are critical/high defects, not optional refinements.

| Severity | Defect and impact | Required correction |
|---|---|---|
| Critical | Independent `user_roles` and `user_scopes` (`rbac.md:745-750`) turn LIQ Analyst + REG Approver into both roles on both modules, widening authority through a Cartesian product. | Persist indivisible scoped role bindings as specified in §3. Deny when no exact binding covers the requested permission, resource scope, conditions, and lifecycle. |
| Critical | Mapping legacy `admin` to account-only Org Admin while retaining rank compatibility (`rbac.md:260-276`) leaves Org Admin above analyst and approver (`backend/app/core/security.py:34-40,81-87`), including on submission gates (`backend/app/api/deps.py:245-288,364-385`; `backend/app/features/manage_regulatory_reporting.py:442-457`). | Introduce `org_admin` outside the operational ladder and atomically cut routes to explicit permissions before assigning it. Coarse operational gates may remain only as defense in depth. |
| Critical | Grant mutation precedes `session_epoch` in the proposed phases (`rbac.md:854-900`). Stateless access tokens carry role claims but no authorization version (`backend/app/core/security.py:98-147`), and tenant validation checks active membership, not current grants (`backend/app/api/deps.py:313-358`). Demotion can therefore remain effective until expiry. | Ship authorization-version checks and refresh-family revocation with the first grant mutation. Atomically invalidate sessions on role, scope, status, or security changes; explicitly invalidate any short-lived permission cache. |
| Critical | A verified but unprovisioned workforce-domain identity currently receives `developer` (`backend/app/operator/deps.py:140-172`), exposing cross-tenant operator surfaces contrary to least privilege (`rbac.md:344-349,601-602`). | This fail-open defect is being addressed in a separate staff-plane change. `operator_users` must remain authoritative and unknown identities must be denied; add explicit operator permissions rather than a second staff store. This document does not close the defect. |
| High | The universal rule says submitter differs from every maker (`rbac.md:103-105,406-409`), but the reporting persona prepares and submits (`rbac.md:176,519`) and current code permits post-approval transmission (`backend/app/services/regulatory_reporting/workflow.py:896-929`). Contradiction makes controls and staffing requirements indeterminate. | Apply the accepted default: a maker may certify the filing as its preparer and may mechanically transmit it only after a distinct checker has certified it as approver. The maker may never approve it or certify an approver/checker slot. |
| High | Proposed approvals identify only a mutable object and event (`rbac.md:763-765`), so approval can survive edits or race a transition. | Bind every decision to an immutable subject revision, value-based digest, policy version, evaluated scope, approval tier/metric, and transition sequence. Editing creates a revision or invalidates pending decisions. Preserve stronger attestation controls. |
| High | Organization RLS is presented as sufficient for narrower query scopes (`rbac.md:380-393`), but it cannot enforce institution, desk, portfolio, currency, section, or sensitivity boundaries. A static dependency cannot authorize an unloaded resource. | Keep RLS as the outer tenant wall, then resolve a canonical resource and evaluate policy after load. Inventory every scoped read and write and retain explicit organization/institution predicates and tenant-consistent foreign keys (`backend/app/api/deps.py:394-419`). |
| High | One `external_id` and one global provider/subject pair (`rbac.md:739-743`; `backend/app/models/user.py:44-52,71-77`) cannot safely represent multiple OIDC/SCIM sources. `is_active` also conflates JIT requests, deactivated humans, and service principals (`backend/app/models/user.py:66,84-90`). | Add source/connection-scoped linked identities, separate membership and request lifecycle, normalize email before invitations/discovery, and model machine principals with fixed narrow authority. |
| High | `roles:manage` has no separate delegation model (`rbac.md:411-423,631-634`), allowing either unusable administration or self-escalation. | Separate permissions a principal may use from grants they may assign. Enforce delegation ceilings, no operational self-grant, owner transfer acceptance, last-owner/admin protection, reasons, step-up, and dual control for top grants. |
| High | Current reads are broad, while the proposal simultaneously gives Org Admin/Auditor broad module access and promises settings-only/sensitivity scoping (`rbac.md:355-378,526`). User-directory and artifact reads illustrate the exposure (`backend/app/features/list_organization_users.py:39-52`; `backend/app/features/manage_regulatory_reporting.py:235-264`). | Gate reads and exports separately. Org Admin gets settings/audit only; Auditor gets controls, lineage, evidence, and published outputs. Raw customer data and bulk export require explicit sensitivity grants. |
| High | New `audit_log` and `platform_staff` tables (`rbac.md:767-776`) would split evidence and staff authority from existing stores. | Evolve `audit_events`, `operator_audit_log`, and `operator_users` additively. Add a tenant RLS-scoped immutable audit read model; never invent missing metadata for historical events. |

## 3. Scope and policy model

### Binding is the authority

A scoped role binding is indivisible:

```text
principal + role/permission + exact scope + conditions + lifecycle
```

It also records grantor, provenance, timestamps, revocation, and policy version.
Bindings combine with **OR** semantics: any one complete binding may authorize the
request. Dimensions inside one binding combine with **AND** semantics: every
specified dimension must match. Roles and scopes must never be unioned
independently (`rbac.md:278-285,380-393,745-750`).

V1 uses additive allow grants and has no tenant-configurable deny grants. Global
conditions remain non-bypassable, including tenant boundary, active lifecycle,
live/demo mutation rules, authorization version, step-up, approval ceiling, and
maker-checker/SoD. No allow binding can override them.

### Tenant and resource hierarchy

Organization is the security/account tenant. Institution/bank is a legal entity
beneath it (`backend/app/models/organization.py:10-19`;
`backend/app/models/regulatory.py:31-64`):

```text
organization -> institution -> desk -> portfolio
```

That hierarchy is intersected within a binding by module, currency, sensitivity,
environment, and pack-section dimensions. Use explicit broad values such as
`all_institutions` or `all_portfolios`; never overload `NULL` to mean "all", "not
applicable", or "not supplied". Database constraints and composite tenant foreign
keys must prevent a principal or scope target from one organization being bound
to another.

Roll out only organization + institution + module + sensitivity first. Add desk,
portfolio, currency, and pack section only after affected resources carry
canonical attributes and structural/integration tests enforce each dimension.

### Resource resolution and decisions

Each protected resource type needs a canonical locator that resolves its
organization, institution, module, sensitivity, and any narrower attributes.
Load with explicit tenant predicates under organization RLS, then evaluate policy
against the loaded canonical attributes. Organization RLS remains the outer
tenant wall, not enforcement for within-tenant scope (`backend/app/api/deps.py:291-358,394-419`).

Decision behavior is part of the contract:

- Return an indistinguishable `404` for a sensitive object ID that is absent or
  outside the caller's resource scope.
- Return `403` when an authenticated caller may know the resource exists but lacks
  the requested action.
- Filter collection results to authorized resources without leaking hidden counts
  or facets.
- Deny an unauthorized mutation explicitly; never silently drop targets and
  return success. Use `409` for validly authorized requests blocked by SoD or
  workflow state.
- Split `view` from `export`; grant raw-data view/export only through explicit
  sensitivity bindings.

Every grant, revocation, lifecycle, or security-policy change must bump the
principal's authorization version in the same transaction, revoke refresh
families, invalidate permission/session caches, and make stale access tokens fail
on their next request.

## 4. Accepted defaults and remaining work

The captain accepted these defaults on 2026-08-23. They are design decisions,
not claims of implementation:

1. Organization is the security/account tenant; institution/bank is a scoped
   legal entity beneath it.
2. A maker may certify a filing as its preparer and may mechanically transmit it
   only after a distinct checker has certified it as approver. The maker may not
   approve it or certify an approver/checker slot.
3. Existing Org Owners require explicit designation; auto-selection is allowed
   only when exactly one eligible active human admin exists and that migration
   rule is approved.
4. Org Admin receives settings/audit only. Auditor receives evidence and
   published outputs; raw data and export require separate grants.
5. Enforced SSO disables ordinary passwords. Separately governed hardware-MFA
   break-glass accounts remain available with alerts and audit.
6. Membership state is separate from invitation/JIT request state. Identity and
   regulated evidence are not physically deleted.
7. `operator_users` is authoritative; unknown workforce identities are denied;
   examiner impersonation remains fixed and read-only.
8. Tenant deletion becomes a platform-mediated closure request subject to
   retention and legal hold.

Except for the separately authorized staff fail-open fix, these decisions remain
unimplemented. In particular, no scoped-binding kernel, authorization version,
tenant member administration, permission-aware dashboard, generalized approval
platform, linked identity model, SCIM, or closure workflow is created by this
review.

## 5. Prioritized sequence

1. **Correct and inventory.** Treat this review as the proposal correction;
   inventory every tenant/operator route by principal, action, scope, sensitivity,
   read/write/export class, and audit event. Close the staff fail-open separately.
2. **Build the authorization foundation.** Add principal types, scoped bindings,
   canonical locators, tenant-safe constraints, lifecycle, authorization version,
   immediate token/cache invalidation, and deny-by-default policy tests.
3. **Cut over backend enforcement atomically.** Shadow-evaluate first; then replace
   rank checks with permission plus post-load scope checks. Remove legacy `admin`
   from operational authority before mapping Org Admin. Gate reads and exports as
   well as writes.
4. **Make the tenant UI truthful.** Expose effective bindings through `/auth/me`;
   filter navigation, guard deep links, choose authorized landings, and gate
   actions while retaining backend enforcement.
5. **Add safe administration.** Build invitations, lifecycle/offboarding, scoped
   assignment, delegation ceilings, owner safeguards, seat accounting, and an
   RLS-scoped view over existing immutable audit evidence.
6. **Generalize approval/SoD.** Reuse immutable revisions and value-based digests;
   enforce maker/approver separation while retaining preparer certification and
   the accepted mechanical-submit rule.
7. **Add enterprise identity in dependency order.** Normalize email, verify
   domains, add linked identities and discovery, then multi-connection SSO, SCIM,
   enforced SSO, and governed break glass.
8. **Roll out and retire legacy authority.** Activate by tenant/module with denial
   telemetry and explicit Owner designation; remove scalar-role authorization only
   after backfill reconciliation and tested parity.

Do not expose grant mutation before immediate invalidation, map Org Admin while
rank gates still confer operational power, add narrow scopes before canonical
resource enforcement, fork audit/staff authorities, or generalize approval around
mutable objects.
