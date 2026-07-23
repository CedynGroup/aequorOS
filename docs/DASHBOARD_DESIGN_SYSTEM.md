# AequorOS Treasury Workbench — Design System & Information Architecture

The dashboard redesign turned the admin panel into a dark-first, token-driven treasury
workbench competitive with MORS 6.x / Finastra ALM IQ. Everything below reflects the shipped
state under `backend/dashboard/`.

## 1. Information architecture

```
TOP BAR   institution + license · ⌘K command palette · as-of period · freshness pill · alerts · theme · user
────────────────────────────────────────────────────────────────────
COMMAND      Command Center /            role lenses · breach banner · module pulse · trends · ops feed
             Risk & Limits /risk        cross-module LimitBar wall (payload thresholds only) + validations
             Alerts /alerts             live findings stream, group by module/severity
MARKETS      Markets /markets           curve board · FX board · ratings — attribution + freshness on every value
             Positions /positions       canonical deal blotter, lineage drill, scale guards (418k book)
MODULES      IRRBB /irr                 Overview · EVE & NII · Gap Analysis · Scenarios · Limits
             Liquidity /liquidity       Cockpit · Buffer · NSFR · Cash-flow · Stress · CFP · Submission
             FX /fx                     Exposure · VaR & Stress · Hedge Book · Limits · Forwards
             Basel /basel               Overview · RWA · Structure · Stress · Planning · Submissions
             FTP /ftp                   Curve · Products · Business Lines · Rules · Ex-ante/Ex-post
             Forecasting /forecasting   Balance Sheet · NII · Scenarios · What-if · Optimizer · Assumptions
             Behavioral /behavioral     Overview · NMD Duration · Prepayment · Deposit Stability
DATA         Data Engine /data-engine   Overview · Excel/CSV · API Push · Market Data · T24 · Adapters · Canonical
GOVERNANCE   Reports /reports (+ /reports/board-pack) · Submissions /submissions · Settings /settings
PERSONAL     Avatar menu → Profile & preferences /settings/profile
```

Placement rules: market data **management** (connect/rotate/upload) lives in the Data Engine;
market data **consumption** (curves, rates, ratings analysis) lives in Markets.

## 2. Token system (`app/globals.css` + `tailwind.config.ts`)

Semantic CSS variables under `:root[data-theme='dark']` (default) and `[data-theme='light']`,
stored as RGB channel triplets so Tailwind opacity modifiers work (`text-navy/85`).
Theme boot is FOUC-safe: an inline pre-paint script reads the light/dark/system
preference from `localStorage['aeq-theme']` and resolves system against the OS.
After authentication, `/auth/me` is authoritative; changes are persisted to the
user profile for cross-browser consistency and mirrored locally for the next
pre-paint boot. System mode follows OS theme changes live.

| Token (Tailwind name) | Role | Dark | Light |
|---|---|---|---|
| `bg-base` | page background | `#0A0F1A` | `#FAFBFC` |
| `bg-surface` / `surface-raised` / `surface-hover` | cards, hovers | `#101827 / #17202F / #1C2737` | `#F5F7FA / #FFF / #F5F7FA` |
| `text-navy` | headings | `#EDF2F9` | `#0A2540` |
| `text-ink` / `text-slate` / `text-slate-light` | body / muted / faint | `#C6D0DE / #8494A9 / #5C6B80` | `#33415C / #5A6776 / #7A8693` |
| `border-border` / `border-light` | hairlines | `#2E3E58 / #223047` | `#D0D7DE / #E4E8EC` |
| `action` (+hover/soft) | accent / links / CTAs | `#4D9FFF` | `#2D7FF9` |
| `success / warning / critical` (+soft) | risk semantics | `#35C28D / #F5A623 / #F26D6D` | `#0E8A4F / #C97C00 / #B3261E` |
| `bg-nav` | always-dark rail/hero | `#070C15` | `#0A2540` |
| `--chart-1..6` | data-viz categorical | blue/teal/violet/amber/rose/cyan | deepened variants |

Utilities: `.card` (10px radius; shadow only in light), `.btn-primary`, `.tnum`
(tabular numerals — global on tables), `text-kpi`/`text-kpi-lg` (28/36px numerics),
themed scrollbars, focus-visible ring, print base (light forced, chrome hidden).

## 3. Component kit (`components/ui/`)

- **KpiStat** — dense KPI: label, tnum value, unit, DeltaBadge, sparkline slot, status edge-glow
- **LimitBar** — bullet limit gauge; zoned track, amber/red ticks, headroom readout,
  `direction: above|below` (floors like LCR vs ceilings like NOP)
- **ChartFrame / SectionCard** — standard card shells with title/actions, loading skeleton,
  footer meta (`computedAt` + RunBadge provenance)
- **DataTable** — sticky header, compact density, right-aligned numeric columns, row drill
- **DeltaBadge, StatusPill, RatioGauge, Sparkline, RunBadge, SubTabs, PageHeader,
  EmptyState, Skeleton, QueryBoundary** — token-native
- **CommandPalette** (⌘K) — zero-dep, full route registry with keywords
- **GuidedTour** (`components/tour/`) — zero-dep spotlight tour, 8 steps, `?tour=1` or
  first-visit pill
- **ProfileProvider** (`components/profile/`) — cached `GET /auth/me`, serialized profile
  updates, and immediate header/profile freshness independent of JWT claim rotation
- **Profile & preferences** (`/settings/profile`) — personal details, generated initials
  avatar with identity-stable color, and server-persisted light/dark/system preference
- `lib/chartTheme.ts` — recharts theme: `CHART_SERIES`, grid/status colors, axis/tooltip props

## 4. Non-negotiables encoded in the pages

1. **Real data only** — every number traces to a dashboard/live/run payload; client-side
   derivations are labeled; anything else carries an amber `Illustrative` badge
   (CFP playbook, Basel planning what-if, FTP ex-post column).
2. **Provenance everywhere** — SectionCard footers with computed-at + immutable-run badges;
   Markets cards carry source-system + staleness chips; positions drill to lineage + batch.
3. **Limits are payload-driven** — the Risk wall and module Limits pages render only
   thresholds present in API payloads. Status-only metrics are listed, not gauged.
4. **Both themes** — no raw hex in page code; module charts read `chartTheme` vars.
5. **Scale honesty** — the 418k-position blotter sizes the book first and refuses unbounded
   reads with an explanatory panel (server pagination is a tracked follow-up).

## 5. Phase 2 (deliberately deferred)

- True role-based access control (role lenses are client-side view permutations)
- PDF/Excel export engine (print-optimized board pack ships; `window.print()` → PDF)
- Alert acknowledge/resolve workflow (no mutation endpoint yet) and resolved-history tab
- Server pagination for the positions blotter; deal-level cash-flow schedule drill
- WebSocket push (30s polling today) · mobile layouts (desktop-first, responsive-safe)
- Next 15/React 19 upgrade · i18n
