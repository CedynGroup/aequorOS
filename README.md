# AequorOS

Treasury and ALM infrastructure for African banks. Six regulatory modules
(IRR · Liquidity · FX · Basel Capital · FTP · Balance Sheet Forecasting) computed
server-side on tenant-isolated, effective-dated data for a Bank of Ghana licensee.

## Repository layout

```
aequorOS/
├── backend/                  # ── THE BACKEND (Python, one FastAPI service) ──
│                             #   all calculation engines incl. the in-process LSTM
│                             #   cash-flow module (app/ml, PyTorch), Postgres,
│                             #   RLS tenancy, regulatory runs, BoG submissions, seed
├── dashboard/                # ── THE PRODUCT UI (Next.js) ── Treasurer console,
│                             #   all values live from the backend API
├── frontend/                 # ── MARKETING SITE (Next.js) ── aequoros.com pages
├── packages/
│   └── risk-service-api/     # generated TypeScript client (backend ⇄ dashboard contract)
├── docs/                     # specs, build handoff, working notes
└── ARCHITECTURE.md           # system map + conventions (start here)
```

Three deployables: `frontend` → Vercel (aequoros.com) · `dashboard` → Vercel
(app/demo subdomain) · `backend` → container host (API incl. ML + Postgres + MinIO).

## Quick start

```bash
# infra (Postgres :15432 + MinIO :9000)
cd backend && docker compose up -d

# migrate + seed Sample Bank Ltd (idempotent)
DATABASE_URL=postgresql+psycopg://risk_service_migrator:risk_service_migrator@localhost:15432/risk_service \
  .venv/bin/alembic upgrade head && .venv/bin/python scripts/seed_sample_bank.py

# backend API :8003 (one service — includes the LSTM cash-flow module, which
# lazy-trains on the first forecast call or reuses artifacts/cashflow/)
DATABASE_URL=postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service \
  CORS_ORIGINS=http://localhost:3001 .venv/bin/fastapi dev app/main.py --port 8003

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
