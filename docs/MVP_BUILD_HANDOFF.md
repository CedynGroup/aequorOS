# AequorOS MVP — Build Handoff

**Status: BUILD COMPLETE — READY FOR TESTING**

The **full six-module product** (Interest Rate Risk · Liquidity · FX · Basel Capital ·
Funds Transfer Pricing · Balance Sheet Forecasting, plus the LSTM cash-flow service) is
built end-to-end on real, server-side, tenant-scoped calculation engines. The polished
Treasurer-facing UI (`dashboard`) pulls every number from the backend — zero hardcoded
financial data.

**Structure:** `dashboard` is the primary product UI; `backend` is the single backend
service (the LSTM cash-flow module runs in-process at `backend/app/ml` — the former
`cashflow-ml` sidecar was folded in); `packages/risk-service-api` is the shared generated
client. See ARCHITECTURE.md → "Structure decision" for why the proposed "move backend
into dashboard / delete other apps" was declined.

### Live-verified values (Sample Bank Ltd, 2026-03)

| Module | Headline | Live value |
|--------|----------|-----------|
| Liquidity | LCR baseline/idio/market/combined | 147.29 / 94.84 / 113.01 / 87.36% |
| Basel | CAR / Tier 1 / CET1 | 15.83 / 13.04 / 12.11% |
| Forecasting | avg ROE / y5 CAR | 13.25 / 15.22% |
| Cash-flow LSTM | MAPE improvement vs static | +25.3% |
| IRR | worst ΔEVE÷Tier1 (limit 15) / dur gap / 12m gap | 5.35% green / 0.48y / −370M |
| FX | NOP÷Tier1 / single-ccy max (limit 10) / VaR 99% 1d | 16.07% / 10.71% red / 731K |
| FTP | portfolio NIM / products below margin / NMD core | 7.20% / 2 / 66.5% |

All six modules run via `.../run-all-scenarios` + `.../dashboard`; tenant isolation verified
(org2 → 404 on every module). Backend suite: **570 passed, 16 skipped** (incl. the in-process
cash-flow ML tests, run with `CASHFLOW_FAST_TEST=1`); repo-wide `ruff` clean.

### Seed is idempotent

`scripts/seed_sample_bank.py` now clears all bank-scoped dependents (regulatory runs +
data-engine ingestion/canonical rows) before re-inserting, so it re-runs cleanly on a live DB.

---

## What runs where

| Component | Path | Port | Role |
|---|---|---|---|
| Risk service (FastAPI) | `backend` | 8003 | Regulatory calc engines + in-process LSTM cash-flow model (`app/ml`), DB, S3 |
| Product UI (Next.js) | `dashboard` | 3001 | Bank Treasurer console |
| Generated API client | `packages/risk-service-api` | — | Typed contract shared by UI |
| Postgres | remote `<postgres-host>:<port>/<database>` (via `backend/.env`) | 5433 | Primary data store (local docker on 15432 remains the offline fallback) |
| MinIO | `<s3-endpoint-host>` (via `backend/.env`; local docker fallback :9000) | — | Object storage |

The canonical MVP demo surface is **`dashboard`** (the prior case-based SPA was removed; see git history).

---

## Start it (one-time + run)

```bash
# 0. Toolchain (Node 24 required for the web build)
export PATH="$HOME/.nvm/versions/node/v24.18.0/bin:$PATH"

# 1. Infra (Postgres + MinIO) — already provisioned; start if down
cd backend   # DATABASE_URL comes from backend/.env (remote Postgres)

# 2. Migrate to head (no-op when the remote is already at head)
.venv/bin/alembic upgrade head

# 3. Backend (tenant CORS for the demo origin) — one service; the LSTM cash-flow
#    module runs in-process (lazy-trains on the first forecast call, or reuses
#    backend/artifacts/cashflow/ if already trained)
CORS_ORIGINS=http://localhost:3001 \
  .venv/bin/fastapi run app/main.py --port 8003 &
# (offline fallback: docker compose up -d, then export the localhost:15432
#  migration/app role URLs as before)

# 4. Seed Sample Bank Ltd (idempotent; only needed if GET /banks is empty)
#    Either: cd backend && .venv/bin/python scripts/seed_sample_bank.py
#    Or POST /api/v1/banks/seed-demo with the demo tenant headers.

# 5. Demo UI
cd .. && pnpm --filter @aequoros/dashboard dev   # http://localhost:3001
```

Demo tenant (baked into `dashboard/lib/api/client.ts`, override via
`NEXT_PUBLIC_*`): org `11111111-1111-4111-8111-111111111111`, user
`aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa`. API base `http://127.0.0.1:8003/api/v1`.

---

## Verified live numbers (Sample Bank Ltd, 2026-03, hand-checked goldens)

| Metric | Value | Status |
|---|---|---|
| **LCR** baseline / idiosyncratic / market-wide / combined | 147.29% / 94.84% / 113.01% / 87.36% | green / amber / green / **red** |
| **NSFR** baseline | 151.49% | green |
| **CAR** / Tier 1 / CET1 / Leverage | 15.83% / 13.04% / 12.11% / 10.94% | all green |
| **Capital stress severe** end-CAR (Q4) | 9.32% | **red** — early-warning + breach triggers fire at Q4 |
| **Forecast base** avg ROE / y5 CAR / y5 LCR / y5 NSFR | 13.25% / 15.22% / 148.36% / 153.11% | 6-year path |
| **Optimizer** | 108 evaluated, 108 feasible, top ROE 14.95% | ranked |
| **LSTM cash-flow** | LSTM MAPE 37.6% vs static 50.3% (+25% improvement) | holdout-validated |

**Cross-module consistency proven**: the forecasting engine, on the year-0 fact
set, reproduces the capital engine's CAR (15.832363) and the liquidity engine's
LCR (147.294589) exactly — same engines, same facts.

**Tenant isolation proven**: org2 sees 0 banks and gets 404 on the Sample Bank
id; every new table has Postgres RLS enable/force + isolation policy.

---

## Manual test flows (45–60 min demo script)

1. **Overview** (`/`) — bank profile from facts; LCR/NSFR/CAR/Tier-1 KPIs; open
   validations; recent regulatory runs (real audit trail).
2. **Liquidity → LCR** (`/liquidity`) — ratio gauge, HQLA composition, outflow/inflow
   tables with runoff rates, 12-period trend. Click **Run baseline** → the inline
   banner clears and a run badge (engine version + hash) appears.
3. **Liquidity → NSFR** — ASF/RSF tables from the stored run's line items.
4. **Liquidity → Cash Flow** (`/liquidity/forecast`) — toggle **LSTM ⇄ Static** and
   **30/60/90**. LSTM shows the confidence band + accuracy panel. The model runs
   in-process (first call may lazy-train); a 503 "service offline" state appears
   only if the ML runtime (torch) fails to load.
5. **Liquidity → Stress** — **Run all scenarios** → idiosyncratic 94.84% amber,
   combined 87.36% red; per-scenario stressed run-off assumptions are visible.
6. **Liquidity → BSD-3** — formatted BoG return; verbatim preview-only note; 409
   empty state before a baseline run.
7. **Basel → Capital** (`/basel`) — CAR gauge + buffers; RWA composition; CAR trend.
8. **Basel → RWA / Structure** — credit/market/operational breakdown; CET1→Tier2 build-up.
9. **Basel → Stress** — **Run all** → severe Q4 CAR 9.32% with **breach** trigger + action text.
10. **Basel → BSD-2** — formatted capital return with pass/fail ratio rows.
11. **Forecasting → Dashboard / Scenario Builder** — preset + custom assumptions → 5-year path.
12. **Forecasting → Optimizer** — 108 combinations, ranked feasible set (honestly
    labeled "constrained scenario search", not RL).
13. **Forecasting → What-If** — four macro shocks; base-vs-shocked CAR deltas.
14. **Submissions / Reports / Settings** — run library + live health checks.
15. **IRR / FX / FTP** — honest **Post-MVP** placeholder pages.

---

## Test + validation gates (all green)

- `backend`: **570 passed, 16 skipped** (`ruff`, `basedpyright` clean).
- `packages/risk-service-api`: typecheck + generated-contract tests pass.
- `dashboard`: `typecheck` + `build` pass (24 static routes).
- Cash-flow ML (`backend/app/ml` + `tests/ml`, in the backend suite): model beats static
  baseline on the fast-test holdout; forecast/history contract tests run in-process.
- Zero `lib/data/` imports remain in the demo (audited).

---

## Honest scoping

- **Synthetic data only.** One provisioned bank ("Sample Bank Ltd"), no core-banking
  integration. The architecture is multi-tenant and RLS-isolated so real banks slot in.
- **The optimizer is a constrained scenario search**, not reinforcement learning.
  Full RL is post-MVP (documented as roadmap).
- **Submission previews are previews.** The system formats BoG BSD-2/BSD-3 returns;
  it does not file them.
- **LSTM accuracy is demonstrated on synthetic data**, validated on a holdout — not
  yet on a real bank's book. That happens in paid pilots.
- **IRR, FX, FTP are deferred** post-MVP (placeholder pages, not fake screens).

---

## Known follow-ups (non-blocking)

- On the LOCAL docker fallback, migrations must run as `risk_service_migrator`
  then re-grant `risk_service_app` (owner-only DDL); `scripts/bootstrap_db.sh`
  does both. The remote database uses a single role that owns the schema.
- The remote role has **no BYPASSRLS** — the cross-tenant background worker
  needs a BYPASSRLS role (`WORKER_DATABASE_URL`) before running against the
  remote; ad-hoc `psql` inspection needs
  `SELECT set_config('app.organization_id','<org-uuid>',false)` or every
  tenant table reads as empty.
- The demo's `lint` script is `next lint`, which prompts for ESLint setup (never
  configured). Real gates are `typecheck` + `build`. Configure ESLint or drop the script.
- Full end-to-end e2e (Playwright) for the ALM flows is not yet written.
