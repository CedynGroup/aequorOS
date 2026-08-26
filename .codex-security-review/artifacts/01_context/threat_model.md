# Threat model: authorization foundation diff

## Overview

The change adds a shadow-only scoped authorization kernel to the tenant backend. Persisted `authorization_bindings` rows combine principal type, a static permission bundle, organization or institution scope, module, sensitivity, provenance, validity, and lifecycle. The evaluator denies by default and ORs only rows whose complete tuple matches (`backend/app/core/authorization.py:290`; `backend/app/models/authorization.py:37`). Live endpoint enforcement remains on verified legacy JWT roles; the immediate enforcing change is an `authv` generation carried by access and refresh tokens and checked against the active tenant user (`backend/app/api/deps.py:318`; `backend/app/services/authentication.py:571`).

## Assets and trust boundaries

- Tenant operational and regulatory data must remain scoped to the correct organization and institution.
- Binding integrity must preserve principal type, role bundle, all scope dimensions, provenance, and lifecycle as one indivisible authority record.
- `users.authorization_version`, JWT role/authv claims, and refresh-token lineage are security-sensitive identity state.
- Verified JWT claims cross into `TenantContext`, then into a tenant-scoped SQLAlchemy session and PostgreSQL `app.organization_id` RLS context (`backend/app/api/deps.py:190`; `backend/app/db/session.py:259`).
- A future administrative caller will cross into `create_role_binding`; that entrypoint must independently enforce delegation and separation of duties because the current primitive validates recorded grantor identity but not caller authority (`backend/app/services/authorization.py:178`).
- Workflow owners supply demo-mode, maker/checker, step-up, and limit conditions. The evaluator makes supplied failures global vetoes but cannot detect an omitted condition (`backend/app/core/authorization.py:195`; `backend/app/core/authorization.py:369`).

## Attacker capabilities and objectives

Remote callers may submit login, refresh, and bearer-token inputs but are not assumed to possess the JWT signing secret, a BYPASSRLS database credential, or an operator identity. Authenticated tenants control request paths and payloads but cannot edit signed org, subject, role, or authv claims. A token thief may replay a still-current token; advancing authv must make that token unusable. Database-owner and trusted-deployment compromise are privileged prerequisites, not baseline attacker capabilities.

Security objectives are to deny without an active exact binding, prevent cross-row scope composition, enforce tenant-consistent principal and institution ownership, keep human and machine bundles distinct, apply workflow vetoes globally, and invalidate all normal sessions whenever authority changes. The migration intentionally creates no implicit bindings and does not replace endpoint role gates (`backend/alembic/versions/202608250044_authorization_foundation.py:6`).

## Assumptions and review limits

The scope is commit range `80b72288ceac42784f275fff617e4a4767b48425..9e184597b32467f076e2101bd3d1ea7666155dc1`. Supporting auth, session, and integration-key code was inspected only to resolve changed behavior. No tests, application execution, network access, or source modification were performed. PostgreSQL RLS enforcement assumes migration `202608250044` is applied; SQLite test paths rely on application scoping. Integration keys and operator impersonation deliberately use separate credential lifecycles and do not carry `authv` (`backend/app/api/deps.py:32`).
