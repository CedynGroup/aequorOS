# Tenant API

The bank-facing FastAPI app in `backend/app/main.py`: business routes under
`/api/v1`, health under `/api/health`. Shared rules (case matrix, evidence
record, local test data, handoff) are in [../SKILL.md](../SKILL.md).

## Reach it

**Without a running server.** The pytest suites drive the app in process, and
`mise run risk-service:test` runs the bounded unit set (no database). Integration,
storage and backend end-to-end suites need PostgreSQL 17 and MinIO and are owned
by CI ([backend/README.md](../../../../backend/README.md#run-tests)). Use these for
status codes and error envelopes; use a live server when the claim is about a
real response over HTTP.

**With a local server.** Follow
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

**Hermetic server.** The dashboard's end-to-end stack boots a disposable API on
a throwaway SQLite database with a canonical fixture bank and minted identities
([dashboard reference](dashboard.md)). Use it when the change is checked through
the dashboard anyway.

## Sign in

Use a local account only. Create one against the local database with
`backend/scripts/create_user.py` (it prints a generated password once), then:

```bash
curl --fail-with-body -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<local account>","password":"<from create_user.py>"}'
```

The response carries `access_token`. Export it to an environment variable and send
`Authorization: Bearer $TOKEN`; never print it into evidence. The authenticated
identity and its effective authority are `GET /api/v1/auth/me` and
`GET /api/v1/auth/effective-authority`.

Authority comes from scoped grants, not from the token's role claim. For a
refusal case, use a second account without the grant, or an account in a sibling
bank of the same organization.

## Load data

Only through the Data Engine, from synthetic files: upload in the dashboard, or
the push flow in [docs/API_INTEGRATION.md](../../../../docs/API_INTEGRATION.md)
with `backend/scripts/ingest_push.py`. `backend/scripts/generate_sdi_dataset.py`
generates a synthetic savings-and-loans dataset and
`backend/onboarding/aequoros_sdi/push_sdi.py` loads it. Never seed rows.

Tenant health after a load is read from `GET /api/v1/banks/{bank_id}/live-summary`,
`/freshness` and `/alerts`, which report what the worker computed.

## Capture

Save each request and response for the matrix case as a text file: method and
URL (placeholders for ids), status line, and the body. Redact the bearer token and
use the fixture's ids.

```bash
curl --silent --include --max-time 20 \
  -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/v1/banks/BK-XXXXXXXX/live-summary" \
  > "$EVIDENCE/tenant-api-live-summary-permitted-after.txt"
```

For a contract change also prove the generated client is fresh:

```bash
mise run risk-service:openapi-client   # regenerate packages/risk-service-api
mise run risk-service:api-fresh        # must leave git clean
```

Cases specific to this surface: object ids under `/banks/{bank_id}` must be
bank-scoped at the query, so verify a sibling bank's id is refused; a new route
that takes an object id belongs in the object-reference census
([authorization contract](../../../../backend/docs/authorization_foundation.md#executable-verification)).
