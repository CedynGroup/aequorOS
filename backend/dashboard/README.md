# AequorOS Demo Prototype

An interactive click-through prototype of the AequorOS Treasury and ALM platform,
built per the AequorOS Figma Design Brief. Used for investor presentations and
bank treasurer validation interviews.

**Target deployment:** `demo.aequoros.com`

---

## Scope — what's built

All four phases of the original plan are now in. **35 routes**, all
statically pre-rendered, all under 215 KB First Load JS.

### Login + Overview
- `/login` — branded sign-in screen with Equilibrium Mark, "Treasury Reimagined"
- `/` — Overview Dashboard: bank profile bar, 4 cross-module KPIs (CAR, LCR,
  NSFR, FX NOP), AI insights, BoG filing deadlines, activity feed

### Module 01 — Interest Rate Risk (4 screens)
- `/irr` — Dashboard: NII at risk, EVE sensitivity, asset duration, gap analysis chart
- `/irr/scenarios` — 5 Basel/BoG IRRBB shocks with NII + EVE impact tables and bar charts
- `/irr/positions` — IRS portfolio with MTM, hedge effectiveness, all 5 contracts
- `/irr/hedging` — 3 Deep RL hedge recommendations with rationale, expected impact, confidence

### Module 02 — Liquidity Risk (5 screens, priority module)
- `/liquidity` — LCR ratio gauge with threshold + buffer, 12-month trend, HQLA stack,
  full outflow/inflow tables with Basel runoff weights, watch-item callout
- `/liquidity/nsfr` — NSFR gauge, ASF/RSF tables with Basel III §50/§52 factors
- `/liquidity/forecast` — interactive 30/60/90-day toggle, LSTM forecast with 95% CI,
  static behavioral overlay toggle, MAPE/RMSE comparison, AI recommendation
- `/liquidity/stress` — 3 Basel III/BoG ILAAP scenarios with breach analysis at Day +22
- `/liquidity/submission` — BoG BSD/2/2024 LCR Return formatted as a real regulatory
  document, validation banner, certification line, audit trail

### Module 03 — FX Risk (4 screens)
- `/fx` — exposure gauge (4.78% of capital vs 5% BoG limit), VaR/ES, 5 currency
  position cards (USD, EUR, GBP, NGN, XOF), full breakdown table
- `/fx/scenarios` — 3 cedi depreciation scenarios (5%, 10%, 20%) with P&L impact
- `/fx/prediction` — ML ensemble (XGBoost+LSTM) 90d forecast vs forward implied,
  with confidence band; model accuracy comparison; AI restructure recommendation
- `/fx/hedging` — full hedge book table, expiring-in-30-days view, hedge ratio metrics

### Module 04 — Basel Capital (5 screens)
- `/basel` — CAR ratio gauge (14.20%), Tier 1/Tier 2 cards, 12-month trend, RWA donut,
  buffer status grid (BoG min, conservation, countercyclical, D-SIB)
- `/basel/rwa` — RWA donut + detailed breakdown by sub-category with capital requirements
- `/basel/structure` — CET1 / AT1 / Tier 2 build-ups with regulatory deductions
- `/basel/stress` — 3 ICAAP scenarios with 12-month CAR projections, capital action plan
- `/basel/submissions` — BoG, CBN, SARB, CBK templates with multi-jurisdiction reference

### Module 05 — Funds Transfer Pricing (4 screens)
- `/ftp` — yield curve chart (4 curves: BoG/market, deposit, lending, FTP), full curve table
- `/ftp/branches` — 18-branch P&L ranking with FTP-adjusted NIM, regional breakdown
- `/ftp/products` — asset and liability product profitability with match-funded spreads
- `/ftp/rates` — active FTP rate table with bps changes vs prior, 12-month rate history

### Module 06 — Balance Sheet Forecasting (4 screens)
- `/forecasting` — 12/24/36-month asset projection chart, strategic assumption variance,
  capital adequacy projection
- `/forecasting/scenario` — **interactive scenario builder** with horizon toggle and live
  asset growth + NIM multiplier sliders that update the projection chart
- `/forecasting/optimizer` — 4 Reinforcement Learning recommendations with rationale,
  expected impact, confidence; SR 11-7 model governance footer
- `/forecasting/whatif` — 6 macro shock scenarios (rate shock, cedi -20%, NPL spike,
  MPR cut, BoG severe combined) with full balance sheet impact

### Cross-module
- `/reports` — 8 cross-module reports (ALCO, ICAAP, board pack, capital plan, RRP)
- `/submissions` — BoG filing calendar with multi-regulator support
- `/settings` — bank profile, integrations status, governance, users & roles

### Polish (Phase 4)
- **Command palette (⌘K / Ctrl+K)** — opens from anywhere; arrow-key navigable;
  searches all 28 module pages; Enter to navigate; Esc to close
- **Notifications drawer** — 5 contextual alerts (filing review, LCR run, AI rec,
  expiring FX hedge, BoG ack); slide-in from right; click to dismiss
- **Mobile responsive** — sidebar collapses to drawer below 1024px; mobile menu
  button + search button in header
- **Loading skeletons** — `SkeletonLine`, `SkeletonCard`, `SkeletonTable`,
  `SkeletonChart` ready for any async loading states
- **Empty states** — reusable `EmptyState` component for zero-data views

---

## Stack

- Next.js 14 (App Router) · TypeScript · Tailwind CSS
- Recharts — all visualizations (LSTM forecast, gap analysis, donut, yield curve, FX rate, etc.)
- Inter (UI) + IBM Plex Mono (numerical data) via `next/font/google`
- All 35 routes statically pre-rendered

## Run locally

```bash
npm install
npm run dev
# http://localhost:3001
```

(Marketing site runs on 3000; demo runs on 3001 to allow side-by-side.)

## Deploy to demo.aequoros.com

Treat as a separate Vercel project from the marketing site.

1. Push the `dashboard/` directory to its own repo (or import the monorepo
   and set the root directory to `dashboard`).
2. In Vercel, **New Project** → import the repo → set Framework to Next.js,
   Root Directory `dashboard`.
3. Build command: `npm run build`. Output: default. Install: `npm install`.
4. After first deploy: **Project Settings → Domains → Add `demo.aequoros.com`**.
5. At your DNS registrar, add the CNAME record Vercel shows you (typically
   `cname.vercel-dns.com`).
6. Wait for verification (minutes), then the site is live.

The site is `noindex`'d by default (see `app/layout.tsx`) so it stays out of
search engines until you explicitly remove the robots block.

## Project structure

```
dashboard/
├── app/
│   ├── layout.tsx                  # Root: fonts, metadata
│   ├── globals.css                 # Brief-aligned base styles
│   ├── icon.svg                    # Equilibrium Mark favicon
│   ├── login/page.tsx
│   └── (app)/                      # Authenticated shell route group
│       ├── layout.tsx              # AppShell (sidebar + header)
│       ├── page.tsx                # Overview Dashboard
│       ├── liquidity/              # Module 02 (5 screens)
│       ├── irr/                    # Module 01 (4 screens)
│       ├── fx/                     # Module 03 (4 screens)
│       ├── basel/                  # Module 04 (5 screens)
│       ├── ftp/                    # Module 05 (4 screens)
│       ├── forecasting/            # Module 06 (4 screens)
│       ├── reports/page.tsx
│       ├── submissions/page.tsx
│       └── settings/page.tsx
├── components/
│   ├── shell/
│   │   ├── AppShell.tsx            # Mobile-aware shell wrapper
│   │   ├── Sidebar.tsx             # Persistent left navigation
│   │   ├── Header.tsx              # Top bar (search, ⌘K, as-of, user)
│   │   ├── Logo.tsx                # Equilibrium Mark
│   │   ├── ModuleTabs.tsx          # Per-module sub-navigation
│   │   ├── CommandPalette.tsx      # ⌘K omni-search
│   │   └── NotificationDrawer.tsx  # Right-side alerts panel
│   ├── ui/
│   │   ├── KPICard.tsx
│   │   ├── RatioGauge.tsx
│   │   ├── StatusPill.tsx
│   │   ├── DataTable.tsx
│   │   ├── Card.tsx
│   │   ├── PageHeader.tsx
│   │   ├── RecommendationCard.tsx
│   │   ├── Sparkline.tsx
│   │   ├── Skeleton.tsx
│   │   ├── EmptyState.tsx
│   │   └── ModulePlaceholder.tsx
│   └── charts/
│       ├── RatioHistoryChart.tsx
│       ├── HQLAStackChart.tsx
│       ├── CashFlowForecastChart.tsx
│       ├── GapAnalysisChart.tsx
│       ├── DonutChart.tsx
│       ├── ScenarioImpactChart.tsx
│       ├── FxRateChart.tsx
│       ├── CapitalProjectionChart.tsx
│       ├── YieldCurveChart.tsx
│       ├── BalanceSheetProjectionChart.tsx
│       └── StackedBar.tsx
├── lib/
│   ├── format.ts                   # GHS / pct / signed formatters
│   └── data/
│       ├── bank.ts                 # Sample Bank Limited profile
│       ├── overview.ts             # Cross-module dashboard data
│       ├── liquidity.ts            # LCR, NSFR, LSTM, stress, submission
│       ├── irr.ts                  # Gap, scenarios, IRS portfolio, hedging recs
│       ├── fx.ts                   # Positions, scenarios, ML forecast, hedges
│       ├── basel.ts                # Capital, RWA, structure, stress, submissions
│       ├── ftp.ts                  # Yield curve, branches, products, rates
│       └── forecasting.ts          # Projection, RL recs, what-if scenarios
├── tailwind.config.ts              # Brief palette + typography scale
├── package.json
└── tsconfig.json
```

## Design adherence

This prototype follows the AequorOS Figma Design Brief exactly:

- **Palette**: `#0A2540` Primary Navy, `#1A4D5C` Deep Teal, `#2D7FF9` Action
  Blue, `#0E8A4F` / `#C97C00` / `#B3261E` regulatory traffic lights, `#5A6776`
  Neutral Slate, `#F5F7FA` Light Surface, `#D0D7DE` Border Gray
- **Typography**: Inter for UI, IBM Plex Mono for all numerical data; tabular
  numerals enabled globally
- **Information density**: tables hold 20+ rows comfortably; numerical cells
  right-aligned and tabular-aligned
- **Quiet aesthetics**: no gradients; only subtle elevation; minimal animation
- **Color is functional**: green/amber/red mapped to regulatory thresholds, not
  branding
- **Equilibrium Mark logo**: triangle resting on a horizontal line — the brief's
  spec — used in sidebar, login, and favicon

## Synthetic data — Sample Bank Limited

Mid-tier Ghanaian universal bank, BoG-licensed. All figures anchored to the brief:

- GHS 2.4B total assets · GHS 1.9B deposits · GHS 1.4B loans
- LCR 142.0% · NSFR 118.0% · CAR 14.20%
- 18 branches · 85,000 customers · founded 2005
- HQLA: Level 1 65% / Level 2A 25% / Level 2B 10%
- 30-day net cash outflow: GHS 180M
- IRS portfolio: 5 contracts (4 GHS + 1 USD), GHS 211M total notional
- FX exposure: 5 currencies, USD long 8M, EUR/GBP short, NOP 4.78% of capital

## Demo flow (suggested for investor / bank exec)

The full demo script is below. Each step takes 30-60 seconds.

1. **`/login`** — sets the visual tone. "Treasury Reimagined" tagline, BoG
   licensee context.
2. **`/`** — Overview Dashboard. Cross-module CRO/CFO view. AI insights show
   the platform isn't just dashboards.
3. **Press ⌘K** — show the command palette. Search for "stress" — instantly find
   3 stress test screens across modules.
4. **`/liquidity`** — the headline screen. LCR 142% gauge, full BoG outflow
   table, watch-item callout shows institutional thinking.
5. **`/liquidity/forecast`** — toggle 30 → 60 → 90 days. Toggle static overlay
   off and on to show the LSTM advantage. AI recommendation card.
6. **`/liquidity/stress`** — show the combined-stress breach at Day +22. Real
   risk management.
7. **`/liquidity/submission`** — show the BoG BSD/2/2024 LCR Return ready to
   file. This is what banks actually need.
8. **`/irr`** — gap analysis with positive/negative bars. Cumulative gap line.
9. **`/irr/hedging`** — 3 Deep RL recommendations. Show that AI integrates with
   workflow (accept / modify / reject).
10. **`/fx`** — 4.78% NOP gauge approaching 5% BoG limit. Currency position
    cards.
11. **`/fx/prediction`** — 180 days of GHS/USD with 90-day forecast and forward
    implied overlay.
12. **`/basel`** — CAR gauge, RWA donut, buffer status grid.
13. **`/basel/stress`** — three ICAAP projections. Severe scenario shows breach
    pathway and capital action plan.
14. **`/ftp`** — interactive yield curve. Show how FTP rates derive from
    market rates.
15. **`/ftp/branches`** — 18-branch ranking. Tier-3 branches in red.
16. **`/forecasting`** — 36-month projection with composition over time.
17. **`/forecasting/scenario`** — drag the asset growth slider. The chart
    updates. This is interactive scenario planning.
18. **`/forecasting/optimizer`** — 4 Reinforcement Learning recommendations.
    SR 11-7 governance footer.
19. **Bell icon (top right)** — show notifications drawer.

Total time: 12-15 minutes for a comprehensive walkthrough.
