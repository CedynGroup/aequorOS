# Tenant API

The bank-facing FastAPI app in `backend/app/main.py`: business routes under
`/api/v1`, health under `/api/health`. Shared rules (case matrix, evidence
record, local test data, handoff) are in [../SKILL.md](../SKILL.md).

## Reach it

For direct API captures, start the interactive local server. Follow
[the root README](../../../../README.md#run-it-locally): PostgreSQL and MinIO,
`mise run risk-service:bootstrap-db`, then from `backend/`:

```bash
uv run fastapi dev app/main.py --port 8000   # tenant API
uv run python -m app.worker                  # live plane and official runs
```

The worker is required for any figure; without it every Treasury and ALM route
answers "no computed data yet". Native services without Docker:
[the dashboard guide](../../../../backend/dashboard/README.md#local-services-without-docker-or-orbstack)
and `backend/scripts/local_services.py`.

Check readiness before capturing (the process being up is not enough):

```bash
curl --fail-with-body --max-time 8 http://127.0.0.1:8000/api/health/live
curl --fail-with-body --max-time 8 http://127.0.0.1:8000/api/health/ready
```

Read `checks.database.status`, `checks.storage.status` and `checks.worker.status`
in the readiness body, not only the top-level `status`.

## Sign in

Use the administrator created by local bank provisioning. For a different
principal, use Access → Members as the local Org Owner to configure the
member’s scoped grants for the case, then sign in as that member. Use the
local account’s credentials:

```bash
curl --fail-with-body -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<local account>","password":"<local password>"}'
```

The response carries `access_token`. Export it to an environment variable and send
`Authorization: Bearer $TOKEN`; never print it into evidence. The authenticated
identity and its effective authority are `GET /api/v1/auth/me` and
`GET /api/v1/auth/effective-authority`.

Use this same server and token to confirm that the account’s effective authority
matches the matrix case before capture.

## Load data

Only through the Data Engine, from synthetic files: upload in the dashboard, or
the push flow in [docs/API_INTEGRATION.md](../../../../docs/API_INTEGRATION.md)
with `backend/scripts/ingest_push.py`. `backend/scripts/generate_sdi_dataset.py`
generates a synthetic savings-and-loans dataset and
`backend/onboarding/aequoros_sdi/push_sdi.py` loads it. Never seed rows.

Tenant health after a load is read from `GET /api/v1/banks/{bank_id}/live-summary`,
`/freshness` and `/alerts`, which report what the worker computed.

## Capture

Set `BANK_ID` to the local fixture bank’s ID. Inspect the exchange locally,
then save the sanitized pair as `tenant-api-live-summary-permitted-after.txt`
using the [shared evidence procedure](../SKILL.md#evidence-record).

```bash
url="http://127.0.0.1:8000/api/v1/banks/${BANK_ID:?Set the local fixture bank ID}/live-summary"
printf 'GET %s\n' 'http://127.0.0.1:8000/api/v1/banks/BK-XXXXXXXX/live-summary'
curl --silent --show-error --include --max-time 20 \
  -H "Authorization: Bearer $TOKEN" "$url"
```
