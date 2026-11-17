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

4. For a bank dashboard handoff, start the local tenant API and dashboard. On
   both local APIs, set the same disposable
   `IMPERSONATION_JWT_SECRET=<local-random-secret>`. Set `BANK_APP_BASE_URL` on
   the operator API to the running loopback dashboard origin, for example
   `http://localhost:3001`, and restart both APIs. Before clicking **Open in
   new tab**, use the [act-token preflight](operator-api.md#inspector-handoff)
   as the same local operator on the active session. The button opens the URL
   automatically, so proceed only after the returned `dashboard_url` exactly
   matches the running loopback origin.

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
- For the inspector handoff, capture the audited, read-only dashboard state
  after completing the preflight.
- Capture a narrow viewport if the change touches layout.
