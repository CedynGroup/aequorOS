# Authorization foundation (as built 2026-08-25)

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
- static bundles: Viewer, Auditor, Analyst, Approver, Account Admin, and the
  machine-only Integration Writer.

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

- `institution_scope=organization` is the explicit institution-wide case and
  requires `institution_id IS NULL`;
- `institution_scope=institution` requires one exact `institution_id`;
- module and sensitivity broad grants store the explicit value `all`.

The database uses composite foreign keys from `(principal_user_id,
organization_id)` to `users` and from `(institution_id, organization_id)` to
`banks`. The service repeats both ownership checks before insert. The table is
tenant-owned and has ENABLE + FORCE RLS with the standard
`app.organization_id` policy. Status/validity constraints make active,
suspended, revoked, scheduled, and expired states unambiguous.

## Decision semantics and conditions

`ResourceLocator` carries exactly the rollout-v1 resource attributes:
organization, optional institution, concrete module, and concrete sensitivity.
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

## Authorization version and deployment transition

Migration `202608250044` adds `users.authorization_version`, initially `1`, and
does **not** backfill any binding. In particular, an existing scalar `admin` is
not silently made an operational principal, Account Admin, or Org Owner in the
new model.

Every newly issued access and refresh token carries the authoritative `authv`.
Tenant request validation compares it with the active user row; refresh also
requires the current value in addition to the server-side token record. A stale
version returns 401. `invalidate_user_authorization()` locks the user, advances
the version, and revokes every refresh family with
`authorization_changed` in one transaction. The binding creation primitive
uses this operation before commit. Future role, scope, status, and security
mutations must do the same.

Tokens issued before this migration have no `authv`. They fail closed for both
access and refresh, even if their signature and old role claims are valid. The
safe deployment consequence is a deliberate one-time re-authentication for
sessions outstanding at deploy time. Existing active users then receive tokens
at version 1 and retain current product behavior through the legacy hierarchy.

## Shadow rollout and next vertical slice

The new evaluator is not wired as an endpoint gate yet. Existing routes keep
their current rank checks; the new table starts empty, grant CRUD is absent, and
no current user is broadened or narrowed by a binding. This allows the schema,
decision trace, and invalidation seam to land without an unsafe partial
authorization cutover.

The next bounded vertical slice should select one institution-scoped Liquidity
read/run flow, resolve its canonical bank resource, create explicit pilot
bindings through a governed non-tenant path, and record legacy-versus-new shadow
decisions. Enforcement should switch only after parity/denial telemetry is
reviewed. Role/grant administration remains later work and must add delegation,
SoD, reason/audit, and last-admin/owner safeguards before exposure.
