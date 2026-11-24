# AequorOS Portal (`@srg/portal`)

The Bank of Ghana supervisors' portal onto the AequorOS Reporting Gateway:
review, query and accept the filings institutions submit through the API.
Next.js 16.4.5, React 19.3, Tailwind CSS 4, port **3100**. It talks to the
gateway only through the supervisor API (`/portal/api`, `../contract/SPEC.md`
Part 3) and shares no code with anything outside `gateway/`.

## Run it

From `gateway/` (its own pnpm workspace; never the repository root):

```bash
pnpm install
```

### Mock mode: no API, no identity provider

```bash
cd portal
GATEWAY_PORTAL_MOCK=1 pnpm dev        # http://localhost:3100
```

Sign-in lists SPEC Part 4's synthetic staff: Kofi Owusu (senior supervisor),
Akosua Boateng (supervisor), Esi Asante (returns policy), Michael Adjei
(returns policy and access approver), Internal Audit (auditor). Every screen
renders the design's data (`../design/*.dc.html`). Decisions work against an
in-memory copy that lasts until the server restarts, and the mock refuses what
the API would refuse: roles that may not decide, and separation of duties
(for example, Esi drafted BSD5A 2026.2, so she cannot approve it). Mock mode
is refused when `GATEWAY_ENV=production`.

### Real mode: against the gateway API

Start the gateway service on :8200 (`../api`), then:

```bash
cd portal
pnpm dev
```

Sign-in sends you to the identity provider (`GATEWAY_OIDC_ISSUER`; locally the
API's development provider at `/dev/oidc`) with the authorization-code flow
and PKCE. The provider must accept `http://localhost:3100/auth/callback` as a
redirect URI for client `srg-portal`.

### Production build

```bash
pnpm build && pnpm start              # standalone output in .next/standalone
```

## Environment

All variables are server-side. See `.env.example` for the full list.

| Variable | Default (local/test) | Purpose |
| --- | --- | --- |
| `GATEWAY_ENV` | `local` under `next dev`, else `production` | Unset in a deployment means strict. |
| `GATEWAY_PORTAL_MOCK` | off | `1` serves fixtures and synthetic sign-in. |
| `GATEWAY_API_URL` | `http://localhost:8200` | The gateway service. https required in production. |
| `GATEWAY_OIDC_ISSUER` | `http://localhost:8200/dev/oidc` | BoG's OIDC provider. https required in production. |
| `GATEWAY_OIDC_CLIENT_ID` | `srg-portal` | The portal's client id at the provider. |
| `GATEWAY_OIDC_CLIENT_SECRET` | none | Only for a confidential client registration. |
| `GATEWAY_OIDC_SCOPE` | `openid profile email` | Add `groups` if the provider needs it. |
| `GATEWAY_PORTAL_SESSION_SECRET` | random per process | ≥ 32 characters; required outside local/test. |
| `GATEWAY_PORTAL_BASE_URL` | request origin | Public origin behind a proxy. |
| `NEXT_SERVER_ACTIONS_ENCRYPTION_KEY` | per build | Next.js: set one shared key when running more than one instance. |

## How it is built

- `lib/api/types.ts`: SPEC Part 3's shapes, field for field. `lib/api/client.ts`
  is the HTTP client (Bearer = the user's ID token; RFC 9457 problems become
  `GatewayApiError`). `lib/api/mock/` is mock mode behind the same interface.
- `lib/server/` is server-only: the encrypted session cookie, the API
  accessor (`getApi()` checks the session on every read; a 401 from the API
  ends the session), and page-load helpers.
- Sign-in (`app/auth/*`, `app/sign-in`): OIDC code flow with PKCE; state,
  nonce and verifier sealed in a short-lived httpOnly cookie; the ID token is
  kept in an AES-256-GCM sealed httpOnly cookie and never reaches browser
  JavaScript. The portal checks state, nonce, issuer, audience and expiry; the
  API verifies the token's signature on every call. `proxy.ts` redirects
  requests without a session cookie to sign-in.
- Decisions are server actions (`app/(portal)/actions.ts`); the API is the
  authority, the UI offers buttons from each record's `allowed_actions` and
  the user's roles (`lib/roles.ts`). Downloads (CSV exports, the audit bundle)
  are route handlers under `app/export/`.
- BoG staff never enrol institutions or manage credentials: the portal has no
  code path to any enrolment endpoint. `/onboarding` ("Channel register") is
  read-only.

## Checks

```bash
pnpm --filter @srg/portal typecheck
pnpm --filter @srg/portal lint
pnpm --filter @srg/portal test        # node --test over tests/**
pnpm --filter @srg/portal build
```
