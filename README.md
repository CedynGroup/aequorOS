# AequorOS

Treasury and ALM infrastructure for African banks. Six regulatory modules
(IRR · Liquidity · FX · Basel Capital · FTP · Balance Sheet Forecasting) computed
server-side on tenant-isolated, effective-dated data for a Bank of Ghana licensee.

## Repository layout

```
aequorOS/
├── backend/                  # ── THE PRODUCT ──
│   ├── app/                  #   FastAPI service: all engines (IRR, Liquidity, FX,
│   │                         #   Basel, FTP, Forecasting), app/ml LSTM, Data Engine,
│   │                         #   Postgres + RLS tenancy, BoG submissions, seed
│   ├── dashboard/            #   the product UI (Next.js) → app.aequoros.com
│   ├── alembic/ tests/ scripts/ docker-compose.yml
├── frontend/                 # ── MARKETING SITE (Next.js) ── aequoros.com pages
├── packages/
│   └── risk-service-api/     # generated TypeScript client (API ⇄ dashboard contract)
├── docs/                     # specs, build handoff, working notes
└── ARCHITECTURE.md           # system map + conventions (start here)
```

Three deployables: `frontend` → Vercel (aequoros.com) · `backend/dashboard` → Vercel
(app.aequoros.com, Root Directory `backend/dashboard`) · `backend` → container host
(API incl. ML + Postgres + MinIO; the Docker image excludes `dashboard/`).

## Prerequisites

- **Python 3.13** (backend). Create the venv once: `cd backend && uv sync`
  (or `python3.13 -m venv .venv && .venv/bin/pip install -e .`).
- **Node 24 + pnpm 9+** (dashboard/frontend): `nvm use && corepack enable && pnpm install`.
- **PostgreSQL 15+** — either your managed instance or the bundled
  `docker compose up -d` (Postgres :15432 + MinIO :9000).
- **Environment**: `cp backend/.env.example backend/.env` and fill in your
  database and object-storage values. Secrets live only in the untracked `.env`.

## Quick start

```bash
# database: the shared remote Postgres — DATABASE_URL comes from backend/.env
# (untracked; shape in backend/.env.example). Schema is kept at alembic head:
cd backend && .venv/bin/alembic upgrade head   # no-op when already at head
.venv/bin/python scripts/seed_sample_bank.py   # idempotent demo seed

# backend API :8003 (one service — includes the LSTM cash-flow module, which
# lazy-trains on the first forecast call or reuses artifacts/cashflow/)
CORS_ORIGINS=http://localhost:3001 .venv/bin/fastapi dev app/main.py --port 8003

# offline/local alternative: docker compose up -d (Postgres :15432 + MinIO :9000)
# and export DATABASE_URL pointing at localhost:15432 instead.

# product dashboard :3001
pnpm install && pnpm --filter @aequoros/dashboard dev
```

## Validation

- Backend: `cd backend && CASHFLOW_FAST_TEST=1 .venv/bin/python -m pytest` (or `mise run risk-service:check`)
- Dashboard: `pnpm --filter @aequoros/dashboard typecheck && pnpm --filter @aequoros/dashboard build`
- Client regen after API changes: `mise run risk-service:openapi-client`

See `docs/MVP_BUILD_HANDOFF.md` for the full run/verify guide and module demo flows.


## License & security

Proprietary source-available — see [LICENSE](LICENSE). Public visibility is for
evaluation and due diligence; production or competing use requires a commercial
license. Report vulnerabilities per [SECURITY.md](SECURITY.md).
