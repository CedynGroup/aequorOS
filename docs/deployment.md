# AequorOS — Deployment & security checklist

What must be true before a real (internet-facing, real-data) deployment. The data
engine, calc modules, and auth are built; this is the operational shell.

## 1. Secrets (inject from a secrets manager — never commit `.env`)

Backend:
- `AUTH_JWT_SECRET` — signs/verifies app JWTs. Strong random (`openssl rand -base64 48`).
  **Auth fails closed if unset** (no header-trust fallback).
- `SSO_INTERNAL_KEY` — authenticates the dashboard server's internal fetch of the OIDC
  client config (own-OIDC SSO). Same value on backend and dashboard; unset = SSO off.
- `DATABASE_URL` (app role, RLS-forced), `WORKER_DATABASE_URL` (**BYPASSRLS** role — the
  worker claims cross-tenant; login also resolves users cross-tenant through it).
- `CREDENTIAL_VAULT_MASTER_KEY` (AES-256-GCM vendor-credential vault).
- `S3_ACCESS_KEY` / `S3_SECRET_KEY` / `STORAGE_*`.
- `CORS_ORIGINS` — comma-separated; set to the dashboard origin(s) only (never `*`).

Dashboard:
- `AUTH_SECRET` (NextAuth session), `SSO_INTERNAL_KEY` (same value as the backend's).
- `NEXT_PUBLIC_RISK_API_BASE_URL` (the backend `/api/v1` origin).

## 2. SSO (own OIDC relying party — no third-party broker)
- The bank registers an OIDC app in **their** IdP (Google Workspace, Entra, Okta, …) with
  redirect URI `{dashboard origin}/api/auth/callback/sso`, then an AequorOS org admin enters
  issuer / client ID / client secret in **Settings → Authentication** (secret is write-only,
  sealed with `CREDENTIAL_VAULT_MASTER_KEY`). Full runbook: `docs/sso-onboarding.md`.
- The backend independently verifies every id_token against the connection issuer's JWKS
  (zero-trust), enforces `email_verified` and the allowed-email-domain list.
- Users are **pre-provisioned** in AequorOS (matched by email on first SSO login); an
  unknown identity is rejected — no auto-provisioning.

## 3. Database
- `alembic upgrade head` (current head: `202607200015`, adds `sso_connections` + the generic
  `oidc` auth provider).
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
