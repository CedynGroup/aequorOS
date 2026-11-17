# Bank dashboard

The authenticated bank product UI in `backend/dashboard/` (Next.js, :3001).
Every figure comes from the tenant API through the generated client. Routes and
what they read: [the dashboard README](../../../../backend/dashboard/README.md#scope).
Shared rules are in [../SKILL.md](../SKILL.md).

## Reach it

### Hermetic stack (default)

The Playwright configuration boots a disposable stack: the API on a throwaway
SQLite file rebuilt every run, a local OpenID Connect issuer, local MinIO,
and `next dev`.
Setup mints a signed-in browser state per fixture role, so there is no password
to enter. Details and prerequisites:
[End-to-end (Playwright)](../../../../backend/dashboard/README.md#end-to-end-playwright).

- First use: `pnpm --filter @aequoros/dashboard exec playwright install chromium`.
- Run `AQS_LOCAL_SERVICES=native pnpm --filter @aequoros/dashboard e2e:visual`.
  Use `AQS_LOCAL_SERVICES=docker` for Docker-backed MinIO. Both modes provision
  worktree-local MinIO and override inherited storage settings.
- Set `E2E_RUNTIME_DIR` to a directory **outside** the worktree; it holds the
  database, the per-role states (`<role>.json`) and the captures.
- Set the same `E2E_TODAY=YYYY-MM-DD` for the before and after runs so both anchor
  on one reporting date.
- One run per worktree at a time; a lock rejects concurrent runs.
- The fixture bank carries synthetic data only, and the stack never touches a
  primary database.

### Interactive local stack

Use when the case needs a role, bank state, theme or viewport the tour does not
cover. Start PostgreSQL, MinIO, the API, the worker and the dashboard as in
[the root README](../../../../README.md#run-it-locally), with local random secrets
in `backend/.env` and `backend/dashboard/.env.local`:

```bash
pnpm --filter @aequoros/dashboard dev   # http://localhost:3001
```

Provision a bank in the [staff console](console.md) (setup records only), use
the provisioned administrator or a member configured as described in the
[tenant API sign-in reference](tenant-api.md#sign-in), and load
synthetic data through the Data Engine ([tenant API reference](tenant-api.md#load-data)).
Use one browser host throughout (`localhost` or `127.0.0.1`, not both): they have
separate cookie jars.

## Sign in

- During the hermetic run, use the fixture role's state file, for example
  `$E2E_RUNTIME_DIR/analyst.json` as the browser's storage state. Roles are
  listed in `E2E_STORAGE_ROLES` in `backend/dashboard/e2e/support/mint.ts`; each
  is a fixture identity at `e2e.<role>@aequoros.example`. Pick the role that
  matches the matrix row, including the lacking-the-grant role for refusals.
- Interactive: sign in at `/login` with the local account and confirm its
  effective authority for the bank and action before capture.

## Capture

**Tour.** `visual-tour.e2e.spec.ts` captures its listed routes as the `admin` fixture,
dark theme, full page, after loading skeletons clear. Output is
`$E2E_RUNTIME_DIR/visual-tour/<name>.png`, where `/` is `home` and each other `/`
becomes `__` (`/liquidity/nsfr` is `liquidity__nsfr.png`). Run it at the base
commit and again at the head, into two different runtime directories. Use a
tour image for a matrix row only if you observed the final URL and expected
state during that capture. The PNG name records the requested route. Use
interactive capture for unattended tour output and routes outside the tour.

**Interactive capture.** Drive the signed-in browser with the session’s browser
automation. Check the final URL and expected state before saving. Settle first: a
page is loaded when no `.animate-pulse` placeholder remains in the viewport (the
logic is `settleForCapture` in `backend/dashboard/e2e/support/capture.ts`).
Charts render without animation.

- **Light theme.** Dark is the default. Choose Light under Settings → Profile →
  Appearance, or select System before emulating
  `prefers-color-scheme: light`. Confirm `data-theme="light"` on `<html>`
  before saving the light-theme artifact.
- **Narrow viewport.** Existing journeys use 390 px wide for mobile and 1440 px
  for desktop; match them.
- **Regulatory status and commencement.** These journeys use a separate stack
  with a file-controlled reporting clock, started by
  `playwright.regulatory.config.ts` (see the README section above). It writes
  captures to `E2E_EVIDENCE_DIR` when set.
