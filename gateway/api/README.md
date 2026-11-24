# AequorOS Reporting Gateway — API service

FastAPI service for the gateway: the public institution filing API (`/oauth/token`,
`/v1`), the BoG supervisor API (`/portal/api`), the operator API (`/ops/api`) and, in
`local`/`test` only, a development OpenID provider (`/dev/oidc`). Contract:
[`../contract/SPEC.md`](../contract/SPEC.md); architecture:
[`../ARCHITECTURE.md`](../ARCHITECTURE.md). This folder is a uv project of its own and
imports nothing from the rest of the repository.

## Run it locally (port 8200)

```bash
cd gateway/api
uv sync
export GATEWAY_ENV=local
export GATEWAY_DATABASE_URL=postgresql+psycopg://postgres@localhost:55415/gateway_dev
export GATEWAY_BASE_URL=http://localhost:8200
uv run alembic upgrade head
uv run python -m app.seed            # the design's synthetic world (about 2 minutes on Postgres)
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8200
```

Without `GATEWAY_DATABASE_URL` the service uses `var/gateway-local.db` (SQLite).
`python -m app.seed --reset` drops and recreates the schema first (`local`/`test` only);
without `--reset` it refuses a database that already holds institutions, and it always
refuses `GATEWAY_ENV=production`.

On first start in `local`/`test` the service writes, under the gitignored `.keys/`:

| Path | What |
| --- | --- |
| `.keys/gateway-receipt-2026-q4.pem`, `.keys/gateway-audit-2026-q4.pem` | gateway receipt and audit keys (ES256) |
| `.keys/dev-oidc-2026-q4.pem` | development identity provider key |
| `.keys/ca/gateway-ca.{key,crt}.pem` | the local client-certificate CA |
| `.keys/operator/operator.json`, `client.key.pem`, `client.crt.pem`, `ca.crt.pem`, `client_id` | the development operator client (`dev-operator-01`, scope `ops:enrol`) |

`operator.json` holds `client_id`, `kid`, `key_file`, `certificate_file`, `ca_file`,
`certificate_sha256` and `scope`; these names are stable.

### Sign in as synthetic staff

The portal runs the authorization-code flow with PKCE against
`http://localhost:8200/dev/oidc` (client id `srg-portal`, redirect URIs under
`http://localhost:3100/` or `http://127.0.0.1:3100/`) and sends the ID token as the
Bearer token. `/dev/oidc/authorize` lists the synthetic staff: Kofi Owusu (senior
supervisor), Akosua Boateng (supervisor), Esi Asante (returns policy), Michael Adjei
(returns policy, access approver), Internal Audit (auditor). Every `/dev/*` path is a
404 in `sandbox` and `production`.

### File a return like an institution

```bash
uv run python scripts/file_return.py --base-url http://localhost:8200 \
  --operator-dir .keys/operator --state-dir .keys/reference-client
```

The reference client enrols institution `0901` through `/ops/api` with the development
operator client, obtains a token with `private_key_jwt`, signs and files BSD2 (an
amendment on re-runs), retries with the same `Idempotency-Key` and verifies the receipt
against `/v1/.well-known/jwks.json`. It imports nothing from `app/`.

## Configuration (`GATEWAY_*`)

| Variable | Default | Notes |
| --- | --- | --- |
| `GATEWAY_ENV` | `local` | `local`, `test`, `sandbox`, `production` |
| `GATEWAY_DATABASE_URL` | `sqlite+pysqlite:///./var/gateway-local.db` | Postgres: `postgresql+psycopg://…` |
| `GATEWAY_MIGRATION_DATABASE_URL` | empty | migrations only: the schema owner's URL (falls back to `GATEWAY_DATABASE_URL`) |
| `GATEWAY_DB_APP_ROLE` | empty | migrations grant this role only what the API needs (also `-x app_role=`) |
| `GATEWAY_BASE_URL` | `http://localhost:8200` | issuer of access tokens; token endpoint = base + `/oauth/token` (the assertion `aud`) |
| `GATEWAY_TRUSTED_PROXIES` | empty | CIDRs allowed to send `X-Forwarded-Tls-Client-Cert` (any client in `local`/`test`) |
| `GATEWAY_OIDC_ISSUER` | the dev provider in `local`/`test` | BoG identity provider; JWKS via discovery or `GATEWAY_OIDC_JWKS_URL` |
| `GATEWAY_OIDC_AUDIENCE` | `srg-portal` | ID-token audience |
| `GATEWAY_KEYS_DIR` | `gateway/api/.keys` | local key provider directory |
| `GATEWAY_RECEIPT_KEY_ID`, `GATEWAY_AUDIT_KEY_ID`, `GATEWAY_RETIRED_RECEIPT_KEY_IDS` | `gateway-receipt-2026-q4`, `gateway-audit-2026-q4`, empty | key ids; retired receipt keys stay in the JWKS |
| `GATEWAY_OPERATOR_CLIENTS` | empty | JSON list of `{client_id, jwks, certificate_sha256}` operator clients |
| `GATEWAY_DUE_BUSINESS_DAYS`, `GATEWAY_DUE_BUSINESS_DAYS_OVERRIDES` | see `app/core/settings.py` | **placeholder** due-date offsets (business days after the reporting date); BoG has not given the rule |
| `GATEWAY_GROUP_ROLES` | the ARCHITECTURE table | identity group → role |
| `GATEWAY_AUDIT_VERIFY_INTERVAL_SECONDS` | `1800` | scheduled chain verification |
| `GATEWAY_VALIDATION_SWEEP_SECONDS` | `0` (off) | re-validate filings stuck in `validating` |

Production refuses development conveniences: no dev provider, no header certificates
outside trusted proxies, no seeding, and no local key files (it needs a KMS/HSM
`KeyProvider` and CA, not built here).

Deploying (Coolify, mutual TLS at the proxy, issuing the operator client with
`python -m app.enrolment.issue_operator`, key backups): [`../deploy/README.md`](../deploy/README.md).

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run basedpyright                      # app/ strict (reportAny, reportUnknown*); tests/scripts standard
uv run pytest                            # SQLite; Postgres tests skip
GATEWAY_TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:55415/gateway_dev uv run pytest
uv run python scripts/export_openapi.py  # rewrite ../contract/*.openapi.json (tests fail when stale)
```

Postgres tests create a disposable schema per session (`srg_test_…`, dropped at the end)
and cover the advisory-locked audit append, gapless receipt numbers, the append-only
trigger and the API role's grants (`alembic upgrade head -x app_role=<role>`).

## Layout

| Path | What |
| --- | --- |
| `app/core` | settings, injectable clock, RFC 8785 JCS, errors (RFC 9457), gapless sequences |
| `app/db` | SQLAlchemy models, sessions, programmatic migrations |
| `app/audit` | the hash chain (append, verify), audit views and the signed export bundle |
| `app/keys` | `KeyProvider`, JWK/JWS helpers, the JWKS endpoint |
| `app/auth` | staff ID tokens and roles; machine clients (mTLS + `private_key_jwt` + bound tokens) |
| `app/enrolment` | operator API (Part 5), the client-certificate CA, operator clients |
| `app/returns` | return registry, draft/approval workflow, rule engine, synthetic definitions |
| `app/filing` | submissions, receipts, validation, obligations and the `/v1` router |
| `app/queries` | supervisor queries and institution responses |
| `app/access` | operator support sessions |
| `app/portal` | supervisor API (Part 3) |
| `app/devidp` | development OpenID provider and synthetic staff |
| `app/seed` | `python -m app.seed` |
| `scripts/` | `export_openapi.py`, `file_return.py` (reference client) |
