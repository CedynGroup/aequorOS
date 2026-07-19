# AequorOS — Deployment & security checklist

What must be true before a real (internet-facing, real-data) deployment. The data
engine, calc modules, and auth are built; this is the operational shell.

## 1. Secrets (inject from a secrets manager — never commit `.env`)

Backend:
- `AUTH_JWT_SECRET` — signs/verifies app JWTs. Strong random (`openssl rand -base64 48`).
  **Auth fails closed if unset** (no header-trust fallback).
- `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID` — SSO id_token verification (JWKS).
- `DATABASE_URL` (app role, RLS-forced), `WORKER_DATABASE_URL` (**BYPASSRLS** role — the
  worker claims cross-tenant; login also resolves users cross-tenant through it).
- `CREDENTIAL_VAULT_MASTER_KEY` (AES-256-GCM vendor-credential vault).
- `S3_ACCESS_KEY` / `S3_SECRET_KEY` / `STORAGE_*`.
- `CORS_ORIGINS` — comma-separated; set to the dashboard origin(s) only (never `*`).

Dashboard:
- `AUTH_SECRET` (NextAuth session), `AUTH0_CLIENT_ID` / `AUTH0_CLIENT_SECRET` / `AUTH0_DOMAIN`.
- `NEXT_PUBLIC_RISK_API_BASE_URL` (the backend `/api/v1` origin).

## 2. Auth0
- App type: Regular Web Application.
- **Allowed Callback URL**: `{dashboard origin}/api/auth/callback/auth0`.
- **Allowed Logout URL**: `{dashboard origin}`.
- Users are **pre-provisioned** in AequorOS (matched by email on first SSO login); an
  unknown identity is rejected — no auto-provisioning.

## 3. Database
- `alembic upgrade head` (current head: `202607180013`, adds the user credential/RBAC fields).
- **Seed the first admin** (else nobody can log in) — a user with `role='admin'`,
  `auth_provider='password'`, and an Argon2id `password_hash` (see `app.core.security.hash_password`).

## 4. Serving topology
- **API**: the container runs `fastapi run … --workers ${WEB_CONCURRENCY:-4}`. Put a reverse
  proxy / load balancer in front for **TLS termination** and standard security headers.
- **Worker**: a SEPARATE process (`python -m app.worker`, its own container/service). Do **not**
  set `RUN_INPROCESS_WORKER` on the API — N API workers must not each spawn a poller.
- Scale workers horizontally with `docker compose up --scale risk-worker=N` (claims use
  `FOR UPDATE SKIP LOCKED`); a startup + periodic reaper reclaims jobs orphaned by a crash.

## 5. AuthZ model (enforced)
- Every request is authenticated by verifying a bearer JWT (zero-trust); no header identity.
- Roles: `admin > approver > analyst > viewer`. **Mutations require `analyst`+**, so `viewer`
  accounts are strictly read-only (the single gate is `get_mutation_tenant_context`).

## 6. Still open before prod
- **Dependabot**: triage the outstanding advisories on `main` (3 critical) — bump/patch.
- Rotate the seeded admin password after first login.
- Consider a JWT-revocation/denylist if instant session kill is required (tokens are
  short-lived — 15 min access — so this is optional for a pilot).
