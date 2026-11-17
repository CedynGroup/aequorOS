# Staff operator API

The staff control plane in `backend/app/operator/`: a separate ASGI app on :8100
with routes under `/operator/v1` and an unauthenticated `/operator/health`. It is
never mounted on the tenant API. Shared rules are in [../SKILL.md](../SKILL.md).

## Reach it

Start the local stack from [the root README](../../../../README.md#run-it-locally),
then from `backend/`:

```bash
uv run uvicorn app.operator.main:app --port 8100
curl --fail-with-body --max-time 8 http://127.0.0.1:8100/operator/health
```

Operator password sign-in needs `OPERATOR_JWT_SECRET` in `backend/.env`, a
separate random value from `AUTH_JWT_SECRET`.

### Inspector handoff

Start the local tenant API and interactive bank dashboard before capturing an
act-token handoff. Set the same fresh, disposable
`IMPERSONATION_JWT_SECRET=<local-random-secret>` on both local APIs and set
`BANK_APP_BASE_URL` on the operator API to the running loopback dashboard
origin, for example `http://localhost:3001`. Restart both APIs after changing
their settings.

As the local operator who opened an active inspector session, call
`POST /operator/v1/inspector/sessions/{session_id}/act-token`. Confirm that the
returned `dashboard_url` exactly matches the configured loopback origin before
opening it. For a mint-only capture, inspect the response locally and use the
shared evidence procedure below.

## Sign in

Local only, either:

- **Local operator account.** Create it against the local database with
  `scripts/create_operator.py` (it prints a generated password once), then sign
  in at `POST /operator/auth/login`:

  ```bash
  uv run python scripts/create_operator.py \
    --email 'operator@example.test' --display-name 'Fixture Operator' --role '<role>'
  ```

- **Dev token.** Set `OPERATOR_DEV_AUTH_ENABLED=1` and a random
  `OPERATOR_DEV_TOKEN` in `backend/.env` and send it as a bearer value. The app
  refuses to boot with dev auth outside an undeployed environment, so this
  cannot reach a deployment. Roles are described in the `.env.example` comments.

Provisioning a bank creates tenant setup records (organization, bank,
administrator, ownership, membership) and no financial data.

## Capture

Set `OPERATOR_TOKEN` to the local login token or local dev token. Substitute
the route using synthetic IDs. Inspect the exchange locally, then save the
sanitized pair as `operator-api-<case>-after.txt` using the
[shared evidence procedure](../SKILL.md#evidence-record).

```bash
url='http://127.0.0.1:8100/operator/v1/<route>'
printf 'GET %s\n' "$url"
curl --silent --show-error --include --max-time 20 \
  -H "Authorization: Bearer $OPERATOR_TOKEN" "$url"
```

Mutating operator calls write to the audit log; verify the audit record as part of
the case, and run them only against the disposable local database.
