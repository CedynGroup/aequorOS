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

## Note on removed code

`apps/aequoros-web` (the earlier case-based risk console) was removed 2026-07-15
in the repo restructure; its case-review backend endpoints remain. Recover the UI
from git history if ever needed: `git log --oneline -- apps/aequoros-web`.
