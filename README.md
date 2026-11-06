# AequorOS

AequorOS is treasury, asset-liability management and regulatory-reporting software
for banks in Africa. A bank loads its own data, and AequorOS calculates six
things on the server: liquidity, Basel capital, interest-rate risk in the banking
book (IRRBB), foreign exchange, funds transfer pricing (FTP) and balance-sheet
forecasts. Each organization's data is isolated from every other's, and each
official calculation is saved as an immutable run pinned to a hash of its inputs.
Country rules are stored as data, not code. The only return family built today is
the Bank of Ghana BSD set, generated from the official workbook templates.

## Repository layout

```
aequorOS/
├── backend/                  # ── THE PRODUCT ──
│   ├── app/                  #   FastAPI tenant API: the six engines, app/forecasting/ml (LSTM),
│   │                         #   Data Engine, BoG return generation, attestation,
│   │                         #   Postgres + row-level-security tenancy
│   ├── app/operator/         #   staff control plane — a SEPARATE ASGI app (:8100),
│   │                         #   never mounted on the tenant API
│   ├── app/worker.py         #   background worker: live-plane refresh + official runs
│   ├── dashboard/            #   the product UI (Next.js) → bank.aequoros.com
│   └── alembic/ tests/ scripts/ docker-compose.yml
├── console/                  # staff operator console (Next.js) → OPERATOR_API_URL, default :8100
├── frontend/                 # marketing site (Next.js) → aequoros.com
├── packages/
│   └── risk-service-api/     # generated TypeScript client (API ⇄ UI contract)
├── deploy/                   # OpenBao (signing-key custody) compose
├── questions/                # answered scoping questions, kept as a decision record
└── ARCHITECTURE.md           # system map + conventions (start here)
```

Specs, runbooks and the internal audit register live in `docs/`, which is private
by default; `.gitignore` publishes only explicitly allow-listed files. Anything
cited below that you cannot find here is held privately — ask for it.

### Deployables

| App                 | Production build                                                                                                                             | Domain                                |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `frontend`          | Vercel (see `frontend/README.md`)                                                                                                            | `aequoros.com`                        |
| `backend/dashboard` | `docker-compose.dashboard.yml` — Docker Compose build pack, **repo root** as build context                                                   | `bank.aequoros.com`                   |
| `backend`           | `backend/docker-compose.prod.yml` — `risk-migrate` (one-shot `alembic upgrade head`), `risk-api` :8000, `risk-worker`, `risk-operator` :8100 | `api.aequoros.com` on `risk-api` only |
| `console`           | `docker-compose.console.yml` — Docker Compose build pack, **repo root** as build context                                                     | intended `console.aequoros.com`       |

`backend/.dockerignore` excludes `dashboard/`, so the API image never carries the UI.

The compose files require the coordinated certificate and proxy preparation in
[the transport deployment contract](backend/docs/transport_security.md#deployment-preparation-review-before-applying).
The audit-chain migration also requires
[migration credential and audit ownership preparation](backend/docs/tamper_evident_records.md#required-deployment-follow-up).

## Prerequisites

| Tool                                              | Version                        | Pinned in                                    | Used for                                                                                                   |
| ------------------------------------------------- | ------------------------------ | -------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| [mise](https://mise.jdx.dev/getting-started.html) | recent release                 | not pinned                                   | Installs the tools below and runs every project task                                                       |
| Node.js                                           | 24                             | `.nvmrc`, `mise.toml`, `engines.node >= 24`  | Dashboard, console, marketing site, generated API client                                                   |
| pnpm                                              | 11.2.2, exactly                | `package.json` `packageManager`, `mise.toml` | Workspace installs. `engineStrict` makes any other version fail                                            |
| Python                                            | 3.13 (`>=3.13,<3.14`)          | `backend/pyproject.toml`, `mise.toml`        | Backend API, worker and tests                                                                              |
| [uv](https://docs.astral.sh/uv/)                  | 0.11.18                        | `mise.toml`                                  | Locked Python dependencies                                                                                 |
| PostgreSQL                                        | 17                             | `backend/docker-compose.yml`, CI             | Running the API and the Postgres-backed tests. 18 is refused by the local test runner                      |
| S3-compatible storage                             | MinIO locally                  | `backend/.env.example`                       | Uploads and exports. The API reports "not ready" without it, and the storage and end-to-end suites need it |
| Docker (optional)                                 | Compose 2.24.4 or newer        | `backend/docker-compose.yml`                 | One way to run Postgres and MinIO. Native binaries work instead                                            |
| OpenBao (optional)                                | 2.6.1 in the `deploy/` compose | `deploy/openbao/docker-compose.openbao.yml`  | Signing-key custody. Locally, only the signing integration tests need it                                   |

mise installs Node, pnpm, Python and uv for you. Automatic installation is switched
off in `mise.toml`, so a mise task run before `mise run setup` reports the missing
tool instead of downloading it.

## Set up a fresh checkout

From the repository root:

```bash
mise run setup    # install pinned tools and locked dependencies, copy env templates, install git hooks
mise run verify   # check the checkout is ready; needs no Docker, database or service
```

`setup` is safe to repeat. It installs JavaScript dependencies with
`pnpm install --frozen-lockfile` and Python dependencies with `uv sync` under
`UV_LOCKED=1`, so a stale lockfile fails instead of being used. `verify` installs
nothing. It checks tool versions, dependencies, hooks and env files, runs the
backend CI task-wiring test and the dashboard typecheck, and reports optional
services (PostgreSQL, MinIO, OpenBao, Docker) separately from core readiness.

`setup` copies each template to a local file only if the local file does not
exist yet. All four are git-ignored:

| Template                         | Local file                     | What to fill in                                                                                                                                                                                                              |
| -------------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/.env.example`           | `backend/.env`                 | `DATABASE_URL`, a real `AUTH_JWT_SECRET` (`openssl rand -base64 48`), the `S3_*` storage values, and a separate `OPERATOR_JWT_SECRET` (`openssl rand -hex 32`) for console password sign-in, distinct from `AUTH_JWT_SECRET` |
| `backend/dashboard/.env.example` | `backend/dashboard/.env.local` | A real `AUTH_SECRET` (`openssl rand -base64 32`). `NEXT_PUBLIC_RISK_API_BASE_URL` already points at `:8000`                                                                                                                  |
| `console/.env.example`           | `console/.env.local`           | `OPERATOR_API_URL` already points at `:8100`. Staff sign-in options are in the file                                                                                                                                          |
| `frontend/.env.example`          | `frontend/.env.local`          | Nothing is needed to run the marketing site. The contact form needs a Resend key                                                                                                                                             |

Template placeholders count as unconfigured. `verify` and `setup` say so and never
print secret values.

If `core.hooksPath` is already set (for example by a hook manager), `setup` leaves
it alone, reports the hooks as unverified and exits non-zero. That is deliberate.
The output lists the pre-commit, commit-msg and pre-push commands your manager
must run, including argument and pre-push stdin forwarding. Remote access, Git
identity, credentials and optional services are reported but do not make the
checkout "ready" on their own.

## Run it locally

The API needs PostgreSQL and S3-compatible storage. Start them with Docker, or use
your own instances.

```bash
# Postgres :15432 and MinIO :9000 (console :9001). Name the services: a bare
# `docker compose up -d` also builds and starts the API and worker containers,
# and the API container listens on :8000.
cd backend && docker compose up -d risk-postgres risk-minio risk-minio-init
```

Then, in `backend/.env`, set
`DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:15432/risk_service`.
The compose init container creates a bucket named `risk-local`, while
`.env.example` says `S3_BUCKET=aequoros`, so set `S3_BUCKET=risk-local` for the
bundled MinIO.

```bash
cd backend

# 1. Database. On a local database this prepares the development roles and
#    applies every migration. It requires APP_ENV=local and a localhost
#    administrator DATABASE_URL. This task does not read backend/.env; export
#    DATABASE_URL or pass it explicitly (otherwise it defaults to the bundled
#    compose database on 127.0.0.1:15432). Use your configured URL here.
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:15432/risk_service mise run risk-service:bootstrap-db
#    For a database whose roles are already prepared, apply migrations alone:
#    mise run risk-service:migrate

# 2. Tenant API on :8000, the port the dashboard expects
#    (NEXT_PUBLIC_RISK_API_BASE_URL=http://127.0.0.1:8000/api/v1). It includes the
#    LSTM cash-flow module, which trains on the first forecast call or reuses
#    backend/artifacts/cashflow/.
uv run fastapi dev app/main.py --port 8000

# 3. Background worker. Required: it writes current_financial_facts and
#    live_metrics, and without it every Treasury/ALM page reads "no computed data
#    yet". With a Postgres credential split, set WORKER_DATABASE_URL as in
#    docs/ops/database-roles.md#worker-compatibility-migration. For local work you
#    can set RUN_INPROCESS_WORKER=1 (default off) to poll inside the API process.
uv run python -m app.worker

# 4. Staff operator API on :8100 (optional; never mounted on the tenant API).
#    Before starting, set OPERATOR_JWT_SECRET in backend/.env for console
#    password sign-in. Generate it separately with openssl rand -hex 32;
#    do not reuse AUTH_JWT_SECRET.
uv run uvicorn app.operator.main:app --port 8100
```

`mise run risk-service:dev` starts the API and worker together, but it listens on
port **8001** unless you set `PORT=8000`.

```bash
# From the repo root, in separate terminals.
pnpm --filter @aequoros/dashboard dev   # product dashboard  http://localhost:3001
pnpm --filter @aequoros/console   dev   # operator console   http://localhost:3002 (proxies to :8100)
pnpm --filter @aequoros/frontend  dev   # marketing site     http://localhost:3000
```

Check the backend with `GET /api/health/live` (process is up) and
`GET /api/health/ready` (database, storage and worker are reachable). The dashboard
answers `GET /api/health/live` on its own port.

**There is no seed step.** Create the first staff account with
`uv run python scripts/create_operator.py --email <you> --display-name <name> --role operator_admin`
(run in `backend/`; it prints a generated password once). Console password sign-in
also requires the separate `OPERATOR_JWT_SECRET` on the backend described above.
Alternatively, for local development only, enable the dev token described in
`backend/.env.example` and `console/.env.example`. Provision a bank in the staff
console, then load its financial data through the Data Engine: Excel/CSV upload,
API push, or a read-only database extract. See the
[Data Engine's no-seeding contract](docs/data_engine.md#standing-order-no-seeded-bank-data).
Provisioning creates the starting board parameter register, including IRR, FX and
FTP stress assumptions for banks. The FX/FTP defaults are internal calibrations,
not regulatory requirements. Applying the migration chain fills missing FX/FTP
scenario keys for existing banks while retaining their board values.
The hermetic pytest suite builds its own tenants in `backend/tests/conftest.py`;
its fixture financial records are test-only.

**Database-direct drivers.** The shipped image installs only the Oracle thin
driver, which is a core dependency. SQL Server/ODBC, generic JDBC and Snowflake
live behind the `db-direct` extra, which `backend/Dockerfile` does not install;
`pyodbc` and JDBC additionally need system packages (unixODBC plus a vendor ODBC
driver, and a JRE). Those backends are built and tested but not enabled in the
default deployment image. See `backend/README.md`.

### Common pitfalls

- **Wrong port.** The dashboard looks for the API on `:8000`. `mise run
risk-service:dev` defaults to `:8001`; pass `PORT=8000`.
- **Empty Treasury/ALM pages.** The worker is not running.
- **`/api/health/ready` returns 503.** It needs the database, object storage and
  worker. `/api/health/live` only proves the process is up.
- **Sign-in fails.** The backend refuses to issue tokens without `AUTH_JWT_SECRET`,
  and the dashboard needs its own `AUTH_SECRET`. Replace both template placeholders.
- **Signed out after a restart or host change.** `localhost` and `127.0.0.1` have
  separate cookie jars. Use one consistently.
- **`pnpm` refuses to run.** The workspace requires exactly the pinned pnpm. Run
  `mise run setup`, or `corepack enable` with Node 24.
- **`psql` shows no rows.** Tenant tables use forced row-level security. Set
  `app.organization_id` first; see [the backend guide](backend/README.md#run-the-api).
- **`console` dev server leaves a diff.** `pnpm --filter @aequoros/console dev`
  rewrites the tracked `console/next-env.d.ts`. Do not commit that change.
- **Pointing tests at the primary database.** Don't. Mutating tests use disposable
  databases; see [the test database guide](backend/README.md#test-databases-and-the-primary-database).

## Validation

Backend, from the repository root or `backend/`:

```bash
mise run risk-service:check          # ruff, basedpyright strict baseline, then the combined hermetic pytest run
mise run unit                        # database-free tests; any database connection fails
mise run risk-service:test-postgres  # pytest against a disposable local Postgres, or your TEST_DATABASE_URL
mise run integration                 # database and storage tests on disposable local services
mise run risk-service:test-e2e       # backend Playwright contracts
```

The hermetic default runs on SQLite with row-level security switched off, so a
green run is **not** evidence about Postgres. Boolean server defaults, String-vs-UUID
comparisons, row locks and RLS policies all behave differently there. The
Postgres-gated suites opt in through `TEST_DATABASE_URL`, which must name a
dedicated test database. See the backend's
[test database guide](backend/README.md#test-databases-and-the-primary-database)
for schema isolation and parallel execution. Task definitions are in
`backend/mise.toml`.

Without Docker, the Postgres and storage tasks start worktree-local PostgreSQL 17
and MinIO on free loopback ports, with data in the git-ignored `.local-services/`.
They never touch an existing PostgreSQL service. Force this with
`AQS_LOCAL_SERVICES=native`:

```bash
# macOS
brew install postgresql@17 homebrew/core/minio
# Linux: install the PostgreSQL 17 server binaries, MinIO and lsof, then point at them
export AQS_POSTGRES_BIN=/usr/lib/postgresql/17/bin   # adjust to your PostgreSQL 17 bin directory

AQS_LOCAL_SERVICES=native mise run risk-service:test-postgres-suite
AQS_LOCAL_SERVICES=native pnpm --filter @aequoros/dashboard e2e
```

Data and ports are isolated per worktree. See the dashboard's
[local service guide](backend/dashboard/README.md#local-services-without-docker-or-orbstack).

TypeScript surfaces, from the repository root:

```bash
pnpm --filter @aequoros/dashboard typecheck && pnpm --filter @aequoros/dashboard lint \
  && pnpm --filter @aequoros/dashboard test && pnpm --filter @aequoros/dashboard build
pnpm --filter @aequoros/dashboard e2e             # disposable stack; package specs need S3/MinIO
pnpm --filter @aequoros/console typecheck && pnpm --filter @aequoros/console lint \
  && pnpm --filter @aequoros/console test && pnpm --filter @aequoros/console build
pnpm --filter @aequoros/frontend typecheck && pnpm --filter @aequoros/frontend lint \
  && pnpm --filter @aequoros/frontend build
pnpm --filter @aequoros/risk-service-api test

# After an API change, regenerate the client and prove it is fresh. The second
# command must leave git clean; it is also the pre-push hook.
mise run risk-service:openapi-client
mise run risk-service:api-fresh
```

CI coverage and the full gate list: [Architecture §8](ARCHITECTURE.md#8-validation-commands).

## Security features

These are the protections the code and tests support today. Each line points to its
implementation and to a test that exercises it. A **(partial)** label means the
feature works but is optional, off by default, or only partly covered by the tests
that can run without extra services. The list describes what the code does. It is
not a security audit, and it is not a certification (see [Status](#license--security)).

| Feature                                             | What it does                                                                                                                                                                                                                                             | Source and tests                                                                                                                                                                                                                                                                 |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tenant data isolation                               | Every table that carries `organization_id` forces Postgres row-level security, except tables the test names as cross-tenant by design.                                                                                                                   | `backend/alembic/versions/`; `backend/tests/db/test_tenant_rls_completeness_integration.py`                                                                                                                                                                                      |
| Password storage and lockout                        | Passwords are hashed with Argon2id, and repeated failed sign-ins lock the account.                                                                                                                                                                       | `backend/app/core/security.py`, `backend/app/identity/service/auth_throttle.py`; `backend/tests/core/test_security.py`, `backend/tests/identity/service/test_authentication_throttle_integration.py`                                                                             |
| Signed sessions that fail closed                    | Access and refresh tokens are signed. With no signing secret configured, tokens are neither issued nor accepted.                                                                                                                                         | `backend/app/core/security.py`; `backend/tests/core/test_security.py`                                                                                                                                                                                                            |
| Refresh-token rotation                              | Each use rotates the refresh token. A second presentation within a short concurrency grace window (default 30 seconds) is treated as a concurrent refresh, not reuse. Reuse outside that window revokes the whole session family; logout revokes it too. | `backend/app/identity/service/authentication.py`; `backend/tests/identity/api/test_refresh_tokens_integration.py`                                                                                                                                                                |
| Single sign-on (OIDC)                               | AequorOS is its own OIDC client. It verifies ID tokens against the issuer's published keys and binds the sign-in to the right tenant. A just-in-time sign-in creates a deactivated account that waits for administrator approval.                        | `backend/app/core/security.py`, `backend/app/identity/service/authentication.py`, `backend/app/identity/service/sso_config.py`; `backend/tests/identity/service/test_sso_tenant_binding_integration.py`, `backend/tests/identity/api/test_sso_config_integration.py`             |
| Scoped access control (partial)                     | Access comes from explicit role bindings tied to an organization or bank, and is denied by default. Enforcement is rolled out surface by surface.                                                                                                        | `backend/app/core/authorization.py`; `backend/tests/core/test_authorization.py`, `backend/tests/identity/service/test_permission_matrix_integration.py`; rollout status in [authorization_foundation.md](backend/docs/authorization_foundation.md#product-rollout-boundary)      |
| Bank-scoped machine keys                            | Integration keys act as service accounts limited to one bank's data-push routes and cannot read ordinary tenant data.                                                                                                                                    | `backend/app/identity/service/integration_keys.py`; `backend/tests/identity/api/test_integration_keys_integration.py`                                                                                                                                                            |
| Separate staff control plane                        | Staff run on their own API, which the tenant API does not mount. The staff dev token is refused on deployed environments. Google Workspace 2-step verification checks are opt-in (partial).                                                              | `backend/app/operator/`; `backend/tests/operator/test_route_isolation.py`, `backend/tests/operator/test_auth.py`, `backend/tests/operator/test_workforce_integration.py`                                                                                                         |
| Tamper-evident audit log (partial)                  | Audit events are hash-chained in the database and append-only for the runtime role. Tampering is detected and alerted when integrity monitoring and the alert topic are configured, which they are not by default.                                       | `backend/app/core/audit_integrity.py`; `backend/tests/db/test_audit_integrity_integration.py`; setup in [tamper_evident_records.md](backend/docs/tamper_evident_records.md)                                                                                                      |
| Bank-held encryption keys (partial)                 | For a bank that connects its own AWS KMS key, stored objects and covered identity fields are encrypted, bound to that bank, and refused when the key is revoked. Optional until `BANK_KEY_REQUIRED` is set; AWS KMS is the only provider.                | `backend/app/core/key_management/`, `backend/app/storage/`; `backend/tests/storage/test_bank_encryption_integration.py`, `backend/tests/core/test_bank_field_encryption_integration.py`; contract in [bank_encryption.md](backend/docs/bank_encryption.md)                       |
| Transport security                                  | Outside local and test environments, services refuse plaintext connections and unverified certificates, including to the database and storage.                                                                                                           | `backend/app/core/tls.py`; `backend/tests/core/test_tls.py`; contract in [transport_security.md](backend/docs/transport_security.md)                                                                                                                                             |
| Outbound connection guard                           | Bank-configured connection targets and SSO issuer addresses are resolved and refused when they point at loopback, private or cloud-metadata addresses.                                                                                                   | `backend/app/core/outbound.py`; `backend/tests/core/test_outbound.py`, `backend/tests/core/test_oidc_discovery_ssrf.py`                                                                                                                                                          |
| Encrypted vendor credentials                        | Market-data vendor credentials are stored as AES-256-GCM ciphertext, and reads fail without the master key.                                                                                                                                              | `backend/app/market_data/adapters/credential_manager.py`; `backend/tests/market_data/adapters/test_credential_manager.py`, `backend/tests/market_data/adapters/test_credential_manager_integration.py`                                                                           |
| Maker-checker filing and signature checks (partial) | Returns need a preparer and an approver, signing steps require re-authentication, and verification detects altered figures, signatures and chains. Tests that exercise signing-key custody run only against a real OpenBao server.                       | `backend/app/attestation/service/`; `backend/tests/attestation/service/test_attestation_verify_integration.py`, `backend/tests/attestation/service/test_attestation_stepup_throttle_integration.py`, `backend/tests/attestation/service/test_attestation_openbao_integration.py` |
| Secret scanning in CI                               | Pull requests and pushes are scanned for committed secrets with gitleaks.                                                                                                                                                                                | `.github/workflows/secret-scan.yml`, `.gitleaks.toml`                                                                                                                                                                                                                            |

## License & security

Proprietary source-available — see [LICENSE](LICENSE). Public visibility is for
evaluation and due diligence; production or competing use requires a commercial
license. Report vulnerabilities to security@aequoros.com.

**Status.** The platform is built and running against a live database, with no
bank in production and no return yet filed with the Bank of Ghana. There is no
SOC 2, ISO or other third-party certification, no regulatory approval or
accreditation of any kind, and none is claimed. "Built" means computed
server-side from ingested data behind an immutable run — it does not mean any
figure has been filed with, or accepted by, a regulator.

A database restore drill **has** been executed end to end against a throwaway
target (134 tables / 12,001,905 rows verified by row count and content digest,
tenant isolation confirmed on the restored copy, measured RTO floor ~123 s for
the database step alone). **No backup schedule is configured, so the effective
RPO is unbounded**; there is no point-in-time recovery, and the timings come from
a local cluster rather than production hardware. Current object-storage recovery
capabilities and executable evidence are governed by
[the recovery contract](backend/docs/bank_encryption.md#rotation-backups-and-recovery).
The full disclosure register lives in
`docs/audit/15_known_limitations.md`, which is **not tracked in this repository**
(the `/docs/**` rule in `.gitignore`) — ask for it directly.
