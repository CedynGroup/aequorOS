# Staff operator console

The staff control-plane UI in `console/` (Next.js, :3002). It talks only to the
[operator API](operator-api.md) through its own `/api/op` proxy; browsers never
call the operator API directly. Shared rules are in [../SKILL.md](../SKILL.md).

## Reach it

1. Start PostgreSQL, MinIO and the operator API on :8100
   ([operator API reference](operator-api.md#reach-it)). Add the tenant API if
   the case reads tenant data.
2. Keep `console/.env.local` (copied from `console/.env.example` by
   `mise run setup`) pointing `OPERATOR_API_URL` at `http://127.0.0.1:8100`.
3. Start the console:

   ```bash
   pnpm --filter @aequoros/console dev   # http://localhost:3002
   ```

   The dev server rewrites the tracked `console/next-env.d.ts`; restore it before
   finishing.

## Sign in

Local only. At `/login`, either the email and password of a local operator
account made with `backend/scripts/create_operator.py`, or the dev token under the
collapsed dev disclosure (development builds only), which needs
`OPERATOR_DEV_AUTH_ENABLED=1` and `OPERATOR_DEV_TOKEN` on the backend. Workforce
SSO is for deployed environments and is not a verification path.

## Capture

Drive the page with the session's browser automation and save full-page or
element screenshots per matrix row, named `console-<case>-<before|after>.png`.

- Provisioning flows write setup records and audit entries: capture the screen
  and the resulting operator API record, and use only the disposable local
  database.
- Impersonation and the tenant inspector open a tenant view for an audited
  support session; capture the audited, read-only state and nothing real.
- Capture a narrow viewport if the change touches layout.
