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
(cd backend && docker compose up -d risk-postgres risk-minio risk-minio-init)
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

# Steps 2-4 each run in the foreground. Start each one in its own terminal,
# from backend/.
#
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

Check the backend with `GET /api/health/live` (the process is up) and
`GET /api/health/ready`. In the readiness response, read `checks.database.status`,
`checks.storage.status` and `checks.worker.status`, not only the top-level `status`.
With `APP_ENV=local` and `DATABASE_URL` unset, the database and worker checks are
`skipped` and the top-level `status` can still be `ok`, so confirm
`checks.database.status` is `ok`. A worker check of `ok` only means no queued core job
is more than `WORKER_STALE_JOB_SECONDS` (default 900 seconds, 15 minutes) past its run
time. It does not prove the worker process is running, or that no job is running late
by less than that. A longer delay shows as `degraded`. Check the worker terminal
separately. The dashboard answers `GET /api/health/live` on its own port.

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
- **Empty Treasury/ALM pages.** Check that the bank has loaded financial data
  through the Data Engine and that the worker is running to compute it.
- **`/api/health/ready` returns 503.** It needs a reachable database and object
  storage (with `DATABASE_URL` unset in local mode, the database check is skipped
  instead). Core jobs more than `WORKER_STALE_JOB_SECONDS` (default 900 seconds)
  overdue, often a stopped worker, show as `degraded` in the response body instead. `/api/health/live` only proves the process is up.
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

# After an API change, regenerate the client, then commit the regenerated
# backend/openapi-schema.json and packages/risk-service-api changes. The freshness
# check reads their git status, so it fails while they are modified, staged or
# untracked. It does not clean them, and it is also the pre-push hook.
mise run risk-service:openapi-client
mise run risk-service:api-fresh
```

CI coverage and the full gate list: [Architecture §8](ARCHITECTURE.md#8-validation-commands).

## License & security

Proprietary source-available — see [LICENSE](LICENSE). Public visibility is for
evaluation and due diligence; production or competing use requires a commercial
license. Report vulnerabilities to security@aequoros.com.

Protections the code supports today, beyond the data isolation described above (this
is not an audit; see Status below):

- **Fine-grained access control.** Access comes from explicit role bindings scoped to
  an organization or bank, and anything not granted is denied. Enforcement is rolled
  out surface by surface.
- **Encrypted connections.** Outside local and test environments, services require
  TLS and refuse plaintext connections and unverified certificates, including to the
  database.
- **Sign-in protection.** Passwords are stored as Argon2id hashes, repeated failed
  attempts lock the account, and organizations can sign in through their own OIDC
  identity provider.
- **Bank-held encryption keys.** A bank can connect its own AWS KMS key to encrypt its
  stored files and sensitive fields. This is optional.

**Status.** The platform is built and running against a live database, with no
bank in production and no return yet filed with the Bank of Ghana. There is no
SOC 2, ISO or other third-party certification, no regulatory approval or
accreditation of any kind, and none is claimed. "Built" means computed
server-side from ingested data behind an immutable run — it does not mean any
figure has been filed with, or accepted by, a regulator.
