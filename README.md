# AequorOS

Treasury, ALM and regulatory-reporting infrastructure for African banks. Six
calculation modules (Liquidity · Basel Capital · IRRBB · FX · FTP · Balance-Sheet
Forecasting) run server-side on tenant-isolated, effective-dated data, each behind
an immutable, hash-pinned calculation run. Jurisdiction is data rather than code;
the return families built today are the Bank of Ghana BSD set, generated from the
official workbook templates.

## Repository layout

```
aequorOS/
├── backend/                  # ── THE PRODUCT ──
│   ├── app/                  #   FastAPI tenant API: the six engines, app/ml (LSTM),
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

Specs, runbooks and the internal audit register live in `docs/`, which is **not**
part of this repository (`.gitignore:42`). Anything cited below that you cannot
find here is held privately — ask for it.

### Deployables

| App | Production build | Domain |
| --- | --- | --- |
| `frontend` | Vercel (see `frontend/README.md`) | `aequoros.com` |
| `backend/dashboard` | `docker-compose.dashboard.yml` — Docker Compose build pack, **repo root** as build context | `bank.aequoros.com` |
| `backend` | `backend/docker-compose.prod.yml` — `risk-migrate` (one-shot `alembic upgrade head`), `risk-api` :8000, `risk-worker`, `risk-operator` :8100 | `api.aequoros.com` on `risk-api` only |
| `console` | none committed yet — no Dockerfile or compose file in this repo | intended `console.aequoros.com` |

`backend/.dockerignore` excludes `dashboard/`, so the API image never carries the UI.

## Prerequisites

- **Python 3.13** and [uv](https://docs.astral.sh/uv/). `pyproject.toml` pins
  `>=3.13,<3.14`. Create the environment once: `cd backend && uv sync`.
- **Node 24** (`.nvmrc`, `engines.node >= 24`) and **pnpm 11.2.2** — the exact
  version in `package.json` `packageManager`. `pnpm-workspace.yaml` sets
  `engineStrict: true`, so an older pnpm is refused rather than warned about.
  `nvm use && corepack enable && pnpm install`.
- **PostgreSQL** — either your managed instance or the bundled compose, which runs
  `postgres:17`.
- **Environment**: `cp backend/.env.example backend/.env` and fill in the database
  and object-storage values. Secrets live only in the untracked `.env`.

## Quick start

```bash
cd backend

# 1. Schema. DATABASE_URL comes from backend/.env (untracked; shape in .env.example).
uv run alembic upgrade head          # no-op when already at head

# 2. Tenant API on :8000 — the port backend/dashboard defaults to
#    (NEXT_PUBLIC_RISK_API_BASE_URL=http://127.0.0.1:8000/api/v1). Includes the
#    LSTM cash-flow module, which lazy-trains on the first forecast call or
#    reuses backend/artifacts/cashflow/.
uv run fastapi dev app/main.py --port 8000

# 3. Background worker — REQUIRED. It writes current_financial_facts and
#    live_metrics; without it every Treasury/ALM page reads "no computed data
#    yet". On RLS-forced Postgres it needs a BYPASSRLS role via
#    WORKER_DATABASE_URL, because it claims jobs across tenants. Locally you can
#    instead set RUN_INPROCESS_WORKER=1 (default off) to poll inside the API
#    process; in production it is always a separate container.
uv run python -m app.worker

# 4. Staff operator API on :8100 (optional; never mounted on the tenant API)
uv run uvicorn app.operator.main:app --port 8100
```

```bash
# From the repo root — the UIs.
pnpm install
pnpm --filter @aequoros/dashboard dev   # product dashboard :3001
pnpm --filter @aequoros/console   dev   # operator console  :3002 (proxies to :8100)
pnpm --filter @aequoros/frontend  dev   # marketing site    :3000
```

Offline/local infrastructure instead of a managed database:

```bash
cd backend && docker compose up -d      # Postgres :15432, MinIO :9000 (console :9001)
```

then point `DATABASE_URL` at `localhost:15432`.

**There is no seed step, and no seed route.** Data enters only through the Data
Engine — Excel/CSV upload, API push, or a read-only database extract — and a bank
is created by its first ingestion. The old `POST /banks/seed-demo` endpoint was
retired in 2026-08; `backend/tests/api/test_banks.py::test_seed_route_is_retired`
pins that the path resolves to no handler, for any role and any tenant. The
hermetic pytest suite builds its own tenants in `tests/conftest.py`; nothing
seeds a real database.

**Database-direct drivers.** The shipped image installs only the Oracle thin
driver, which is a core dependency. SQL Server/ODBC, generic JDBC and Snowflake
live behind the `db-direct` extra, which `backend/Dockerfile` does not install;
`pyodbc` and JDBC additionally need system packages (unixODBC plus a vendor ODBC
driver, and a JRE). Those backends are built and tested but not enabled in the
default deployment image — see `backend/README.md`.

## Validation

```bash
cd backend
uv run pytest                 # hermetic suite (~4,800 tests)
uv run ruff check .
uv run basedpyright
```

The hermetic default runs on SQLite with row-level security switched off, so a
green run is **not** evidence about Postgres. Boolean server defaults, String-vs-UUID
comparisons, row locks and RLS policies all behave differently there; the
Postgres-gated suites opt in explicitly via `TEST_DATABASE_URL` (each run creates
and drops its own `risk_service_test_<hex>` schema). The task inventory is in
`backend/mise.toml`.

```bash
# From the repo root — the TypeScript surfaces.
pnpm --filter @aequoros/dashboard typecheck && pnpm --filter @aequoros/dashboard lint \
  && pnpm --filter @aequoros/dashboard test && pnpm --filter @aequoros/dashboard build
pnpm --filter @aequoros/dashboard e2e             # disposable stack; package specs need S3/MinIO
pnpm --filter @aequoros/console typecheck && pnpm --filter @aequoros/console test \
  && pnpm --filter @aequoros/console build          # no lint: no ESLint config in this workspace
pnpm --filter @aequoros/frontend lint && pnpm --filter @aequoros/frontend build
pnpm --filter @aequoros/risk-service-api test

# Regenerate the client after an API change, then prove it is fresh:
mise run risk-service:openapi-client
mise run risk-service:api-fresh                     # must leave git clean
```

The CI gates live in `.github/workflows/`: `risk-service.yml` (backend plus the
MinIO-backed dashboard Playwright journeys),
`dashboard.yml` (generated client + dashboard) and `web.yml` (marketing site +
operator console). Each workflow's header comment is its own gate inventory —
read that comment rather than inferring coverage from the job names. See
`ARCHITECTURE.md` for the system map and the full validation table.

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
the database step alone). What is still unproven: **no backup schedule is
configured, so the effective RPO is unbounded**; object storage is inventoried
but has never been restored, so filed-artifact recoverability is untested; there
is no point-in-time recovery, and the timings come from a local cluster rather
than production hardware. The full disclosure register lives in
`docs/audit/15_known_limitations.md`, which is **not tracked in this repository**
(`.gitignore:42`) — ask for it directly.
