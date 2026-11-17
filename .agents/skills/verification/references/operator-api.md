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
separate random value from `AUTH_JWT_SECRET`. Operator routes are covered
hermetically by `backend/tests/operator`; run the relevant files for logic, and a
live server for HTTP behavior.

## Sign in

Local only, either:

- **Local operator account.** Create it against the local database with
  `scripts/create_operator.py` (it prints a generated password once), then sign
  in at `POST /operator/auth/login`:

  ```bash
  uv run python scripts/create_operator.py \
    --email <local address> --display-name <name> --role <role>
  ```

- **Dev token.** Set `OPERATOR_DEV_AUTH_ENABLED=1` and a random
  `OPERATOR_DEV_TOKEN` in `backend/.env` and send it as a bearer value. The app
  refuses to boot with dev auth outside an undeployed environment, so this
  cannot reach a deployment. Roles are described in the `.env.example` comments.

Provisioning a bank creates tenant setup records (organization, bank,
administrator, ownership, membership) and no financial data.

## Capture

Same shape as the tenant API: one text file per matrix case with the request
(token redacted), status and body.

```bash
curl --silent --include --max-time 20 \
  -H "Authorization: Bearer $OPERATOR_DEV_TOKEN" \
  http://127.0.0.1:8100/operator/v1/<route> \
  > "$EVIDENCE/operator-api-<case>-after.txt"
```

Mutating operator calls write to the audit log; verify the audit record as part of
the case, and run them only against the disposable local database.
